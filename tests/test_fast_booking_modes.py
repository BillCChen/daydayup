import logging
import threading
import time
import unittest
from argparse import Namespace
from collections import Counter

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
        "direct_max_inflight": smart.DEFAULT_DIRECT_MAX_INFLIGHT,
        "direct_max_attempts": smart.DEFAULT_DIRECT_MAX_ATTEMPTS,
        "reservation_place_gap": smart.DEFAULT_RESERVATION_PLACE_GAP,
        "reservation_place_fast_retry_gap": smart.DEFAULT_RESERVATION_PLACE_FAST_RETRY_GAP,
        "reservation_place_timeout": smart.DEFAULT_RESERVATION_PLACE_TIMEOUT,
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
        self.assertEqual(command[command.index("--direct-spec-adjacent-delay") + 1], "0")
        self.assertEqual(command[command.index("--direct-max-inflight") + 1], "3")
        self.assertEqual(command[command.index("--direct-max-attempts") + 1], "2")
        self.assertEqual(command[command.index("--reservation-place-gap") + 1], "0.35")
        self.assertEqual(command[command.index("--reservation-place-fast-retry-gap") + 1], "1.2")
        self.assertEqual(command[command.index("--reservation-place-timeout") + 1], "2.5")
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

    def test_reservation_place_gate_adapts_repeated_fast_retry_gap(self):
        gate = smart.ReservationPlaceGate(0.01, 0.05, max_fast_retry_gap_seconds=0.1)
        candidate = {"hour": 18, "court_id": "ymq7"}

        gate.record_response(candidate, "first", "fast_retry", fast_retry=True, defer_retry=True)
        first_gap = gate.last_cooldown_seconds
        gate.record_response(candidate, "second", "fast_retry", fast_retry=True, defer_retry=True)
        second_gap = gate.last_cooldown_seconds
        gate.record_response(candidate, "third", "fast_retry", fast_retry=True, defer_retry=True)

        self.assertAlmostEqual(first_gap, 0.075)
        self.assertAlmostEqual(second_gap, 0.1)
        self.assertAlmostEqual(gate.last_cooldown_seconds, 0.1)
        self.assertEqual(gate.fast_retry_streak, 3)
        self.assertEqual(gate.last_cooldown_reason, "too_fast")

    def test_reservation_place_gate_uses_fast_gap_after_success(self):
        gate = smart.ReservationPlaceGate(0.01, 0.05)
        candidate = {"hour": 18, "court_id": "ymq7"}

        gate.record_response(candidate, "first", "success")

        self.assertAlmostEqual(gate.last_cooldown_seconds, 0.05)
        self.assertEqual(gate.fast_retry_streak, 0)
        self.assertEqual(gate.last_cooldown_reason, "success")

    def test_reservation_place_gate_uses_production_success_gap(self):
        gate = smart.ReservationPlaceGate(
            0.35,
            1.2,
            success_gap_seconds=smart.DEFAULT_RESERVATION_PLACE_SUCCESS_GAP,
        )
        candidate = {"hour": 18, "court_id": "ymq7"}

        gate.record_response(candidate, "first", "success")

        self.assertAlmostEqual(gate.last_cooldown_seconds, 1.8)
        self.assertEqual(gate.last_cooldown_reason, "success")

    def test_reservation_place_gate_skips_submit_without_deadline_budget(self):
        gate = smart.ReservationPlaceGate(0, 0)
        candidate = {"hour": 18, "court_id": "ymq7"}

        allowed = gate.wait_for_turn(
            candidate,
            "late",
            deadline=time.monotonic() + 0.01,
            min_remaining_seconds=0.05,
        )

        self.assertFalse(allowed)
        self.assertIsNone(gate.active_label)

    def test_reservation_place_gate_deferred_fast_retry_has_no_owner(self):
        gate = smart.ReservationPlaceGate(0, 0.01)
        first = {"hour": 18, "court_id": "ymq7"}

        gate.record_response(first, "first", "fast_retry", fast_retry=True, defer_retry=True)

        self.assertIsNone(gate.retry_owner_key)
        self.assertGreater(gate.next_allowed_at, time.monotonic())

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

    def test_reservation_place_gate_stops_single_hour_after_success(self):
        gate = smart.ReservationPlaceGate(0, 0, required_hours=1)
        first = {"hour": 18, "court_id": "ymq7"}
        second = {"hour": 19, "court_id": "ymq8"}

        gate.record_response(first, "first", "success")

        self.assertEqual(gate.skip_reason(second), "single_hour_complete")
        self.assertFalse(gate.wait_for_turn(second, "second"))

    def test_reservation_place_gate_stops_single_hour_after_unknown_outcome(self):
        gate = smart.ReservationPlaceGate(0, 0, required_hours=1)
        first = {"hour": 18, "court_id": "ymq7"}
        second = {"hour": 19, "court_id": "ymq8"}

        gate.record_response(first, "first", "unknown_outcome")

        self.assertEqual(gate.unknown_candidates(), [first])
        self.assertEqual(gate.skip_reason(second), "single_hour_unknown")
        self.assertFalse(gate.wait_for_turn(second, "second"))

    def test_reservation_place_gate_allows_only_adjacent_hour_after_unknown(self):
        gate = smart.ReservationPlaceGate(0, 0, required_hours=2)
        unknown = {"hour": 18, "court_id": "ymq7"}
        adjacent = {"hour": 19, "court_id": "ymq8"}
        non_adjacent = {"hour": 20, "court_id": "ymq9"}

        gate.record_response(unknown, "unknown", "unknown_outcome")

        self.assertEqual(gate.skip_reason(adjacent), "")
        self.assertEqual(gate.skip_reason(non_adjacent), "not_adjacent_to_committed_or_unknown")
        gate.record_response(adjacent, "adjacent", "success")
        self.assertTrue(gate.goal_saturated())
        self.assertEqual(gate.skip_reason(non_adjacent), "contiguous_pair_unknown")

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

    def test_direct_wave_prioritizes_distinct_hours_and_covers_full_pool(self):
        bot = smart.SmartBookingBotV2(
            make_args(
                time="18-21",
                booking_mode=smart.BOOKING_MODE_DIRECT_FAST,
                dry_run=False,
                step_sleep=0,
                window_seconds=1,
                poll_interval=0.001,
            )
        )
        calls = []

        def fake_attempt(candidate, label, round_index, candidate_index, candidate_total, client=None, failure_stats=None):
            calls.append((label, candidate["hour"], candidate["court_id"]))
            return "business_fail"

        bot.attempt_single_hour_booking = fake_attempt

        self.assertEqual(bot.run_direct_mode(), "failed")
        self.assertEqual({hour for _label, hour, _court_id in calls[:3]}, {18, 19, 20})
        self.assertEqual(len(calls), len(bot.generate_direct_first_candidates()))
        self.assertIn(("direct_spec_center_19_ymq11_w9_a1", 19, "ymq11"), calls)

    def test_single_hour_direct_wave_stops_after_first_success(self):
        bot = smart.SmartBookingBotV2(
            make_args(
                time="18-21",
                duration=1,
                booking_mode=smart.BOOKING_MODE_DIRECT_FAST,
                dry_run=False,
                step_sleep=0,
            )
        )
        calls = []

        def fake_attempt(candidate, label, round_index, candidate_index, candidate_total, client=None, failure_stats=None):
            calls.append((candidate["hour"], candidate["court_id"]))
            if candidate["hour"] == 19 and candidate["court_id"] == "ymq7":
                return "success"
            return "business_fail"

        bot.attempt_single_hour_booking = fake_attempt

        self.assertEqual(bot.run_direct_mode(), "success")
        self.assertEqual(bot.first_booking["hour"], 19)
        self.assertEqual(bot.first_booking["court_id"], "ymq7")
        self.assertLessEqual(len(calls), bot.args.direct_max_inflight)

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

            def request(
                self,
                method,
                endpoint,
                *,
                params=None,
                data=None,
                timeout=None,
                label="",
                retry_transport=True,
            ):
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

    def test_keep_alive_client_does_not_replay_non_idempotent_request(self):
        logger = logging.getLogger("test_no_reservation_replay")
        logger.handlers = [logging.NullHandler()]
        client = smart.KeepAliveClient(
            "https://example.invalid/easyserpClient",
            {},
            timeout=5,
            logger=logger,
            fail_stats=Counter(),
        )
        attempts = []

        def fail_once(*_args, **_kwargs):
            attempts.append(1)
            raise TimeoutError("timed out")

        client._send_once = fail_once
        result = client.request(
            "POST",
            "/place/reservationPlace",
            retry_transport=False,
            label="reservationPlace",
        )

        self.assertEqual(len(attempts), 1)
        self.assertEqual(result.status, 0)
        self.assertEqual(result.error_kind, "timeout")

    def test_keep_alive_client_can_retry_prerequisite_transport_failure(self):
        logger = logging.getLogger("test_prerequisite_retry")
        logger.handlers = [logging.NullHandler()]
        client = smart.KeepAliveClient(
            "https://example.invalid/easyserpClient",
            {},
            timeout=5,
            logger=logger,
            fail_stats=Counter(),
        )
        attempts = []

        def fail_twice(*_args, **_kwargs):
            attempts.append(1)
            raise TimeoutError("timed out")

        client._send_once = fail_twice
        result = client.request("POST", "/place/canBook", label="canBook")

        self.assertEqual(len(attempts), 2)
        self.assertEqual(result.error_kind, "timeout")

    def test_keep_alive_client_does_not_retry_after_timeout_budget_is_consumed(self):
        logger = logging.getLogger("test_consumed_timeout_budget")
        logger.handlers = [logging.NullHandler()]
        client = smart.KeepAliveClient(
            "https://example.invalid/easyserpClient",
            {},
            timeout=0.01,
            logger=logger,
            fail_stats=Counter(),
        )
        attempts = []

        def consume_timeout(*_args, **_kwargs):
            attempts.append(1)
            time.sleep(0.02)
            raise TimeoutError("timed out")

        client._send_once = consume_timeout
        result = client.request("POST", "/place/canBook", label="canBook")

        self.assertEqual(len(attempts), 1)
        self.assertEqual(result.error_kind, "timeout")

    def test_keep_alive_client_resets_default_timeout_after_override(self):
        logger = logging.getLogger("test_timeout_reset")
        logger.handlers = [logging.NullHandler()]
        client = smart.KeepAliveClient(
            "https://example.invalid/easyserpClient",
            {},
            timeout=5,
            logger=logger,
            fail_stats=Counter(),
        )
        observed_timeouts = []

        def capture_timeout(_method, _path, _body, _headers, _label, _start, timeout):
            observed_timeouts.append(timeout)
            return smart.HttpResult(200, "", 0.01, json_data={"msg": "success", "data": ""})

        client._send_once = capture_timeout
        client.request("GET", "/first", timeout=1.25)
        client.request("GET", "/second")

        self.assertEqual(observed_timeouts, [1.25, 5])

    def test_keep_alive_client_applies_request_timeout_to_open_connection(self):
        class FakeSocket:
            def __init__(self):
                self.timeout = None

            def settimeout(self, value):
                self.timeout = value

        class FakeConnection:
            def __init__(self):
                self.timeout = 5
                self.sock = FakeSocket()

        logger = logging.getLogger("test_request_timeout")
        logger.handlers = [logging.NullHandler()]
        client = smart.KeepAliveClient(
            "https://example.invalid/easyserpClient",
            {},
            timeout=5,
            logger=logger,
            fail_stats=Counter(),
        )
        client.conn = FakeConnection()

        client._apply_timeout(1.25)

        self.assertEqual(client.conn.timeout, 1.25)
        self.assertEqual(client.conn.sock.timeout, 1.25)

    def test_reconciliation_http_metric_is_not_counted_as_reservation_submit(self):
        bot = smart.SmartBookingBotV2(make_args())

        bot._record_http_event(
            label="direct_reservationPlace_reconcile_getPlaceOrder",
            method="GET",
            status=200,
            elapsed=0.05,
            response_bytes=10,
            outcome="success",
        )

        self.assertEqual(bot.http_metrics["getPlaceOrder"], [0.05])
        self.assertNotIn("reservationPlace", bot.http_metrics)

    def test_reservation_timeout_reconciliation_confirms_exact_order(self):
        bot = smart.SmartBookingBotV2(
            make_args(booking_mode=smart.BOOKING_MODE_DIRECT_FAST, dry_run=False, step_sleep=0)
        )
        candidate = bot._synthetic_candidate(19, "ymq7")

        class FakeClient:
            def __init__(self):
                self.reservation_attempts = 0
                self.order_queries = 0
                self.order_params = []

            def request(self, method, endpoint, **kwargs):
                if endpoint == "/place/reservationPlace":
                    self.reservation_attempts += 1
                    self.reservation_kwargs = kwargs
                    return smart.HttpResult(0, "", 2.5, error_kind="timeout")
                if endpoint == "/place/getPlaceOrder":
                    self.order_queries += 1
                    self.order_params.append(kwargs.get("params", {}))
                    return smart.HttpResult(
                        200,
                        "",
                        0.05,
                        json_data={
                            "msg": "success",
                            "data": [
                                {
                                    "readydate": bot.target_date,
                                    "readystarttime": "19:00:00",
                                    "readyendtime": "20:00:00",
                                    "stagenum": "羽毛球7",
                                    "prestatus": "等待",
                                }
                            ],
                        },
                    )
                return smart.HttpResult(200, "", 0.01, json_data={"msg": "success", "data": ""})

        fake_client = FakeClient()
        bot._sleep_before_reconciliation = lambda _delay: True

        result = bot.attempt_single_hour_booking(candidate, "direct", 1, 1, 1, client=fake_client)

        self.assertEqual(result, "success")
        self.assertEqual(fake_client.reservation_attempts, 1)
        self.assertEqual(fake_client.order_queries, 1)
        self.assertNotIn("startTime", fake_client.order_params[0])
        self.assertNotIn("endTime", fake_client.order_params[0])
        self.assertFalse(fake_client.reservation_kwargs["retry_transport"])
        self.assertEqual(fake_client.reservation_kwargs["timeout"], 2.5)

    def test_reservation_timeout_with_stable_absence_continues_without_replay(self):
        bot = smart.SmartBookingBotV2(
            make_args(booking_mode=smart.BOOKING_MODE_DIRECT_FAST, dry_run=False, step_sleep=0)
        )
        candidate = bot._synthetic_candidate(19, "ymq7")
        bot.reservation_place_gate = smart.ReservationPlaceGate(0, 0, required_hours=1)

        class FakeClient:
            def __init__(self):
                self.reservation_attempts = 0
                self.order_queries = 0

            def request(self, method, endpoint, **_kwargs):
                if endpoint == "/place/reservationPlace":
                    self.reservation_attempts += 1
                    return smart.HttpResult(0, "", 2.5, error_kind="timeout")
                if endpoint == "/place/getPlaceOrder":
                    self.order_queries += 1
                    return smart.HttpResult(200, "", 0.05, json_data={"msg": "success", "data": []})
                return smart.HttpResult(200, "", 0.01, json_data={"msg": "success", "data": ""})

        fake_client = FakeClient()
        bot._sleep_before_reconciliation = lambda _delay: True

        result = bot.attempt_single_hour_booking(candidate, "direct", 1, 1, 1, client=fake_client)

        self.assertEqual(result, "reservation_not_confirmed")
        self.assertEqual(fake_client.reservation_attempts, 1)
        self.assertEqual(fake_client.order_queries, 3)
        self.assertEqual(bot.reservation_place_gate.unknown_candidates(), [])

    def test_reservation_timeout_query_failure_stays_unknown(self):
        bot = smart.SmartBookingBotV2(
            make_args(booking_mode=smart.BOOKING_MODE_DIRECT_FAST, dry_run=False, step_sleep=0)
        )
        candidate = bot._synthetic_candidate(19, "ymq7")
        bot.reservation_place_gate = smart.ReservationPlaceGate(0, 0, required_hours=1)

        class FakeClient:
            def __init__(self):
                self.order_queries = 0

            def request(self, method, endpoint, **_kwargs):
                if endpoint == "/place/reservationPlace":
                    return smart.HttpResult(0, "", 2.5, error_kind="timeout")
                if endpoint == "/place/getPlaceOrder":
                    self.order_queries += 1
                    if self.order_queries == 1:
                        return smart.HttpResult(
                            200,
                            "",
                            0.05,
                            json_data={"msg": "success", "data": []},
                        )
                    return smart.HttpResult(0, "", 1.5, error_kind="timeout")
                return smart.HttpResult(200, "", 0.01, json_data={"msg": "success", "data": ""})

        fake_client = FakeClient()
        bot._sleep_before_reconciliation = lambda _delay: True

        result = bot.attempt_single_hour_booking(candidate, "direct", 1, 1, 1, client=fake_client)

        self.assertEqual(result, "unknown_outcome")
        self.assertEqual(fake_client.order_queries, 2)
        self.assertEqual(bot.reservation_place_gate.unknown_candidates(), [candidate])
        self.assertEqual(bot.reservation_place_gate.skip_reason(candidate), "single_hour_unknown")

    def test_reservation_timeout_can_be_confirmed_on_third_snapshot(self):
        bot = smart.SmartBookingBotV2(
            make_args(booking_mode=smart.BOOKING_MODE_DIRECT_FAST, dry_run=False, step_sleep=0)
        )
        candidate = bot._synthetic_candidate(19, "ymq7")

        class FakeClient:
            def __init__(self):
                self.order_queries = 0

            def request(self, method, endpoint, **_kwargs):
                if endpoint == "/place/reservationPlace":
                    return smart.HttpResult(0, "", 2.5, error_kind="timeout")
                if endpoint == "/place/getPlaceOrder":
                    self.order_queries += 1
                    orders = []
                    if self.order_queries == 3:
                        orders = [
                            {
                                "readydate": bot.target_date,
                                "readystarttime": "19:00:00",
                                "readyendtime": "20:00:00",
                                "stagenum": "羽毛球7",
                                "prestatus": "等待",
                            }
                        ]
                    return smart.HttpResult(
                        200,
                        "",
                        0.05,
                        json_data={"msg": "success", "data": orders},
                    )
                return smart.HttpResult(200, "", 0.01, json_data={"msg": "success", "data": ""})

        fake_client = FakeClient()
        bot._sleep_before_reconciliation = lambda _delay: True

        result = bot.attempt_single_hour_booking(candidate, "direct", 1, 1, 1, client=fake_client)

        self.assertEqual(result, "success")
        self.assertEqual(fake_client.order_queries, 3)

    def test_direct_scheduler_continues_after_stable_absence(self):
        bot = smart.SmartBookingBotV2(
            make_args(
                time="18-21",
                booking_mode=smart.BOOKING_MODE_DIRECT_FAST,
                dry_run=False,
                step_sleep=0,
                poll_interval=0.001,
                window_seconds=0.2,
            )
        )
        calls = Counter()

        def fake_attempt(candidate, label, round_index, candidate_index, candidate_total, client=None, failure_stats=None):
            key = (candidate["hour"], candidate["court_id"])
            calls[key] += 1
            if key == (19, "ymq7"):
                return "reservation_not_confirmed"
            if key in ((18, "ymq7"), (19, "ymq8")):
                return "success"
            return "business_fail"

        bot.attempt_single_hour_booking = fake_attempt

        self.assertEqual(bot.run_direct_mode(), "success")
        self.assertEqual(calls[(19, "ymq7")], 1)
        self.assertGreaterEqual(calls[(19, "ymq8")], 1)
        self.assertEqual([bot.first_booking["hour"], bot.second_booking["hour"]], [18, 19])

    def test_response_log_summary_does_not_render_structured_failure_data(self):
        outcome, message, shape = smart.response_log_summary(
            200,
            {"msg": "fail", "data": {"token": "secret-token", "member": "private-name"}},
        )

        self.assertEqual(outcome, "business_error")
        self.assertEqual(message, "fail")
        self.assertEqual(shape, "dict:2")
        self.assertNotIn("secret-token", message)

    def test_booking_failure_data_does_not_render_structured_payload(self):
        result = smart.HttpResult(
            status=200,
            text="",
            elapsed=0.01,
            json_data={"msg": "fail", "data": {"token": "secret-token", "member": "private-name"}},
        )

        self.assertEqual(smart.SmartBookingBotV2._failure_data(result), "fail")

    def test_redact_text_covers_query_cookie_and_json_credentials(self):
        redacted = smart.redact_text(
            'token=abc&JSESSIONID=session {"cardIndex":"card-1","offerId":"offer-1"}'
        )

        self.assertNotIn("abc", redacted)
        self.assertNotIn("session", redacted)
        self.assertNotIn("card-1", redacted)
        self.assertNotIn("offer-1", redacted)

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
