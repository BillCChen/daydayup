import json
import os
import re
import shutil
import stat
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import web_console


def make_user(key: str, *, enabled: bool = True, token: str | None = None) -> web_console.UserAccount:
    return web_console.UserAccount(
        key=key,
        label=f"User {key[-1]}",
        token=token if token is not None else f"token-{key}",
        jsessionid=f"session-{key}",
        card_name="学生球类卡",
        enabled=enabled,
    )


class FakeUserStore:
    def __init__(self, users):
        self.users = list(users)

    def list_users(self):
        return list(self.users)

    def get_user(self, user_key=""):
        enabled = [user for user in self.users if user.enabled]
        if not user_key:
            return enabled[0]
        return next(user for user in enabled if user.key == user_key)

    def get_users(self, user_keys):
        ordered = []
        for key in user_keys if isinstance(user_keys, list) else []:
            if key not in ordered:
                ordered.append(key)
        available = {user.key: user for user in self.users if user.enabled}
        if any(key not in available for key in ordered):
            raise web_console.EasySerpError("selected user is not available")
        return [available[key] for key in ordered]


class FakeJobs:
    def __init__(self):
        self.calls = []

    def start_pool(self, payload, accounts):
        self.calls.append((dict(payload), list(accounts)))
        return SimpleNamespace(
            id=1,
            status="running",
            returncode=None,
            started_at=1.0,
            command_label="multi_pool",
            lines=[],
        )


class FakeHistory:
    def __init__(self):
        self.created = []

    def create(self, payload, job_id, command_label, user, participant_users=None):
        self.created.append((payload, job_id, command_label, user, participant_users))
        return "history-1"


class CaptureStdin:
    def __init__(self):
        self.value = ""
        self.closed = False

    def write(self, value):
        self.value += value

    def close(self):
        self.closed = True


class FakeProcess:
    def __init__(self):
        self.stdin = CaptureStdin()
        self.stdout = []
        self.returncode = None

    def poll(self):
        return self.returncode

    def terminate(self):
        self.returncode = -15


