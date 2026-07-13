import json
import io
import tempfile
import threading
import time
import unittest
from unittest import mock
from datetime import datetime
from pathlib import Path

import web_console


def make_places(schedule):
    places = []
    for court_number, ranges in schedule.items():
        places.append(
            {
                "projectName": {"shortname": f"ymq{court_number}", "name": f"羽毛球{court_number}"},
                "projectInfo": [
                    {
                        "oldMoney": 100.0,
                        "money": 100.0,
                        "starttime": start,
                        "endtime": end,
                        "state": 1,
                    }
                    for start, end in ranges
                ],
            }
        )
    return places


def target(date_value="2026-05-22", start_time="18:00", end_time="21:00", status="pending"):
    return {
        "id": "target_1",
        "date": date_value,
        "start_time": start_time,
        "end_time": end_time,
        "status": status,
        "booked_slots": [],
    }


def task_for(targets, **overrides):
    task = {
        "id": "scan_1",
        "name": "Evening scan",
        "user_key": "user_1",
        "user_label": "User 1",
        "status": "active",
        "targets": targets,
        "success_mode": "any",
        "scan_interval_minutes": 30,
        "court_mode": "selected",
        "selected_courts": [2, 3, 4],
        "same_court_required": False,
        "iterative_optimization": False,
    }
    task.update(overrides)
    return task


def slot(start_time, end_time, bill_num=""):
    result = {
        "date": "2026-05-22",
        "time": f"{start_time}-{end_time}",
        "start_time": start_time,
        "end_time": end_time,
        "id": "ymq2",
        "name": "羽毛球2",
        "number": 2,
        "pay_value": 30.0,
        "price_value": 100.0,
    }
    if bill_num:
        result["bill_num"] = bill_num
    return result


class FakeUserStore:
    def __init__(self):
        self.user = web_console.UserAccount(
            key="user_1",
            label="User 1",
            token="token",
            jsessionid="session",
            card_name="学生球类卡",
            enabled=True,
        )

    def get_user(self, user_key=""):
        return self.user


class FakeScanApp:
    def __init__(self, places, cancel_confirmed=True, booking_fails=False):
        self.users = FakeUserStore()
        self.places = places
        self.cancel_confirmed = cancel_confirmed
        self.booking_fails = booking_fails
        self.booked_payloads = []
        self.cancelled_payloads = []

    def client(self, user):
        return object()

    def _fetch_places(self, client, user, date_value):
        return self.places

    def book_exact(self, payload):
        self.booked_payloads.append(payload)
        if self.booking_fails:
            return {
                "successes": [],
                "failures": [{"slot": payload["slots"][0], "error": "slot is gone"}],
            }
        successes = []
        for item in payload["slots"]:
            booked = dict(item)
            booked["bill_num"] = f"bill-{item['start_time']}"
            successes.append({"slot": booked})
        return {"successes": successes, "failures": []}

    def cancel(self, payload):
        self.cancelled_payloads.append(payload)
        return {"confirmed": self.cancel_confirmed}


