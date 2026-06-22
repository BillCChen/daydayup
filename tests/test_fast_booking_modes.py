import threading
import time
import unittest
from argparse import Namespace

import enhanced_book_smart_v2 as smart
import web_console


def make_args(**overrides):
    values = {
        "token": "token",
        "jsessionid": "session",
        "card_index": "card-1",
        "date": "2026-05-22",
        "in_days": None,
        "time": "17-21",
        "duration": 2,
        "priority": [7, 8, 9, 1, 6],
        "backup": [2, 3, 4, 5, 10, 11, 12],
        "all_court": False,
        "force": True,
        "rounds": 100,
        "second_rounds": 100,
        "step_sleep": 0.03,
        "base_url": "https://example.invalid/easyserpClient",
        "window_seconds": 0.1,
        "poll_interval": 0.05,
        "direct_spec_adjacent_delay": smart.DEFAULT_DIRECT_SPEC_ADJACENT_DELAY,
        "reservation_place_gap": smart.DEFAULT_RESERVATION_PLACE_GAP,
        "reservation_place_fast_retry_gap": smart.DEFAULT_RESERVATION_PLACE_FAST_RETRY_GAP,
        "booking_mode": smart.BOOKING_MODE_BALANCED,
        "guide_interval": 0.01,
        "guide_max_inflight": 4,
        "error_backoff": 0.25,
        "dry_run": True,
        "check_session": False,
        "timeout": 0.01,
    }
    values.update(overrides)
    return Namespace(**values)


