import io
import json
import threading
import time
import unittest
from argparse import Namespace
from collections import Counter
from unittest.mock import patch

import enhanced_book_smart_v2 as smart
from easyserp_client import redact_sensitive_text


def make_contexts(jsessionid=""):
    return [
        smart.BookingAccountContext("pool_1", "user-a", "token-a", jsessionid, "card-a"),
        smart.BookingAccountContext("pool_2", "user-b", "token-b", jsessionid, "card-b"),
    ]


def make_args(contexts=None, **overrides):
    values = {
        "token": "",
        "jsessionid": "",
        "card_index": "",
        "date": "2026-07-19",
        "in_days": None,
        "time": "18-20",
        "duration": 2,
        "priority": [7],
        "backup": [7],
        "all_court": False,
        "force": True,
        "rounds": 1,
        "second_rounds": 1,
        "step_sleep": 0,
        "base_url": "https://example.invalid/easyserpClient",
        "window_seconds": 1.0,
        "poll_interval": 0.001,
        "direct_spec_adjacent_delay": 0,
        "direct_max_inflight": 2,
        "direct_max_attempts": 2,
        "reservation_place_gap": 0,
        "reservation_place_fast_retry_gap": 1.2,
        "reservation_place_timeout": 2.5,
        "booking_mode": smart.BOOKING_MODE_DIRECT_FAST,
        "guide_interval": 0.01,
        "guide_max_inflight": 2,
        "error_backoff": 0.01,
        "dry_run": False,
        "check_session": False,
        "account_pool_stdin": True,
        "account_pool": contexts or make_contexts(),
        "timeout": 0.01,
    }
    values.update(overrides)
    return Namespace(**values)


class FakeClient:
    def __init__(self, mode="success", mode_by_hour=None):
        self.mode = mode
        self.mode_by_hour = dict(mode_by_hour or {})
        self.calls = []
        self.order_query_count = 0
        self.lock = threading.Lock()

    def close(self):
        return None

    @staticmethod
    def _hour(data):
        if not data or "fieldinfo" not in data:
            return None
        fields = json.loads(data["fieldinfo"])
        return int(fields[0]["startTime"].split(":", 1)[0])

    def request(self, method, endpoint, **kwargs):
        hour = self._hour(kwargs.get("data"))
        with self.lock:
            self.calls.append((method, endpoint, kwargs, hour))
        if endpoint == "/place/reservationPlace":
            mode = self.mode_by_hour.get(hour, self.mode)
            if mode == "success":
                return smart.HttpResult(200, "", 0.01, json_data={"msg": "success", "data": ""})
            if mode == "too_fast":
                return smart.HttpResult(
                    200,
                    "",
                    0.01,
                    json_data={"msg": "fail", "data": smart.FAST_RETRY_TEXT},
                )
            if mode in (
                "timeout_empty",
                "timeout_query_failure",
                "timeout_confirm",
                "timeout_confirm_third",
                "timeout_post_confirm",
                "timeout_post_empty",
            ):
                return smart.HttpResult(0, "", 2.5, error_kind="timeout")
            return smart.HttpResult(
                200,
                "",
                0.01,
                json_data={"msg": "fail", "data": smart.TAKEN_RETRY_TEXT},
            )
        if endpoint == "/place/getPlaceOrder":
            self.order_query_count += 1
            mode = self.mode_by_hour.get(self._reconcile_hour(), self.mode)
            if mode == "timeout_query_failure":
                return smart.HttpResult(0, "", 0.01, error_kind="timeout")
            orders = []
            if mode in ("timeout_post_confirm", "timeout_post_empty") and self.order_query_count == 1:
                return smart.HttpResult(0, "", 0.01, error_kind="timeout")
            if mode in ("timeout_confirm", "timeout_post_confirm") or (
                mode == "timeout_confirm_third" and self.order_query_count == 3
            ):
                hour = self._reconcile_hour()
                orders = [
                    {
                        "readydate": "2026-07-19",
                        "readystarttime": f"{hour:02d}:00:00",
                        "readyendtime": f"{hour + 1:02d}:00:00",
                        "stagenum": "羽毛球7",
                        "prestatus": "等待",
                    }
                ]
            return smart.HttpResult(200, "", 0.01, json_data={"msg": "success", "data": orders})
        return smart.HttpResult(200, "", 0.01, json_data={"msg": "success", "data": ""})

    def _reconcile_hour(self):
        with self.lock:
            reservations = [call for call in self.calls if call[1] == "/place/reservationPlace"]
        return reservations[-1][3]