class ScanBookingTest(unittest.TestCase):
    def test_scan_time_windows(self):
        scan_target = target()
        self.assertEqual(
            web_console.target_release_datetime(scan_target),
            datetime(2026, 5, 17, 12, 30),
        )
        self.assertTrue(web_console.quiet_window_active(datetime(2026, 5, 17, 11, 45)))
        self.assertFalse(web_console.quiet_window_active(datetime(2026, 5, 17, 12, 30)))
        self.assertFalse(web_console.target_in_lockout(scan_target, datetime(2026, 5, 21, 17, 59)))
        self.assertTrue(web_console.target_in_lockout(scan_target, datetime(2026, 5, 21, 18, 0)))

    def test_scan_candidates_and_score(self):
        places = make_places({2: [("18:00", "19:00"), ("19:00", "20:00"), ("20:00", "21:00")]})
        candidates = web_console.scan_candidates_for_target(task_for([target()]), target(), places)
        self.assertGreaterEqual(len(candidates), 2)
        self.assertEqual([item["start_time"] for item in candidates[0]["slots"]], ["19:00", "20:00"])

    def test_same_court_requirement_filters_cross_court_pairs(self):
        places = make_places({2: [("18:00", "19:00")], 3: [("19:00", "20:00")]})
        flexible = task_for([target(end_time="20:00")], same_court_required=False)
        strict = task_for([target(end_time="20:00")], same_court_required=True)
        self.assertEqual(len(web_console.scan_candidates_for_target(flexible, flexible["targets"][0], places)), 1)
        self.assertEqual(len(web_console.scan_candidates_for_target(strict, strict["targets"][0], places)), 0)

    def test_single_hour_target_generates_single_slot_candidates(self):
        scan_target = target(start_time="18:00", end_time="19:00")
        places = make_places({2: [("18:00", "19:00")], 3: [("18:00", "19:00")]})
        candidates = web_console.scan_candidates_for_target(task_for([scan_target]), scan_target, places)

        self.assertEqual(len(candidates), 2)
        self.assertEqual(len(candidates[0]["slots"]), 1)
        self.assertEqual(candidates[0]["slots"][0]["start_time"], "18:00")

    def test_single_hour_target_books_one_slot(self):
        app = FakeScanApp(make_places({2: [("18:00", "19:00")]}))
        manager = web_console.ScanTaskManager.__new__(web_console.ScanTaskManager)
        manager.app = app
        manager.events = web_console.ScanEventStore(Path(tempfile.mkstemp()[1]))
        manager.record_event = lambda *args, **kwargs: None
        scan_task = task_for([target(start_time="18:00", end_time="19:00")])

        manager.process_task(scan_task, datetime(2026, 5, 17, 13, 0))

        self.assertEqual(scan_task["targets"][0]["status"], "booked")
        self.assertEqual([item["start_time"] for item in scan_task["targets"][0]["booked_slots"]], ["18:00"])
        self.assertEqual(len(app.booked_payloads[0]["slots"]), 1)

    def test_success_modes_and_completion_readiness(self):
        first = target(status="booked")
        second = target(date_value="2026-05-23")
        self.assertTrue(web_console.task_satisfied(task_for([first, second], success_mode="any")))
        self.assertFalse(web_console.task_satisfied(task_for([first, second], success_mode="all")))
        second["status"] = "booked"
        self.assertTrue(web_console.task_satisfied(task_for([first, second], success_mode="all")))
        iterative = task_for([first], iterative_optimization=True)
        self.assertFalse(web_console.task_completion_ready(iterative, datetime(2026, 5, 17, 13, 0)))
        self.assertTrue(web_console.task_completion_ready(iterative, datetime(2026, 5, 21, 18, 0)))

    def test_process_task_books_and_records_bills(self):
        app = FakeScanApp(make_places({2: [("18:00", "19:00"), ("19:00", "20:00")]}))
        manager = web_console.ScanTaskManager.__new__(web_console.ScanTaskManager)
        manager.app = app
        manager.recorded_events = []
        manager.record_event = lambda task, event_type, title, message, **kwargs: manager.recorded_events.append(event_type)
        scan_task = task_for([target(end_time="20:00")], same_court_required=True)

        manager.process_task(scan_task, datetime(2026, 5, 17, 13, 0))

        self.assertEqual(scan_task["status"], "completed")
        self.assertEqual(scan_task["targets"][0]["status"], "booked")
        self.assertEqual([item["bill_num"] for item in scan_task["targets"][0]["booked_slots"]], ["bill-18:00", "bill-19:00"])
        self.assertEqual(manager.recorded_events, ["scan_booking_success", "task_completed"])

    def test_iterative_optimization_preserves_overlap(self):
        app = FakeScanApp(make_places({2: [("19:00", "20:00"), ("20:00", "21:00")]}))
        manager = web_console.ScanTaskManager.__new__(web_console.ScanTaskManager)
        manager.app = app
        manager.record_event = lambda task, event_type, title, message, **kwargs: None
        scan_target = target(status="booked")
        scan_target["booked_slots"] = [slot("18:00", "19:00", "old-18"), slot("19:00", "20:00", "old-19")]
        scan_task = task_for([scan_target], same_court_required=True, iterative_optimization=True)

        manager.process_task(scan_task, datetime(2026, 5, 17, 13, 0))

        self.assertEqual(scan_task["status"], "active")
        self.assertEqual([item["start_time"] for item in scan_target["booked_slots"]], ["19:00", "20:00"])
        self.assertEqual(app.cancelled_payloads[0]["bill_num"], "old-18")
        self.assertEqual([item["start_time"] for item in app.booked_payloads[0]["slots"]], ["20:00"])

    def test_partial_target_can_book_missing_hour(self):
        app = FakeScanApp(make_places({2: [("18:00", "19:00"), ("19:00", "20:00")]}))
        manager = web_console.ScanTaskManager.__new__(web_console.ScanTaskManager)
        manager.app = app
        manager.record_event = lambda task, event_type, title, message, **kwargs: None
        scan_target = target(end_time="20:00", status="partial")
        scan_target["booked_slots"] = [slot("18:00", "19:00", "old-18")]
        scan_task = task_for([scan_target], same_court_required=True)

        manager.process_task(scan_task, datetime(2026, 5, 17, 13, 0))

        self.assertEqual(scan_target["status"], "booked")
        self.assertEqual([item["start_time"] for item in scan_target["booked_slots"]], ["18:00", "19:00"])
        self.assertEqual([item["start_time"] for item in app.booked_payloads[0]["slots"]], ["19:00"])

    def test_unconfirmed_cancel_blocks_rebook(self):
        app = FakeScanApp(make_places({2: [("19:00", "20:00"), ("20:00", "21:00")]}), cancel_confirmed=False)
        manager = web_console.ScanTaskManager.__new__(web_console.ScanTaskManager)
        manager.app = app
        events = []
        manager.record_event = lambda task, event_type, title, message, **kwargs: events.append(event_type)
        scan_target = target(status="booked")
        scan_target["booked_slots"] = [slot("18:00", "19:00", "old-18"), slot("19:00", "20:00", "old-19")]
        scan_task = task_for([scan_target], same_court_required=True, iterative_optimization=True)

        manager.process_task(scan_task, datetime(2026, 5, 17, 13, 0))

        self.assertEqual(events, ["scan_cancel_failed"])
        self.assertEqual(app.booked_payloads, [])
        self.assertEqual([item["start_time"] for item in scan_target["booked_slots"]], ["18:00", "19:00"])

    def test_rebook_failure_keeps_known_partial_state(self):
        app = FakeScanApp(make_places({2: [("19:00", "20:00"), ("20:00", "21:00")]}), booking_fails=True)
        manager = web_console.ScanTaskManager.__new__(web_console.ScanTaskManager)
        manager.app = app
        events = []
        manager.record_event = lambda task, event_type, title, message, **kwargs: events.append(event_type)
        scan_target = target(status="booked")
        scan_target["booked_slots"] = [slot("18:00", "19:00", "old-18"), slot("19:00", "20:00", "old-19")]
        scan_task = task_for([scan_target], same_court_required=True, iterative_optimization=True)

        manager.process_task(scan_task, datetime(2026, 5, 17, 13, 0))

        self.assertIn("scan_cancel_success", events)
        self.assertIn("scan_rebook_failed", events)
        self.assertEqual(scan_target["status"], "partial")
        self.assertEqual([item["start_time"] for item in scan_target["booked_slots"]], ["19:00"])

    def test_lockout_blocks_iterative_changes(self):
        app = FakeScanApp(make_places({2: [("19:00", "20:00"), ("20:00", "21:00")]}))
        manager = web_console.ScanTaskManager.__new__(web_console.ScanTaskManager)
        manager.app = app
        manager.record_event = lambda task, event_type, title, message, **kwargs: None
        scan_target = target(status="booked")
        scan_target["booked_slots"] = [slot("18:00", "19:00", "old-18"), slot("19:00", "20:00", "old-19")]
        scan_task = task_for([scan_target], same_court_required=True, iterative_optimization=True)

        manager.process_task(scan_task, datetime(2026, 5, 21, 18, 0))

        self.assertEqual(scan_task["status"], "completed")
        self.assertEqual(app.cancelled_payloads, [])
        self.assertEqual(app.booked_payloads, [])

    def test_task_update_state_guards_and_no_mail(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            manager = web_console.ScanTaskManager.__new__(web_console.ScanTaskManager)
            manager.tasks = web_console.ScanTaskStore(Path(tmpdir) / "scan_tasks.json")
            manager.events = web_console.ScanEventStore(Path(tmpdir) / "scan_events.json")
            manager.lock = threading.Lock()
            scan_task = task_for([target()])
            manager.tasks.save_all([scan_task])
            sent = []
            original = web_console.send_scan_email
            web_console.send_scan_email = lambda subject, body: sent.append((subject, body))
            try:
                manager.update({"id": "scan_1", "action": "pause"})
                paused = manager.tasks.list()[0]
                self.assertEqual(paused["status"], "paused")
                events = manager.events.list(limit=10)
                self.assertFalse(events[0]["important"])
                self.assertEqual(sent, [])
                paused["status"] = "stopped"
                manager.tasks.save_all([paused])
                with self.assertRaises(web_console.EasySerpError):
                    manager.update({"id": "scan_1", "action": "resume"})
            finally:
                web_console.send_scan_email = original

    def test_web_console_can_disable_embedded_scan_worker(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            original_tasks_path = web_console.SCAN_TASKS_PATH
            original_events_path = web_console.SCAN_EVENTS_PATH
            original_history_path = web_console.HISTORY_PATH
            web_console.SCAN_TASKS_PATH = Path(tmpdir) / "scan_tasks.json"
            web_console.SCAN_EVENTS_PATH = Path(tmpdir) / "scan_events.json"
            web_console.HISTORY_PATH = Path(tmpdir) / "booking_history.json"
            config = web_console.ServerConfig(
                shop_num="1001",
                base_url="https://example.invalid/easyserpClient",
                timeout=1.0,
            )
            try:
                console = web_console.WebConsole(config, FakeUserStore(), start_scan_worker=False)
                self.assertIsNone(console.scans.thread)
                created = console.create_scan_task(
                    {
                        "user_key": "user_1",
                        "targets": [{"date": "2026-05-22", "start_time": "18:00", "end_time": "19:00"}],
                    }
                )
                self.assertEqual(created["task"]["status"], "active")
                console.close()
            finally:
                web_console.SCAN_TASKS_PATH = original_tasks_path
                web_console.SCAN_EVENTS_PATH = original_events_path
                web_console.HISTORY_PATH = original_history_path

    def test_daily_summary_is_sent_once(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            manager = web_console.ScanTaskManager.__new__(web_console.ScanTaskManager)
            manager.events = web_console.ScanEventStore(Path(tmpdir) / "scan_events.json")
            event = web_console.make_scan_event(
                {"id": "scan_1", "name": "Evening scan"},
                "scan_booking_success",
                "扫描预约成功",
                "已预约 18:00-20:00",
                important=True,
            )
            summary_time = datetime.now().replace(hour=22, minute=0, second=0, microsecond=0)
            event_time = summary_time.replace(hour=21, minute=30)
            event["created_at"] = web_console.format_datetime(event_time)
            event["created_ts"] = event_time.timestamp()
            manager.events.append(event)
            sent = []
            original = web_console.send_scan_email
            web_console.send_scan_email = lambda subject, body: sent.append((subject, body))
            try:
                manager.send_daily_summary_if_due(summary_time)
                manager.send_daily_summary_if_due(summary_time.replace(minute=5))
            finally:
                web_console.send_scan_email = original
            summaries = [item for item in manager.events.list(limit=20) if item.get("type") == "daily_summary"]
            self.assertEqual(len(sent), 1)
            self.assertEqual(len(summaries), 1)

    def test_booking_history_window_and_retention(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "booking_history.json"
            now = time.time()
            records = [
                {"id": "recent", "requested_ts": now - 2 * 60 * 60, "requested_at": "2026-05-23 08:00:00"},
                {"id": "older", "requested_ts": now - 8 * 60 * 60, "requested_at": "2026-05-23 02:00:00"},
                {"id": "expired", "requested_ts": now - 8 * 24 * 60 * 60, "requested_at": "2026-05-15 08:00:00"},
            ]
            path.write_text(json.dumps(records), encoding="utf-8")
            store = web_console.BookingHistoryStore(path)

            self.assertEqual([item["id"] for item in store.list(limit=10, window_hours=6)], ["recent"])
            self.assertEqual({item["id"] for item in store.list(limit=10, window_hours=12)}, {"recent", "older"})
            retained = json.loads(path.read_text(encoding="utf-8"))
            self.assertNotIn("expired", {item["id"] for item in retained})

    def test_scan_events_window_and_retention(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "scan_events.json"
            now = time.time()
            events = [
                {"id": "recent", "type": "scan", "created_ts": now - 2 * 60 * 60, "created_at": "2026-05-23 08:00:00"},
                {"id": "older", "type": "scan", "created_ts": now - 8 * 60 * 60, "created_at": "2026-05-23 02:00:00"},
                {"id": "expired", "type": "scan", "created_ts": now - 8 * 24 * 60 * 60, "created_at": "2026-05-15 08:00:00"},
            ]
            path.write_text(json.dumps({"events": events}), encoding="utf-8")
            store = web_console.ScanEventStore(path)

            self.assertEqual([item["id"] for item in store.list(limit=10, window_hours=6)], ["recent"])
            self.assertEqual({item["id"] for item in store.list(limit=10, window_hours=12)}, {"recent", "older"})
            retained = json.loads(path.read_text(encoding="utf-8"))["events"]
            self.assertNotIn("expired", {item["id"] for item in retained})


class FakeBookingProcess:
    def __init__(self):
        self.stdout = io.StringIO("")
        self.terminated = False

    def poll(self):
        return None

    def terminate(self):
        self.terminated = True


class JobManagerTests(unittest.TestCase):
    def test_start_allows_multiple_concurrent_booking_jobs(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            history = web_console.BookingHistoryStore(Path(tmpdir) / "booking_history.json")
            manager = web_console.JobManager(
                web_console.ServerConfig("1", "https://example.invalid/easyserpClient", 10),
                history,
            )
            user = web_console.UserAccount(
                key="user_1",
                label="User 1",
                token="token",
                jsessionid="session",
                card_name="学生球类卡",
                enabled=True,
            )
            processes = [FakeBookingProcess(), FakeBookingProcess()]
            payload = {
                "date": "2026-05-22",
                "time": "18-21",
                "duration": "1",
                "booking_mode": "direct-fast",
            }

            with mock.patch("web_console.subprocess.Popen", side_effect=processes):
                first = manager.start(payload, user, "card_1")
                second = manager.start(payload, user, "card_1")

            snapshot = manager.snapshot()
            self.assertEqual(first.id, 1)
            self.assertEqual(second.id, 2)
            self.assertTrue(snapshot["running"])
            self.assertEqual(snapshot["active_count"], 2)
            self.assertEqual([job["id"] for job in snapshot["jobs"]], [1, 2])
            self.assertEqual(snapshot["job"]["id"], 2)
            self.assertEqual(
                len({item["id"] for item in history.list(limit=10, window_hours=None)}),
                2,
            )

            stopped = manager.stop()
            self.assertTrue(stopped["stopped"])
            self.assertEqual(stopped["stopped_count"], 2)
            self.assertTrue(all(process.terminated for process in processes))

    def test_history_marks_running_records_without_active_process_as_orphaned(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "booking_history.json"
            path.write_text(
                json.dumps(
                    [
                        {"id": "active", "job_id": 1, "status": "running", "result": "运行中", "requested_ts": time.time()},
                        {"id": "lost", "job_id": 2, "status": "running", "result": "运行中", "requested_ts": time.time()},
                    ]
                ),
                encoding="utf-8",
            )
            history = web_console.BookingHistoryStore(path)

            history.mark_orphaned_running({1})

            records = {item["id"]: item for item in history.list(limit=10, window_hours=None)}
            self.assertEqual(records["active"]["status"], "running")
            self.assertEqual(records["lost"]["status"], "orphaned")
            self.assertEqual(records["lost"]["result"], "已失联")
            self.assertTrue(records["lost"].get("finished_at"))

    def test_active_job_ids_finalizes_completed_process_before_orphan_sweep(self):
        class CompletedProcess(FakeBookingProcess):
            def poll(self):
                return 0

        class RecordingHistory:
            def __init__(self):
                self.finished = []

            def finish(self, job):
                self.finished.append(job.id)

        history = RecordingHistory()
        manager = web_console.JobManager(
            web_console.ServerConfig("1", "https://example.invalid/easyserpClient", 10),
            history,
        )
        job = web_console.BookingJob(1, CompletedProcess(), time.time(), "test", "history-1")
        manager.jobs[job.id] = job

        self.assertEqual(manager.active_job_ids(), set())
        self.assertEqual(job.status, "completed")
        self.assertTrue(job.history_finalized)
        self.assertEqual(history.finished, [1])


if __name__ == "__main__":
    unittest.main()