class MultiPoolUserStoreTest(unittest.TestCase):
    def test_users_file_is_always_mode_0600_and_get_users_deduplicates_in_order(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "users.csv"
            store = web_console.UserStore(path, default_token="token-1", default_jsessionid="session-1")
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)

            second = store.upsert_user(
                {
                    "key": "user_2",
                    "label": "User 2",
                    "token": "token-2",
                    "jsessionid": "session-2",
                    "card_name": "学生球类卡",
                    "enabled": True,
                }
            )
            users = store.get_users([second.key, web_console.DEFAULT_USER_KEY, second.key])

            self.assertEqual([user.key for user in users], [second.key, web_console.DEFAULT_USER_KEY])
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)

            os.chmod(path, 0o644)
            store.ensure_exists()
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)

    def test_duplicate_token_is_rejected_without_writing_a_second_user(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "users.csv"
            store = web_console.UserStore(path, default_token="shared-token", default_jsessionid="")

            with self.assertRaisesRegex(web_console.EasySerpError, "different WeChat identity"):
                store.upsert_user(
                    {
                        "key": "user_2",
                        "label": "User 2",
                        "token": "shared-token",
                        "card_name": "学生球类卡",
                        "enabled": True,
                    }
                )

            self.assertEqual(len(store.list_users()), 1)


class MultiPoolStartTest(unittest.TestCase):
    def make_console(self):
        console = web_console.WebConsole.__new__(web_console.WebConsole)
        console.config = web_console.ServerConfig("1001", "https://example.invalid", 1.0)
        console.users = FakeUserStore([make_user("user_1"), make_user("user_2")])
        console.jobs = FakeJobs()
        console.resolve_booking_card = lambda user: {
            "card_index_raw": f"card-{user.key}",
            "card_index": "masked",
            "cash_balance_value": 100.0,
        }
        return console

    def payload(self, **updates):
        payload = {
            "account_mode": "multi_pool",
            "user_keys": ["user_1", "user_2"],
            "duration": "2",
            "booking_mode": "direct-fast",
        }
        payload.update(updates)
        return payload

    def test_off_mode_rejects_before_preflight(self):
        console = self.make_console()
        with mock.patch.dict(os.environ, {"DAYDAYUP_MULTI_POOL_MODE": "off"}, clear=False):
            with self.assertRaisesRegex(web_console.EasySerpError, "disabled"):
                console.start_booking(self.payload())
        self.assertEqual(console.jobs.calls, [])

    def test_dry_run_mode_forces_dry_run_and_preflights_both_accounts(self):
        console = self.make_console()
        with mock.patch.dict(os.environ, {"DAYDAYUP_MULTI_POOL_MODE": "dry_run"}, clear=False):
            result = console.start_booking(self.payload(dry_run=False))

        effective_payload, accounts = console.jobs.calls[0]
        self.assertTrue(effective_payload["dry_run"])
        self.assertEqual([user.key for user, _card in accounts], ["user_1", "user_2"])
        self.assertEqual(result["multi_pool_mode"], "dry_run")
        self.assertTrue(result["dry_run"])

    def test_duplicate_users_and_unsupported_shapes_fail_closed(self):
        console = self.make_console()
        invalid_payloads = [
            self.payload(user_keys=["user_1", "user_1"]),
            self.payload(duration="1"),
            self.payload(booking_mode="balanced"),
        ]
        with mock.patch.dict(os.environ, {"DAYDAYUP_MULTI_POOL_MODE": "live"}, clear=False):
            for payload in invalid_payloads:
                with self.subTest(payload=payload):
                    with self.assertRaises(web_console.EasySerpError):
                        console.start_booking(payload)
        self.assertEqual(console.jobs.calls, [])

    def test_missing_token_fails_before_process_start(self):
        console = self.make_console()
        console.users = FakeUserStore([make_user("user_1"), make_user("user_2", token="")])
        with mock.patch.dict(os.environ, {"DAYDAYUP_MULTI_POOL_MODE": "live"}, clear=False):
            with self.assertRaisesRegex(web_console.EasySerpError, "token"):
                console.start_booking(self.payload())
        self.assertEqual(console.jobs.calls, [])

    def test_two_missing_tokens_report_missing_token_before_card_preflight(self):
        console = self.make_console()
        console.users = FakeUserStore(
            [make_user("user_1", token=""), make_user("user_2", token="")]
        )
        with mock.patch.dict(os.environ, {"DAYDAYUP_MULTI_POOL_MODE": "live"}, clear=False):
            with self.assertRaisesRegex(web_console.EasySerpError, "token is required"):
                console.start_booking(self.payload())
        self.assertEqual(console.jobs.calls, [])

    def test_duplicate_account_tokens_fail_before_card_preflight(self):
        console = self.make_console()
        console.users = FakeUserStore(
            [make_user("user_1", token="shared-token"), make_user("user_2", token="shared-token")]
        )
        with mock.patch.dict(os.environ, {"DAYDAYUP_MULTI_POOL_MODE": "live"}, clear=False):
            with self.assertRaisesRegex(web_console.EasySerpError, "distinct account tokens"):
                console.start_booking(self.payload())
        self.assertEqual(console.jobs.calls, [])

    def test_user_list_marks_shared_credentials_without_exposing_tokens(self):
        console = self.make_console()
        console.users = FakeUserStore(
            [make_user("user_1", token="shared-token"), make_user("user_2", token="shared-token")]
        )

        users = console.user_list()["users"]

        self.assertEqual(users[0]["credential_conflicts_with"], ["user_2"])
        self.assertEqual(users[1]["credential_conflicts_with"], ["user_1"])
        self.assertTrue(all(user["credential_conflict"] for user in users))
        self.assertTrue(all("token" not in user for user in users))

    def test_disabled_or_unknown_account_fails_before_process_start(self):
        console = self.make_console()
        console.users = FakeUserStore([make_user("user_1"), make_user("user_2", enabled=False)])
        with mock.patch.dict(os.environ, {"DAYDAYUP_MULTI_POOL_MODE": "live"}, clear=False):
            with self.assertRaises(web_console.EasySerpError):
                console.start_booking(self.payload())
        self.assertEqual(console.jobs.calls, [])

    def test_missing_card_and_insufficient_balance_fail_before_process_start(self):
        for card in (
            {"card_index_raw": "", "cash_balance_value": 100.0},
            {"card_index_raw": "card", "cash_balance_value": 29.99},
        ):
            with self.subTest(card=card):
                console = self.make_console()
                console.resolve_booking_card = lambda _user, value=card: dict(value)
                with mock.patch.dict(
                    os.environ,
                    {"DAYDAYUP_MULTI_POOL_MODE": "live"},
                    clear=False,
                ):
                    with self.assertRaises(web_console.EasySerpError):
                        console.start_booking(self.payload(date="2026-07-19", time="18-20"))
                self.assertEqual(console.jobs.calls, [])

    def test_balance_preflight_matches_the_most_expensive_candidate_hour(self):
        payload = self.payload(date="2026-07-20", time="15-17")
        self.assertEqual(web_console.multi_pool_required_balance(payload), 30.0)
        payload["time"] = "14-16"
        self.assertEqual(web_console.multi_pool_required_balance(payload), 20.0)


class MultiPoolProcessBoundaryTest(unittest.TestCase):
    def test_pool_credentials_only_cross_process_boundary_via_single_stdin_line(self):
        history = FakeHistory()
        manager = web_console.JobManager(web_console.ServerConfig("1001", "https://example.invalid", 1.0), history)
        process = FakeProcess()
        users = [make_user("user_1"), make_user("user_2")]
        popen_call = {}

        def fake_popen(command, **kwargs):
            popen_call["command"] = list(command)
            popen_call["kwargs"] = kwargs
            return process

        with mock.patch.dict(
            os.environ,
            {"DAYDAYUP_MULTI_POOL_MODE": "live"},
            clear=False,
        ), mock.patch("web_console.subprocess.Popen", side_effect=fake_popen), mock.patch(
            "web_console.threading.Thread"
        ) as thread:
            manager.start_pool(
                {"date": "2026-07-19", "duration": "2", "booking_mode": "direct-fast"},
                [(users[0], "card-secret-1"), (users[1], "card-secret-2")],
            )

        command_text = " ".join(popen_call["command"])
        env = popen_call["kwargs"]["env"]
        self.assertIn("--account-pool-stdin", popen_call["command"])
        for secret in ("token-user_1", "token-user_2", "session-user_1", "card-secret-1"):
            self.assertNotIn(secret, command_text)
        self.assertNotIn("DAYDAYUP_TOKEN", env)
        self.assertNotIn("DAYDAYUP_JSESSIONID", env)
        self.assertNotIn("DAYDAYUP_CARD_INDEX", env)
        self.assertEqual(env["DAYDAYUP_MULTI_POOL_MODE"], "live")
        self.assertTrue(process.stdin.closed)
        self.assertEqual(process.stdin.value.count("\n"), 1)
        stdin_payload = json.loads(process.stdin.value)
        self.assertEqual([item["slot"] for item in stdin_payload["accounts"]], ["pool_1", "pool_2"])
        self.assertEqual(stdin_payload["accounts"][1]["token"], "token-user_2")
        self.assertNotIn("token-user_1", history.created[0][2])
        thread.assert_called_once()

    def test_concurrent_pool_start_is_atomically_rejected_before_second_process(self):
        history = FakeHistory()
        manager = web_console.JobManager(
            web_console.ServerConfig("1001", "https://example.invalid", 1.0),
            history,
        )
        users = [make_user("user_1"), make_user("user_2")]
        barrier = threading.Barrier(2)
        outcomes = []
        outcome_lock = threading.Lock()

        def start_once():
            barrier.wait()
            try:
                manager.start_pool(
                    {"date": "2026-07-19", "duration": "2", "booking_mode": "direct-fast"},
                    [(users[0], "card-1"), (users[1], "card-2")],
                )
                outcome = "started"
            except web_console.EasySerpError as exc:
                outcome = str(exc)
            with outcome_lock:
                outcomes.append(outcome)

        with mock.patch.dict(
            os.environ,
            {"DAYDAYUP_MULTI_POOL_MODE": "dry_run"},
            clear=False,
        ), mock.patch(
            "web_console.subprocess.Popen",
            side_effect=lambda *_args, **_kwargs: FakeProcess(),
        ) as popen:
            threads = [threading.Thread(target=start_once) for _index in range(2)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()

        popen.assert_called_once()
        self.assertEqual(outcomes.count("started"), 1)
        self.assertEqual(sum("exclusive" in outcome for outcome in outcomes), 1)


class MultiPoolHistoryTest(unittest.TestCase):
    def test_dry_run_completion_is_preserved_as_a_rehearsal(self):
        participants = [
            {"slot": "pool_1", "user_key": "user_1", "user_label": "User 1"},
            {"slot": "pool_2", "user_key": "user_2", "user_label": "User 2"},
        ]
        job = web_console.BookingJob(
            id=1,
            process=SimpleNamespace(),
            started_at=1.0,
            command_label="multi_pool dry_run",
            history_id="history-1",
            lines=[
                'prefix [EVENT] {"event":"multi_pool_slot_result","account_slot":"pool_1","status":"dry_run","target_date":"2026-07-19","hour":20,"end_hour":21,"court":"羽毛球7","source":"dry_run"}',
                'prefix [EVENT] {"event":"multi_pool_slot_result","account_slot":"pool_2","status":"dry_run","target_date":"2026-07-19","hour":21,"end_hour":22,"court":"羽毛球7","source":"dry_run"}',
                'prefix [EVENT] {"event":"multi_pool_complete","status":"dry_run","confirmed_hours":[],"unknown_hours":[],"tombstoned_hours":[]}',
            ],
            status="completed",
            returncode=0,
            participant_users=participants,
        )

        summary = web_console.summarize_job_history(job)

        self.assertEqual(summary["result"], "演练完成")
        self.assertEqual(summary["pool_summary"]["status"], "dry_run")
        self.assertEqual(
            [(item["account_slot"], item["status"]) for item in summary["hour_ownership"]],
            [("pool_1", "dry_run"), ("pool_2", "dry_run")],
        )

    def test_structured_events_record_hour_ownership_without_human_log_dependency(self):
        participants = [
            {"slot": "pool_1", "user_key": "user_1", "user_label": "User 1"},
            {"slot": "pool_2", "user_key": "user_2", "user_label": "User 2"},
        ]
        lines = [
            'prefix [EVENT] {"event":"multi_pool_slot_result","account_slot":"pool_1","status":"confirmed","target_date":"2026-07-19","hour":20,"end_hour":21,"court":"羽毛球7","source":"reservation"}',
            'prefix [EVENT] {"event":"multi_pool_slot_result","account_slot":"pool_2","status":"unknown","target_date":"2026-07-19","hour":21,"end_hour":22,"court":"羽毛球7","source":"reconcile"}',
            'prefix [EVENT] {"event":"multi_pool_complete","status":"unknown","confirmed_hours":[20],"unknown_hours":[21],"tombstoned_hours":[21]}',
        ]
        job = web_console.BookingJob(
            id=1,
            process=SimpleNamespace(),
            started_at=1.0,
            command_label="multi_pool",
            history_id="history-1",
            lines=lines,
            status="completed",
            returncode=0,
            participant_users=participants,
        )

        summary = web_console.summarize_job_history(job)

        self.assertEqual(summary["result"], "结果未知")
        self.assertEqual(summary["hour_ownership"][0]["user_key"], "user_1")
        self.assertEqual(summary["hour_ownership"][1]["user_key"], "user_2")
        self.assertEqual(summary["pool_summary"]["unknown_hours"], [21])
        self.assertNotIn("prefix", json.dumps(summary, ensure_ascii=False))

    def test_history_filter_matches_any_participant_and_keeps_legacy_fallback(self):
        record = {
            "user_key": "user_1",
            "participant_users": [
                {"slot": "pool_1", "user_key": "user_1"},
                {"slot": "pool_2", "user_key": "user_2"},
            ],
        }
        self.assertTrue(web_console.booking_history_matches_user(record, "user_2", "user_1"))
        self.assertTrue(web_console.booking_history_matches_user({"user_key": "user_1"}, "user_1", "user_1"))
        self.assertTrue(web_console.booking_history_matches_user({}, "user_1", "user_1"))
        self.assertFalse(web_console.booking_history_matches_user(record, "user_3", "user_1"))

    def test_web_redactor_covers_login_field_spellings(self):
        raw = '{"username":"19960000000","passWord":"secret","admin_password":"admin"}'
        redacted = web_console.redact_sensitive_text(raw)
        self.assertNotIn("19960000000", redacted)
        self.assertNotIn("secret", redacted)
        self.assertNotIn('"admin"', redacted)


class MultiPoolFrontendSafetyTest(unittest.TestCase):
    @unittest.skipUnless(shutil.which("node"), "Node.js is required for the frontend state harness")
    def test_account_scoped_runtime_state_harness(self):
        root = Path(web_console.ROOT)
        result = subprocess.run(
            [shutil.which("node"), str(root / "tests" / "web_account_state_harness.js")],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_account_switch_discards_stale_responses_and_exposes_availability_freshness(self):
        root = Path(web_console.ROOT)
        html = (root / "web" / "index.html").read_text(encoding="utf-8")
        javascript = (root / "web" / "app.js").read_text(encoding="utf-8")

        self.assertIn('id="availabilityRefreshState" role="status" aria-live="polite"', html)
        self.assertIn('id="userSaveMessage" role="status" aria-live="polite"', html)
        for function_name in ("loadStatus", "loadCards", "loadBookings", "loadBookingHistory", "loadScanTasks"):
            body = javascript.split(f"async function {function_name}()", 1)[1].split("\n}", 1)[0]
            self.assertIn("const requestUserKey = state.selectedUserKey;", body)
            self.assertIn("if (!isCurrentUserRequest(requestUserKey))", body)

        change_user = javascript.split("async function changeUser()", 1)[1].split("function showAccountDataLoading()", 1)[0]
        self.assertIn("showAccountDataLoading();", change_user)
        self.assertIn("await triggerRefresh({ includeUsers: false, force: true });", change_user)
        save_user = javascript.split("async function saveUser(event)", 1)[1].split("function setTokenHelperMessage", 1)[0]
        self.assertIn("if (previousUserKey !== state.selectedUserKey)", save_user)
        self.assertIn("showAccountDataLoading();", save_user)
        self.assertIn('setAvailabilityRefreshState(`${fmtTime(updatedAt)} 已更新`, "success")', javascript)
        self.assertIn('showAvailabilityWarning(`${removedCount} 个已选场地已失效，已从选择中移除`)', javascript)
        self.assertIn('className = `chip availability-refresh-state ${tone}`.trim()', javascript)
        self.assertIn('setUserSaveMessage(`保存失败: ${error.message}`, "danger-text")', javascript)
        self.assertIn("共享授权数据", javascript)
        self.assertIn("credential_conflicts_with", javascript)
        self.assertIn("共享同一微信授权，无法作为独立账号组合预约", javascript)

    @unittest.skipUnless(shutil.which("node"), "Node.js is required for the visual contract harness")
    def test_browser_computed_visual_contract(self):
        root = Path(web_console.ROOT)
        result = subprocess.run(
            [shutil.which("node"), str(root / "tests" / "web_visual_contract_harness.js")],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
        if result.returncode == 0 and result.stdout.startswith("SKIP:"):
            self.skipTest(result.stdout.strip())
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_borderless_semantic_workspace_contract(self):
        root = Path(web_console.ROOT)
        html = (root / "web" / "index.html").read_text(encoding="utf-8")
        css = (root / "web" / "styles.css").read_text(encoding="utf-8")
        javascript = (root / "web" / "app.js").read_text(encoding="utf-8")

        self.assertIn('class="panel user-panel utility-panel" id="users"', html)
        self.assertNotIn('id="users" hidden', html)
        self.assertIn('class="lower-workspace"', html)
        self.assertIn('class="lower-side"', html)
        self.assertIn('class="status-pill status-help-trigger"', html)
        self.assertNotIn('id="sessionHelpTrigger"', html)
        self.assertNotIn("本地访问密码默认", html)
        self.assertIn('sessionHelpTrigger: document.querySelector("#sessionState")', javascript)
        self.assertIn(".utility-panel,", css)
        self.assertIn("#users.utility-panel {\n  display: block !important;\n}", css)
        self.assertIn(".topbar {\n  z-index: 10;", css)
        self.assertIn("overflow: visible;", css)
        self.assertIn(
            '.booking-form input:not([type="checkbox"]):not([type="range"]):not([type="hidden"])',
            css,
        )

        border_values = re.findall(
            r"(?<!-)\bborder(?:-(?:top|right|bottom|left))?\s*:\s*([^;}{]+)",
            css,
        )
        self.assertTrue(border_values)
        self.assertTrue(
            all(value.strip() in {"0", "none"} for value in border_values),
            border_values,
        )
        for selector in ("#wallet", "#availability", "#scanTasks", "#submit", "#bookingHistory", "#users"):
            self.assertIn(f"{selector} {{", css)
        for ambient_selector in ("body::before", "body::after", ".panel::after", ".lower-side::after"):
            self.assertIn(ambient_selector, css)

    def test_token_password_is_ephemeral_and_new_password_autocomplete(self):
        root = Path(web_console.ROOT)
        html = (root / "web" / "index.html").read_text(encoding="utf-8")
        javascript = (root / "web" / "app.js").read_text(encoding="utf-8")

        self.assertIn('name="token_password" type="password" autocomplete="new-password"', html)
        finally_block = javascript.split("async function exchangeUserToken()", 1)[1].split("async function changeUser()", 1)[0]
        self.assertIn('finally {\n    els.userForm.elements.token_password.value = "";', finally_block)


if __name__ == "__main__":
    unittest.main()