class FastBookingModeTest(unittest.TestCase):
    def test_booking_command_passes_mode_and_guide_defaults(self):
        command, label = web_console.build_booking_command(
            {
                "date": "2026-05-22",
                "time": "17-21",
                "duration": "2",
                "booking_mode": "guided-fast",
                "poll_interval": "0.05",
            }
        )

        self.assertIn("--booking-mode", command)
        self.assertEqual(command[command.index("--booking-mode") + 1], "guided-fast")
        self.assertEqual(command[command.index("--guide-interval") + 1], "0.5")
        self.assertEqual(command[command.index("--guide-max-inflight") + 1], "4")
        self.assertEqual(command[command.index("--direct-spec-adjacent-delay") + 1], "0.2")
        self.assertEqual(command[command.index("--reservation-place-gap") + 1], "0.85")
        self.assertEqual(command[command.index("--reservation-place-fast-retry-gap") + 1], "1.35")
        self.assertIn("mode=guided-fast", label)

    def test_booking_command_passes_direct_spec_adjacent_delay(self):
        command, _label = web_console.build_booking_command(
            {
                "date": "2026-05-22",
                "time": "17-21",
                "duration": "2",
                "booking_mode": "direct-fast",
                "direct_spec_adjacent_delay": "0.35",
            }
        )

        self.assertIn("--direct-spec-adjacent-delay", command)
        self.assertEqual(command[command.index("--direct-spec-adjacent-delay") + 1], "0.35")

    def test_booking_command_accepts_reservation_place_gate_tuning(self):
        command, _label = web_console.build_booking_command(
            {
                "date": "2026-05-22",
                "time": "17-21",
                "duration": "2",
                "booking_mode": "direct-fast",
                "reservation_place_gap": "0.72",
                "reservation_place_fast_retry_gap": "1.45",
            }
        )

        self.assertEqual(command[command.index("--reservation-place-gap") + 1], "0.72")
        self.assertEqual(command[command.index("--reservation-place-fast-retry-gap") + 1], "1.45")

    def test_booking_command_rejects_invalid_mode(self):
        with self.assertRaises(web_console.EasySerpError):
            web_console.build_booking_command({"booking_mode": "fastest"})

    def test_reservation_place_gate_waits_after_response(self):
        gate = smart.ReservationPlaceGate(0.04, 0.08)
        first = {"hour": 18, "court_id": "ymq7"}
        second = {"hour": 19, "court_id": "ymq7"}

        self.assertTrue(gate.wait_for_turn(first, "first"))
        gate.record_response(first, "first", "failed")
        started_at = time.monotonic()

        self.assertTrue(gate.wait_for_turn(second, "second"))

        self.assertGreaterEqual(time.monotonic() - started_at, 0.03)

    def test_reservation_place_gate_allows_one_active_submitter(self):
        gate = smart.ReservationPlaceGate(0, 0)
        first = {"hour": 18, "court_id": "ymq7"}
        second = {"hour": 19, "court_id": "ymq7"}
        second_allowed = threading.Event()

        self.assertTrue(gate.wait_for_turn(first, "first"))
        thread = threading.Thread(
            target=lambda: second_allowed.set() if gate.wait_for_turn(second, "second") else None
        )
        thread.start()
        time.sleep(0.03)

        self.assertFalse(second_allowed.is_set())
        gate.record_response(first, "first", "failed")
        thread.join(timeout=0.3)
        self.assertFalse(thread.is_alive())
        self.assertTrue(second_allowed.is_set())
        gate.record_response(second, "second", "failed")

    def test_reservation_place_gate_uses_fast_retry_gap(self):
        gate = smart.ReservationPlaceGate(0.01, 0.05)
        first = {"hour": 18, "court_id": "ymq7"}

        self.assertTrue(gate.wait_for_turn(first, "first"))
        gate.record_response(first, "first", "fast_retry", fast_retry=True)
        started_at = time.monotonic()

        self.assertTrue(gate.wait_for_turn(first, "first", retry=True))

        self.assertGreaterEqual(time.monotonic() - started_at, 0.04)
        gate.record_response(first, "first", "failed")

    def test_reservation_place_gate_prioritizes_fast_retry_owner(self):
        gate = smart.ReservationPlaceGate(0.01, 0.03)
        first = {"hour": 18, "court_id": "ymq7"}
        second = {"hour": 19, "court_id": "ymq7"}
        second_allowed = threading.Event()

        self.assertTrue(gate.wait_for_turn(first, "first"))
        gate.record_response(first, "first", "fast_retry", fast_retry=True)
        thread = threading.Thread(
            target=lambda: second_allowed.set() if gate.wait_for_turn(second, "second") else None
        )
        thread.start()
        time.sleep(0.05)

        self.assertFalse(second_allowed.is_set())
        self.assertTrue(gate.wait_for_turn(first, "first", retry=True))
        gate.record_response(first, "first", "failed")
        thread.join(timeout=0.3)
        self.assertFalse(thread.is_alive())
        self.assertTrue(second_allowed.is_set())
        gate.record_response(second, "second", "failed")

    def test_reservation_place_gate_skips_non_pair_candidates_after_success(self):
        gate = smart.ReservationPlaceGate(0, 0)
        first = {"hour": 18, "court_id": "ymq7"}
        same_hour = {"hour": 18, "court_id": "ymq8"}
        non_adjacent = {"hour": 20, "court_id": "ymq7"}
        adjacent = {"hour": 19, "court_id": "ymq7"}
        late_candidate = {"hour": 17, "court_id": "ymq7"}

        gate.record_response(first, "first", "success")

        self.assertFalse(gate.wait_for_turn(same_hour, "same_hour"))
        self.assertFalse(gate.wait_for_turn(non_adjacent, "non_adjacent"))
        self.assertTrue(gate.wait_for_turn(adjacent, "adjacent"))
        gate.record_response(adjacent, "adjacent", "success")
        self.assertFalse(gate.wait_for_turn(late_candidate, "late"))

    def test_direct_candidates_follow_court_pool_without_wall_courts(self):
        bot = smart.SmartBookingBotV2(make_args())
        candidates = bot.generate_direct_first_candidates()
        first_courts = [item["court_id"] for item in candidates[:4]]
        all_courts = {item["court_id"] for item in candidates}

        self.assertEqual(first_courts, ["ymq7", "ymq7", "ymq8", "ymq8"])
        self.assertNotIn("ymq4", all_courts)
        self.assertNotIn("ymq5", all_courts)
        self.assertNotIn("ymq12", all_courts)
        self.assertEqual([item["hour"] for item in candidates[:4]], [18, 19, 18, 19])

    def test_balanced_first_candidates_prefer_middle_hour_in_wide_window(self):
        bot = smart.SmartBookingBotV2(make_args(time="18-21", priority=[7], backup=[7]))
        hour_table = {
            "ymq7": {
                "fullname": "羽毛球7",
                "slots": {
                    18: {"state": 1, "starttime": "18:00", "endtime": "19:00"},
                    19: {"state": 1, "starttime": "19:00", "endtime": "20:00"},
                    20: {"state": 1, "starttime": "20:00", "endtime": "21:00"},
                },
                "states": {18: 1, 19: 1, 20: 1},
            }
        }
        candidates = bot.generate_first_candidates(hour_table)

        self.assertEqual([item["hour"] for item in candidates], [19, 18, 20])

    def test_guided_sort_keeps_middle_first_when_snapshot_states_match(self):
        bot = smart.SmartBookingBotV2(make_args(time="18-21", priority=[7], backup=[7]))
        state = smart.GuidedBookingState(bot.court_rank)
        candidates = bot.generate_direct_first_candidates()
        state.update_snapshot(
            {"ymq7": {"states": {18: 1, 19: 1, 20: 1}}},
            [18, 19, 20],
            ["ymq7"],
        )

        self.assertEqual([item["hour"] for item in state.sort_candidates(candidates)], [19, 18, 20])

    def test_direct_second_candidates_only_use_adjacent_hours(self):
        bot = smart.SmartBookingBotV2(make_args())
        candidates = bot.generate_direct_second_candidates(18)

        self.assertEqual({item["hour"] for item in candidates}, {17, 19})
        self.assertEqual(candidates[0]["court_id"], "ymq7")

    def test_direct_speculative_adjacent_candidates_use_three_priority_courts_per_neighbor(self):
        bot = smart.SmartBookingBotV2(
            make_args(time="18-21", priority=[7, 8, 9, 1], backup=[2, 3])
        )
        first_candidates = bot.generate_direct_first_candidates()
        center = first_candidates[0]
        center_candidates = bot.generate_direct_speculative_center_candidates(first_candidates, center["hour"])
        adjacent_candidates = bot.generate_direct_speculative_adjacent_candidates(center["hour"])

        self.assertEqual(center["hour"], 19)
        self.assertEqual(
            [(item["hour"], item["court_id"]) for item in center_candidates],
            [
                (19, "ymq7"),
                (19, "ymq8"),
                (19, "ymq9"),
            ],
        )
        self.assertEqual(
            [(item["hour"], item["court_id"]) for item in adjacent_candidates],
            [
                (18, "ymq7"),
                (18, "ymq8"),
                (18, "ymq9"),
                (20, "ymq7"),
                (20, "ymq8"),
                (20, "ymq9"),
            ],
        )

    def test_direct_speculative_mode_starts_adjacent_before_center_returns(self):
        bot = smart.SmartBookingBotV2(
            make_args(
                time="18-21",
                booking_mode=smart.BOOKING_MODE_DIRECT_FAST,
                dry_run=False,
                step_sleep=0,
                direct_spec_adjacent_delay=0.03,
            )
        )
        center_started = threading.Event()
        center_released = threading.Event()
        center_finished = threading.Event()
        adjacent_started_before_center_finished = threading.Event()
        first_center_start = []
        first_adjacent_start = []
        calls = []
        calls_lock = threading.Lock()

        def fake_attempt(candidate, label, round_index, candidate_index, candidate_total, client=None, failure_stats=None):
            now = time.monotonic()
            with calls_lock:
                calls.append((label, candidate["hour"]))
            if label.startswith("direct_spec_center"):
                if not first_center_start:
                    first_center_start.append(now)
                center_started.set()
                center_released.wait(1.0)
                center_finished.set()
                return "success"
            if not first_adjacent_start:
                first_adjacent_start.append(now)
            if center_started.is_set() and not center_finished.is_set():
                adjacent_started_before_center_finished.set()
            return "business_fail"

        bot.attempt_single_hour_booking = fake_attempt
        bot.run_direct_second_stage = lambda guide_state=None: "failed"
        results = []
        thread = threading.Thread(target=lambda: results.append(bot.run_direct_mode()))
        thread.start()

        self.assertTrue(center_started.wait(0.2))
        self.assertTrue(adjacent_started_before_center_finished.wait(0.4))
        self.assertFalse(center_finished.is_set())
        center_released.set()
        thread.join(timeout=1.0)

        self.assertFalse(thread.is_alive())
        self.assertTrue(first_center_start)
        self.assertTrue(first_adjacent_start)
        self.assertGreaterEqual(first_adjacent_start[0] - first_center_start[0], 0.02)
        self.assertTrue(any(label.startswith("direct_spec_center") and hour == 19 for label, hour in calls))
        self.assertTrue(any(label.startswith("direct_spec_") and hour in (18, 20) for label, hour in calls))
        self.assertEqual(results, ["failed"])

    def test_direct_speculative_mode_attempts_three_center_courts_initially(self):
        bot = smart.SmartBookingBotV2(
            make_args(
                time="18-21",
                booking_mode=smart.BOOKING_MODE_DIRECT_FAST,
                dry_run=False,
                step_sleep=0,
            )
        )
        calls = []

        def fake_attempt(candidate, label, round_index, candidate_index, candidate_total, client=None, failure_stats=None):
            calls.append((label, candidate["hour"], candidate["court_id"]))
            return "business_fail"

        bot.attempt_single_hour_booking = fake_attempt

        self.assertEqual(bot.run_direct_mode(), "failed")
        self.assertEqual(
            [(hour, court_id) for label, hour, court_id in calls if label.startswith("direct_spec_center")],
            [
                (19, "ymq7"),
                (19, "ymq8"),
                (19, "ymq9"),
            ],
        )

    def test_direct_speculative_mode_uses_adjacent_success_as_anchor_when_center_fails(self):
        bot = smart.SmartBookingBotV2(
            make_args(
                time="18-21",
                booking_mode=smart.BOOKING_MODE_DIRECT_FAST,
                dry_run=False,
                step_sleep=0,
            )
        )
        second_stage_anchors = []

        def fake_attempt(candidate, label, round_index, candidate_index, candidate_total, client=None, failure_stats=None):
            if label.startswith("direct_spec_left") and candidate["hour"] == 18 and candidate["court_id"] == "ymq7":
                return "success"
            return "business_fail"

        def fake_second_stage(guide_state=None):
            second_stage_anchors.append(bot.first_booking)
            return "success"

        bot.attempt_single_hour_booking = fake_attempt
        bot.run_direct_second_stage = fake_second_stage

        self.assertEqual(bot.run_direct_mode(), "success")
        self.assertEqual(bot.first_booking["hour"], 18)
        self.assertEqual(bot.first_booking["court_id"], "ymq7")
        self.assertEqual([item["hour"] for item in second_stage_anchors], [18])

    def test_direct_speculative_mode_finishes_when_initial_batch_has_contiguous_pair(self):
        bot = smart.SmartBookingBotV2(
            make_args(
                time="18-21",
                booking_mode=smart.BOOKING_MODE_DIRECT_FAST,
                dry_run=False,
                step_sleep=0,
            )
        )
        second_stage_called = threading.Event()

        def fake_attempt(candidate, label, round_index, candidate_index, candidate_total, client=None, failure_stats=None):
            if label.startswith("direct_spec_center"):
                return "success"
            if label.startswith("direct_spec_left") and candidate["hour"] == 18 and candidate["court_id"] == "ymq7":
                return "success"
            return "business_fail"

        def fake_second_stage(guide_state=None):
            second_stage_called.set()
            return "failed"

        bot.attempt_single_hour_booking = fake_attempt
        bot.run_direct_second_stage = fake_second_stage

        self.assertEqual(bot.run_direct_mode(), "success")
        self.assertFalse(second_stage_called.is_set())
        self.assertEqual([bot.first_booking["hour"], bot.second_booking["hour"]], [18, 19])

    def test_direct_speculative_dry_run_only_attempts_center_candidate(self):
        bot = smart.SmartBookingBotV2(
            make_args(
                time="18-21",
                booking_mode=smart.BOOKING_MODE_DIRECT_FAST,
                dry_run=True,
            )
        )
        calls = []

        def fake_attempt(candidate, label, round_index, candidate_index, candidate_total, client=None, failure_stats=None):
            calls.append((label, candidate["hour"]))
            return "dry_run"

        bot.attempt_single_hour_booking = fake_attempt

        self.assertEqual(bot.run_direct_mode(), "dry_run")
        self.assertEqual(calls, [("direct_spec_center", 19)])

    def test_reservation_place_fast_retry_keeps_same_candidate(self):
        bot = smart.SmartBookingBotV2(
            make_args(
                booking_mode=smart.BOOKING_MODE_DIRECT_FAST,
                dry_run=False,
                step_sleep=0,
            )
        )
        candidate = bot._synthetic_candidate(19, "ymq7")
        sleeps = []

        class FakeClient:
            def __init__(self):
                self.reservation_attempts = 0

            def request(self, method, endpoint, *, params=None, data=None, timeout=None, label=""):
                if endpoint == "/place/reservationPlace":
                    self.reservation_attempts += 1
                    if self.reservation_attempts == 1:
                        return smart.HttpResult(
                            status=200,
                            text="",
                            elapsed=0.01,
                            json_data={"msg": "fail", "data": "操作过快,请稍后重试。"},
                        )
                return smart.HttpResult(
                    status=200,
                    text="",
                    elapsed=0.01,
                    json_data={"msg": "success", "data": ""},
                )

        fake_client = FakeClient()
        bot._sleep_after_fast_retry = lambda: sleeps.append(0.8)

        result = bot.attempt_single_hour_booking(
            candidate,
            "direct_second",
            1,
            1,
            1,
            client=fake_client,
        )

        self.assertEqual(result, "success")
        self.assertEqual(fake_client.reservation_attempts, 2)
        self.assertEqual(sleeps, [0.8])

    def test_guided_sort_uses_snapshot_and_failures(self):
        state = smart.GuidedBookingState({"ymq7": 0, "ymq8": 1})
        base = [
            {"court_id": "ymq7", "hour": 17},
            {"court_id": "ymq8", "hour": 17},
        ]

        self.assertEqual(state.sort_candidates(base), base)
        state.update_snapshot(
            {
                "ymq7": {"states": {17: 2}},
                "ymq8": {"states": {17: 1}},
            },
            [17],
            ["ymq7", "ymq8"],
        )
        self.assertEqual(state.sort_candidates(base)[0]["court_id"], "ymq8")

        failure_state = smart.GuidedBookingState({"ymq7": 0, "ymq8": 1})
        failure_state.record_attempt_result(base[0], "business_fail")
        self.assertEqual(failure_state.sort_candidates(base)[0]["court_id"], "ymq8")

    def test_guided_collector_does_not_block_on_slow_probe(self):
        bot = smart.SmartBookingBotV2(make_args(guide_interval=0.01, guide_max_inflight=4))
        release = threading.Event()
        started = []

        def blocking_probe(_state, probe_index):
            started.append(probe_index)
            release.wait(0.2)

        bot._guided_probe_worker = blocking_probe
        state = smart.GuidedBookingState(bot.court_rank)
        stop_event = threading.Event()
        thread = threading.Thread(
            target=bot._guided_collector_loop,
            args=(state, time.monotonic() + 0.08, stop_event),
        )
        thread.start()
        thread.join(timeout=0.5)
        release.set()

        self.assertFalse(thread.is_alive())
        self.assertEqual(len(started), 4)
        self.assertGreater(bot.fail_stats["guided_probe_skipped_inflight"], 0)


if __name__ == "__main__":
    unittest.main()
