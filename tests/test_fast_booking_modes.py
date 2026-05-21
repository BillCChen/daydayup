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
        self.assertIn("mode=guided-fast", label)

    def test_booking_command_rejects_invalid_mode(self):
        with self.assertRaises(web_console.EasySerpError):
            web_console.build_booking_command({"booking_mode": "fastest"})

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