def attach_clients(bot, pool_1, pool_2):
    for context, client in zip(bot.account_pool, (pool_1, pool_2)):
        context.client = client
    bot.client = pool_1


class AccountPoolInputTest(unittest.TestCase):
    def test_loads_two_accounts_and_allows_empty_jsessionid(self):
        stream = io.StringIO(
            json.dumps(
                {
                    "accounts": [
                        {
                            "slot": "pool_2",
                            "user_key": "user-b",
                            "token": "secret-b",
                            "jsessionid": "",
                            "card_index": "card-b",
                        },
                        {
                            "slot": "pool_1",
                            "user_key": "user-a",
                            "token": "secret-a",
                            "card_index": "card-a",
                        },
                    ]
                }
            )
            + "\n"
        )

        contexts = smart.load_booking_account_pool(stream)

        self.assertEqual([context.slot for context in contexts], ["pool_1", "pool_2"])
        self.assertEqual([context.jsessionid for context in contexts], ["", ""])
        self.assertNotIn("secret-a", repr(contexts[0]))
        self.assertNotIn("user-a", repr(contexts[0]))

    def test_rejects_invalid_account_pool_payloads(self):
        base = [
            {"slot": "pool_1", "user_key": "a", "token": "ta", "card_index": "ca"},
            {"slot": "pool_2", "user_key": "b", "token": "tb", "card_index": "cb"},
        ]
        invalid_accounts = [
            base[:1],
            [dict(base[0]), dict(base[1], slot="pool_1")],
            [dict(base[0]), dict(base[1], user_key="a")],
            [dict(base[0], token=""), dict(base[1])],
            [dict(base[0], card_index=""), dict(base[1])],
        ]
        for accounts in invalid_accounts:
            with self.subTest(accounts=accounts):
                with self.assertRaises(ValueError):
                    smart.load_booking_account_pool(
                        io.StringIO(json.dumps({"accounts": accounts}) + "\n")
                    )

    def test_multi_pool_mode_validation_and_legacy_default(self):
        for override in (
            {"duration": 1},
            {"booking_mode": smart.BOOKING_MODE_BALANCED},
            {"check_session": True},
        ):
            with self.subTest(override=override):
                with self.assertRaises(ValueError):
                    smart.SmartBookingBotV2(make_args(**override))

        parsed = smart.build_parser().parse_args(["-t", "18-20"])
        self.assertFalse(parsed.account_pool_stdin)
        self.assertEqual(smart.BOOKING_ENGINE_VERSION, "3.8.1")

    def test_runtime_mode_is_enforced_again_inside_the_engine(self):
        args = make_args()
        with self.assertRaisesRegex(ValueError, "disabled"):
            smart.enforce_multi_pool_runtime_mode(args, {})

        args = make_args(dry_run=False)
        self.assertEqual(
            smart.enforce_multi_pool_runtime_mode(
                args,
                {"DAYDAYUP_MULTI_POOL_MODE": "dry_run"},
            ),
            "dry_run",
        )
        self.assertTrue(args.dry_run)

        args = make_args(dry_run=False)
        self.assertEqual(
            smart.enforce_multi_pool_runtime_mode(
                args,
                {"DAYDAYUP_MULTI_POOL_MODE": "live"},
            ),
            "live",
        )
        self.assertFalse(args.dry_run)


class MultiPoolCoordinatorTest(unittest.TestCase):
    def test_concurrent_acquire_allows_one_lease_per_hour_and_only_adjacent_inflight(self):
        coordinator = smart.MultiPoolCoordinator((18, 19), 1.2)
        results = []
        lock = threading.Lock()

        def acquire(barrier, hour, slot):
            barrier.wait()
            result = coordinator.try_acquire(
                hour,
                slot,
                {"hour": hour, "court_id": "ymq7"},
                now=0,
            )
            with lock:
                results.append((hour, result))

        first_barrier = threading.Barrier(2)
        threads = [
            threading.Thread(target=acquire, args=(first_barrier, 18, "pool_1")),
            threading.Thread(target=acquire, args=(first_barrier, 18, "pool_2")),
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        self.assertEqual(sum(hour == 18 and result == "acquired" for hour, result in results), 1)
        first_owner = coordinator.snapshot()[18]["account_slot"]
        other_slot = "pool_2" if first_owner == "pool_1" else "pool_1"
        self.assertEqual(
            coordinator.try_acquire(
                19,
                first_owner,
                {"hour": 19, "court_id": "ymq7"},
                now=0,
            ),
            "account_slot_in_flight",
        )

        second_barrier = threading.Barrier(2)
        threads = [
            threading.Thread(target=acquire, args=(second_barrier, 19, first_owner)),
            threading.Thread(target=acquire, args=(second_barrier, 19, other_slot)),
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        self.assertEqual(sum(result == "acquired" for _hour, result in results), 2)
        inflight = sorted(
            hour
            for hour, record in coordinator.snapshot().items()
            if record["state"] == "in_flight"
        )
        self.assertEqual(inflight, [18, 19])
        owners = {coordinator.snapshot()[hour]["account_slot"] for hour in inflight}
        self.assertEqual(owners, {"pool_1", "pool_2"})

    def test_lease_and_global_cooldown_events_use_only_pool_slots(self):
        events = []
        coordinator = smart.MultiPoolCoordinator(
            (18, 19),
            1.2,
            event_callback=lambda event, **fields: events.append((event, fields)),
        )
        candidate = {"hour": 18, "court_id": "ymq7"}
        coordinator.try_acquire(18, "pool_1", candidate, now=0)
        coordinator.record(18, "pool_1", "failed", candidate, too_fast=True, now=0)

        event_names = [event for event, _fields in events]
        self.assertIn("multi_pool_lease_acquired", event_names)
        self.assertIn("multi_pool_global_cooldown_set", event_names)
        self.assertIn("multi_pool_lease_recorded", event_names)
        self.assertTrue(
            all(fields.get("account_slot") == "pool_1" for _event, fields in events)
        )
    def test_lease_blocks_same_hour_and_non_adjacent_hour(self):
        coordinator = smart.MultiPoolCoordinator((18, 19), 1.2)
        candidate = {"hour": 18, "court_id": "ymq7"}

        self.assertEqual(coordinator.try_acquire(18, "pool_1", candidate, now=0), "acquired")
        self.assertEqual(coordinator.try_acquire(18, "pool_2", candidate, now=0), "hour_in_flight")
        self.assertEqual(coordinator.try_acquire(20, "pool_2", candidate, now=0), "not_in_target_pair")
        coordinator.record(18, "pool_1", "failed", now=0)
        self.assertEqual(coordinator.try_acquire(18, "pool_2", candidate, now=0), "acquired")

    def test_timeout_tombstone_cannot_be_released_or_taken_over(self):
        coordinator = smart.MultiPoolCoordinator((18, 19), 1.2)
        candidate = {"hour": 18, "court_id": "ymq7"}
        coordinator.try_acquire(18, "pool_1", candidate, now=0)
        coordinator.record(18, "pool_1", "tombstoned", candidate, now=0)

        self.assertEqual(coordinator.try_acquire(18, "pool_2", candidate, now=10), "hour_tombstoned")
        self.assertEqual(coordinator.snapshot()[18]["account_slot"], "pool_1")

    def test_account_pacing_is_independent_and_too_fast_raises_global_cooldown(self):
        contexts = make_contexts()
        for context in contexts:
            context.reservation_gate = smart.ReservationPlaceGate(0, 1.2, required_hours=1)
        candidate = {"hour": 18, "court_id": "ymq7"}
        self.assertTrue(contexts[0].reservation_gate.wait_for_turn(candidate, "pool_1"))
        contexts[0].reservation_gate.record_response(
            candidate,
            "pool_1",
            "fast_retry",
            fast_retry=True,
        )
        self.assertAlmostEqual(contexts[0].reservation_gate.last_cooldown_seconds, 1.8)
        self.assertEqual(contexts[1].reservation_gate.last_cooldown_seconds, 0)

        coordinator = smart.MultiPoolCoordinator((18, 19), 1.2)
        coordinator.try_acquire(18, "pool_1", candidate, now=0)
        coordinator.record(18, "pool_1", "failed", too_fast=True, now=0)
        self.assertEqual(coordinator.try_acquire(19, "pool_2", candidate, now=1), "global_cooldown")
        self.assertEqual(coordinator.try_acquire(19, "pool_2", candidate, now=1.8), "acquired")

    def test_unknown_can_only_be_resolved_by_original_owner(self):
        coordinator = smart.MultiPoolCoordinator((18, 19), 1.2)
        candidate = {"hour": 18, "court_id": "ymq7"}
        coordinator.try_acquire(18, "pool_1", candidate, now=0)
        coordinator.record(18, "pool_1", "unknown", candidate, now=0)

        with self.assertRaisesRegex(RuntimeError, "original account"):
            coordinator.resolve_unknown(18, "pool_2", "confirmed", candidate)
        self.assertEqual(
            coordinator.resolve_unknown(18, "pool_1", "confirmed", candidate),
            "confirmed",
        )
        self.assertEqual(coordinator.snapshot()[18]["state"], "confirmed")


class MultiPoolBookingTest(unittest.TestCase):
    def make_bot(self, **overrides):
        bot = smart.SmartBookingBotV2(make_args(**overrides))
        bot._sleep_before_reconciliation = lambda _delay: True
        bot._sleep_before_multi_pool_post_reconciliation = lambda _delay: True
        return bot

    def test_secondary_account_has_no_fixed_start_delay(self):
        bot = self.make_bot()
        clients = (FakeClient(), FakeClient())
        attach_clients(bot, *clients)
        events = []
        bot.log_event = lambda event, **fields: events.append((event, fields))

        status = bot.run_multi_pool_mode()

        self.assertEqual(status, "success")
        start = next(fields for event, fields in events if event == "multi_pool_start")
        self.assertEqual(start["second_account_delay_ms"], 0)
        self.assertFalse(any(event == "multi_pool_account_start_delay" for event, _fields in events))

    def test_final_submit_business_failure_uses_write_cooldown(self):
        bot = self.make_bot()
        client = FakeClient("business")
        context = bot.account_pool[0]
        context.client = client
        coordinator = smart.MultiPoolCoordinator((18, 19), 1.2)
        bot.direct_deadline = time.monotonic() + 1
        candidate = bot._synthetic_candidate(18, "ymq7")

        result = bot.attempt_single_hour_booking(
            candidate,
            "multi_pool_pool_1_h18",
            1,
            1,
            1,
            client=client,
            account_context=context,
            multi_pool_coordinator=coordinator,
        )

        self.assertEqual(result, "candidate_taken")
        self.assertAlmostEqual(
            context.reservation_gate.last_cooldown_seconds,
            smart.DEFAULT_RESERVATION_PLACE_SUCCESS_GAP,
        )
        self.assertEqual(context.reservation_gate.last_cooldown_reason, "business_failure")

    def test_failed_hour_reports_last_final_submit_candidate(self):
        class FinalThenCanBookMissClient(FakeClient):
            @staticmethod
            def _court(data):
                fields = json.loads(data["fieldinfo"])
                return fields[0]["placeShortName"]

            def request(self, method, endpoint, **kwargs):
                if endpoint == "/place/canBook" and self._court(kwargs["data"]) == "ymq8":
                    with self.lock:
                        self.calls.append((method, endpoint, kwargs, self._hour(kwargs.get("data"))))
                    return smart.HttpResult(
                        200,
                        "",
                        0.01,
                        json_data={"msg": "fail", "data": smart.TAKEN_RETRY_TEXT},
                    )
                return super().request(method, endpoint, **kwargs)

        bot = self.make_bot(priority=[7], backup=[8])
        context = bot.account_pool[0]
        context.client = FinalThenCanBookMissClient("business")
        coordinator = smart.MultiPoolCoordinator((18, 19), 1.2)
        bot.direct_deadline = time.monotonic() + 2
        events = []
        bot.log_event = lambda event, **fields: events.append((event, fields))

        bot._run_multi_pool_account(context, 18, coordinator, None, {}, threading.Lock(), 0)

        result = next(fields for event, fields in events if event == "multi_pool_slot_result")
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["court"], "ymq7")
        self.assertEqual(result["source"], "reservation_failed")

    def test_prewarm_uses_each_account_client_and_never_builds_legacy_workers(self):
        bot = self.make_bot()
        clients = (FakeClient(), FakeClient())
        attach_clients(bot, *clients)

        bot.prewarm()

        self.assertEqual(bot.direct_client_slots, [])
        for context, client in zip(bot.account_pool, clients):
            self.assertEqual(len(client.calls), 2)
            self.assertTrue(
                all(call[2]["params"]["token"] == context.token for call in client.calls)
            )

    def test_double_success_uses_separate_tokens_and_emits_contract_events(self):
        bot = self.make_bot()
        clients = (FakeClient(), FakeClient())
        attach_clients(bot, *clients)
        events = []
        bot.log_event = lambda event, **fields: events.append((event, fields))

        with patch.object(smart, "DEFAULT_MULTI_POOL_SECOND_ACCOUNT_DELAY", 0):
            status = bot.run_multi_pool_mode()

        self.assertEqual(status, "success")
        self.assertEqual([bot.first_booking["hour"], bot.second_booking["hour"]], [18, 19])
        for context, client in zip(bot.account_pool, clients):
            reservation = next(call for call in client.calls if call[1] == "/place/reservationPlace")
            self.assertEqual(reservation[2]["data"]["token"], context.token)
            self.assertEqual(reservation[2]["data"]["cardIndex"], context.card_index)
        slot_events = [fields for event, fields in events if event == "multi_pool_slot_result"]
        self.assertEqual({item["status"] for item in slot_events}, {"confirmed"})
        self.assertEqual({item["account_slot"] for item in slot_events}, {"pool_1", "pool_2"})
        required = {"account_slot", "status", "target_date", "hour", "end_hour", "court", "source"}
        self.assertTrue(all(required <= set(item) for item in slot_events))
        complete = next(fields for event, fields in events if event == "multi_pool_complete")
        self.assertEqual(complete["status"], "success")
        self.assertEqual(complete["confirmed_hours"], [18, 19])

    def test_success_plus_query_failure_is_partial_and_reconciles_with_original_account(self):
        bot = self.make_bot()
        pool_1 = FakeClient("success")
        pool_2 = FakeClient("timeout_query_failure")
        attach_clients(bot, pool_1, pool_2)
        events = []
        bot.log_event = lambda event, **fields: events.append((event, fields))

        with patch.object(smart, "DEFAULT_MULTI_POOL_SECOND_ACCOUNT_DELAY", 0):
            status = bot.run_multi_pool_mode()

        self.assertEqual(status, "unknown")
        self.assertEqual(bot.multi_pool_coordinator.snapshot()[19]["state"], "unknown")
        order_call = next(call for call in pool_2.calls if call[1] == "/place/getPlaceOrder")
        self.assertEqual(order_call[2]["params"]["token"], "token-b")
        self.assertFalse(any(call[1] == "/place/getPlaceOrder" for call in pool_1.calls))
        complete = next(fields for event, fields in events if event == "multi_pool_complete")
        self.assertEqual(complete["unknown_hours"], [19])

    def test_delayed_original_account_reconciliation_can_resolve_unknown(self):
        bot = self.make_bot()
        pool_1 = FakeClient("success")
        pool_2 = FakeClient("timeout_post_confirm")
        attach_clients(bot, pool_1, pool_2)
        events = []
        bot.log_event = lambda event, **fields: events.append((event, fields))

        status = bot.run_multi_pool_mode()

        self.assertEqual(status, "success")
        self.assertEqual(bot.multi_pool_coordinator.snapshot()[19]["state"], "confirmed")
        self.assertEqual(
            len([call for call in pool_2.calls if call[1] == "/place/reservationPlace"]),
            1,
        )
        order_calls = [call for call in pool_2.calls if call[1] == "/place/getPlaceOrder"]
        self.assertGreaterEqual(len(order_calls), 2)
        self.assertTrue(all(call[2]["params"]["token"] == "token-b" for call in order_calls))
        self.assertFalse(any(call[1] == "/place/getPlaceOrder" for call in pool_1.calls))
        pool_2_results = [
            fields
            for event, fields in events
            if event == "multi_pool_slot_result" and fields["account_slot"] == "pool_2"
        ]
        self.assertEqual([item["status"] for item in pool_2_results], ["unknown", "confirmed"])
        self.assertEqual(pool_2_results[-1]["source"], "post_run_order_reconciliation")

    def test_delayed_original_account_reconciliation_can_tombstone_stable_absence(self):
        bot = self.make_bot()
        pool_1 = FakeClient("success")
        pool_2 = FakeClient("timeout_post_empty")
        attach_clients(bot, pool_1, pool_2)
        events = []
        bot.log_event = lambda event, **fields: events.append((event, fields))

        status = bot.run_multi_pool_mode()

        self.assertEqual(status, "partial")
        self.assertEqual(bot.multi_pool_coordinator.snapshot()[19]["state"], "tombstoned")
        self.assertEqual(
            len([call for call in pool_2.calls if call[1] == "/place/reservationPlace"]),
            1,
        )
        order_calls = [call for call in pool_2.calls if call[1] == "/place/getPlaceOrder"]
        self.assertEqual(len(order_calls), 3)
        self.assertTrue(all(call[2]["params"]["token"] == "token-b" for call in order_calls))
        pool_2_results = [
            fields
            for event, fields in events
            if event == "multi_pool_slot_result" and fields["account_slot"] == "pool_2"
        ]
        self.assertEqual([item["status"] for item in pool_2_results], ["unknown", "tombstoned"])
        self.assertEqual(
            pool_2_results[-1]["source"],
            "post_run_order_reconciliation_stable_not_found",
        )

    def test_both_unknown_stops_as_unknown(self):
        bot = self.make_bot()
        clients = (FakeClient("timeout_query_failure"), FakeClient("timeout_query_failure"))
        attach_clients(bot, *clients)

        with patch.object(smart, "DEFAULT_MULTI_POOL_SECOND_ACCOUNT_DELAY", 0):
            status = bot.run_multi_pool_mode()

        self.assertEqual(status, "unknown")
        self.assertEqual(
            {record["state"] for record in bot.multi_pool_coordinator.snapshot().values()},
            {"unknown"},
        )
        self.assertTrue(all(len([c for c in client.calls if c[1] == "/place/reservationPlace"]) == 1 for client in clients))

    def test_single_hour_success_is_kept_when_other_account_fails(self):
        bot = self.make_bot()
        clients = (FakeClient("success"), FakeClient("business"))
        attach_clients(bot, *clients)

        with patch.object(smart, "DEFAULT_MULTI_POOL_SECOND_ACCOUNT_DELAY", 0):
            status = bot.run_multi_pool_mode()

        self.assertEqual(status, "partial")
        self.assertEqual(bot.first_booking["hour"], 18)
        self.assertIsNone(bot.second_booking)
        self.assertEqual(bot.multi_pool_coordinator.snapshot()[18]["state"], "confirmed")
        self.assertFalse(
            any("cancel" in call[1].lower() for client in clients for call in client.calls)
        )

    def test_explicit_business_failures_end_without_terminal_hour_state(self):
        bot = self.make_bot()
        clients = (FakeClient("business"), FakeClient("business"))
        attach_clients(bot, *clients)

        with patch.object(smart, "DEFAULT_MULTI_POOL_SECOND_ACCOUNT_DELAY", 0):
            status = bot.run_multi_pool_mode()

        self.assertEqual(status, "failed")
        self.assertEqual(
            {record["state"] for record in bot.multi_pool_coordinator.snapshot().values()},
            {"available"},
        )

    def test_third_reconciliation_snapshot_can_confirm_original_account_order(self):
        bot = self.make_bot()
        client = FakeClient("timeout_confirm_third")
        context = bot.account_pool[0]
        context.client = client
        coordinator = smart.MultiPoolCoordinator((18, 19), 1.2)
        bot.direct_deadline = time.monotonic() + 1
        candidate = bot._synthetic_candidate(18, "ymq7")

        result = bot.attempt_single_hour_booking(
            candidate,
            "multi_pool_pool_1_h18",
            1,
            1,
            1,
            client=client,
            account_context=context,
            multi_pool_coordinator=coordinator,
        )

        self.assertEqual(result, "success")
        self.assertEqual(client.order_query_count, 3)
        self.assertEqual(coordinator.snapshot()[18]["state"], "confirmed")
        order_calls = [call for call in client.calls if call[1] == "/place/getPlaceOrder"]
        self.assertTrue(all(call[2]["params"]["token"] == context.token for call in order_calls))

    def test_timeout_stable_absence_tombstones_without_final_post_replay(self):
        bot = self.make_bot()
        client = FakeClient("timeout_empty")
        context = bot.account_pool[0]
        context.client = client
        coordinator = smart.MultiPoolCoordinator((18, 19), 1.2)
        bot.direct_deadline = time.monotonic() + 1
        candidate = bot._synthetic_candidate(18, "ymq7")

        result = bot.attempt_single_hour_booking(
            candidate,
            "multi_pool_pool_1_h18",
            1,
            1,
            1,
            client=client,
            account_context=context,
            multi_pool_coordinator=coordinator,
        )

        self.assertEqual(result, "reservation_not_confirmed")
        self.assertEqual(len([c for c in client.calls if c[1] == "/place/reservationPlace"]), 1)
        self.assertEqual(len([c for c in client.calls if c[1] == "/place/getPlaceOrder"]), 3)
        self.assertEqual(coordinator.snapshot()[18]["state"], "tombstoned")
        self.assertEqual(coordinator.try_acquire(18, "pool_2", candidate), "hour_tombstoned")

    def test_too_fast_is_not_blindly_replayed_and_updates_local_and_global_backoff(self):
        bot = self.make_bot()
        client = FakeClient("too_fast")
        context = bot.account_pool[0]
        context.client = client
        coordinator = smart.MultiPoolCoordinator((18, 19), 1.2)
        bot.direct_deadline = time.monotonic() + 1
        candidate = bot._synthetic_candidate(18, "ymq7")

        result = bot.attempt_single_hour_booking(
            candidate,
            "multi_pool_pool_1_h18",
            1,
            1,
            1,
            client=client,
            account_context=context,
            multi_pool_coordinator=coordinator,
        )

        self.assertEqual(result, "retry_delay")
        self.assertEqual(len([c for c in client.calls if c[1] == "/place/reservationPlace"]), 1)
        self.assertAlmostEqual(context.reservation_gate.last_cooldown_seconds, 1.8)
        self.assertGreater(coordinator.global_next_allowed_at, time.monotonic())
        self.assertEqual(bot.account_pool[1].reservation_gate.last_cooldown_seconds, 0)

    def test_confirmed_anchor_tries_alternate_neighbor_and_keeps_hour_owner(self):
        bot = self.make_bot(time="17-21", window_seconds=3)
        pool_1 = FakeClient(mode_by_hour={18: "success", 20: "success", 17: "success"})
        pool_2 = FakeClient(mode_by_hour={19: "business", 17: "success", 20: "success"})
        attach_clients(bot, pool_1, pool_2)

        with patch.object(smart, "DEFAULT_MULTI_POOL_SECOND_ACCOUNT_DELAY", 0):
            status = bot.run_multi_pool_mode()

        self.assertEqual(status, "success")
        confirmed = sorted(
            hour
            for hour, record in bot.multi_pool_coordinator.snapshot().items()
            if record["state"] == "confirmed"
        )
        self.assertEqual(confirmed, [17, 18])
        pool_1_hours = [call[3] for call in pool_1.calls if call[1] == "/place/reservationPlace"]
        pool_2_hours = [call[3] for call in pool_2.calls if call[1] == "/place/reservationPlace"]
        self.assertEqual(pool_1_hours, [18])
        self.assertEqual(pool_2_hours, [19, 17])

    def test_dry_run_emits_explicit_assignment_without_booking_post(self):
        bot = self.make_bot(dry_run=True)
        clients = (FakeClient(), FakeClient())
        attach_clients(bot, *clients)
        events = []
        bot.log_event = lambda event, **fields: events.append((event, fields))

        with patch.object(smart, "DEFAULT_MULTI_POOL_SECOND_ACCOUNT_DELAY", 0):
            status = bot.run_multi_pool_mode()

        self.assertEqual(status, "dry_run")
        slot_events = [fields for event, fields in events if event == "multi_pool_slot_result"]
        self.assertEqual({item["status"] for item in slot_events}, {"dry_run"})
        self.assertEqual({item["account_slot"] for item in slot_events}, {"pool_1", "pool_2"})
        self.assertTrue(all(not any(call[1] == "/place/reservationPlace" for call in client.calls) for client in clients))

    def test_guided_fast_uses_primary_account_probe_and_stops_collector(self):
        bot = self.make_bot(booking_mode=smart.BOOKING_MODE_GUIDED_FAST)
        clients = (FakeClient(), FakeClient())
        attach_clients(bot, *clients)
        probe_calls = []
        collector_state = {}

        class ProbeClient:
            def request(self, method, endpoint, **kwargs):
                probe_calls.append((method, endpoint, kwargs))
                return smart.HttpResult(
                    200,
                    "",
                    0.01,
                    json_data={"msg": "success", "data": {"placeArray": []}},
                )

            def close(self):
                collector_state["probe_closed"] = True

        class CollectorThread:
            def join(self, timeout=None):
                collector_state["joined"] = timeout

        def start_collector(guide_state, _deadline, account_context=None):
            collector_state["account_slot"] = account_context.slot
            stop_event = threading.Event()
            collector_state["stop_event"] = stop_event
            with patch.object(smart, "KeepAliveClient", return_value=ProbeClient()):
                bot._guided_probe_worker(guide_state, 1, account_context)
            return stop_event, CollectorThread()

        bot.start_guided_collector = start_collector
        with patch.object(smart, "DEFAULT_MULTI_POOL_SECOND_ACCOUNT_DELAY", 0):
            status = bot.run_multi_pool_mode()

        self.assertEqual(status, "success")
        self.assertEqual(collector_state["account_slot"], "pool_1")
        self.assertTrue(collector_state["stop_event"].is_set())
        self.assertEqual(collector_state["joined"], 1.0)
        self.assertTrue(collector_state["probe_closed"])
        self.assertEqual(probe_calls[0][2]["params"]["token"], "token-a")
        self.assertEqual(
            {
                call[2]["data"]["token"]
                for client in clients
                for call in client.calls
                if call[1] == "/place/reservationPlace"
            },
            {"token-a", "token-b"},
        )

    def test_redactors_cover_identity_and_password_variants(self):
        raw = (
            'username=alice&userName=bob&password=one&passWord=two&admin_password=three '
            '{"token":"secret","cardIndex":"card"}'
        )
        for redactor in (smart.redact_text, redact_sensitive_text):
            redacted = redactor(raw)
            for secret in ("alice", "bob", "one", "two", "three", "secret"):
                self.assertNotIn(secret, redacted)


if __name__ == "__main__":
    unittest.main()
