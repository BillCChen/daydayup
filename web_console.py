#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Local web console for booking operations.
"""

from __future__ import annotations

import argparse
import csv
import fcntl
import json
import os
import re
import smtplib
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from datetime import date, datetime, time as datetime_time, timedelta
from email.mime.text import MIMEText
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, quote, urlsplit

from easyserp_client import (
    DEFAULT_BASE_URL,
    DEFAULT_JSESSIONID,
    DEFAULT_SHORT_NAME,
    DEFAULT_SHOP_NUM,
    DEFAULT_TOKEN,
    EasySerpClient,
    EasySerpError,
    fetch_orders,
    find_order,
    format_amount,
    is_cancelled,
    mask_card,
    redact_sensitive_text,
    require_success,
    summarize_order,
    trim_time,
)
import availability_analytics


ROOT = Path(__file__).resolve().parent
WEB_DIR = ROOT / "web"
BOOKING_SCRIPT = ROOT / "enhanced_book_smart_v2.py"
HISTORY_PATH = ROOT / "logs" / "booking_history.json"
USERS_PATH = ROOT / "local" / "users.csv"
SCAN_TASKS_PATH = ROOT / "local" / "scan_tasks.json"
SCAN_EVENTS_PATH = ROOT / "local" / "scan_events.json"
MAIL_CONFIG_PATH = ROOT / "local" / "cloudflared_mail.env"
DEFAULT_WEB_PORT = int(os.getenv("DAYDAYUP_WEB_PORT", "8789"))
WALL_COURTS = {4, 5, 12}
SAFE_COURTS = [2, 3, 4, 6, 7, 8, 9, 10, 11]
ALL_COURTS = list(range(1, 13))
USER_FIELDS = ("type", "key", "label", "password", "token", "jsessionid", "card_name", "enabled")
DEFAULT_CARD_NAME = "学生球类卡"
DEFAULT_USER_KEY = "chen_qixuan"
DEFAULT_USER_LABEL = "陈启轩"
DEFAULT_OAUTH_REDIRECT_URL = "https://www.147soft.cn/easyserp/index.html"
PROJECT_TYPE = "3"
BOOKING_ITEM_TYPE = "羽毛球"
BOOKING_CALL_DELAY_SECONDS = 0.03
BOOKING_MODES = {"balanced", "direct-fast", "guided-fast"}
SCAN_MIN_INTERVAL_MINUTES = 5
SCAN_MAX_INTERVAL_MINUTES = 1440
SCAN_DEFAULT_INTERVAL_MINUTES = 30
SCAN_SILENT_START = datetime_time(11, 30)
SCAN_SILENT_END = datetime_time(12, 30)
SCAN_SUMMARY_TIME = datetime_time(22, 0)
LOG_WINDOW_CHOICES_HOURS = {6, 12, 24, 168}
LOG_DEFAULT_WINDOW_HOURS = 6
LOG_RETENTION_SECONDS = 7 * 24 * 60 * 60


@dataclass
class ServerConfig:
    shop_num: str
    base_url: str
    timeout: float


@dataclass(frozen=True)
class UserAccount:
    key: str
    label: str
    token: str
    jsessionid: str
    card_name: str
    enabled: bool


@dataclass
class BookingJob:
    id: int
    process: subprocess.Popen[str]
    started_at: float
    command_label: str
    history_id: str
    lines: list[str] = field(default_factory=list)
    status: str = "running"
    returncode: int | None = None
    history_finalized: bool = False


class UserStore:
    def __init__(self, path: Path, *, default_token: str, default_jsessionid: str):
        self.path = path
        self.default_token = default_token
        self.default_jsessionid = default_jsessionid
        self.lock = threading.Lock()
        self.ensure_exists()

    def ensure_exists(self) -> None:
        with self.lock:
            if self.path.exists():
                return
            rows = [
                {
                    "type": "config",
                    "key": "web_access",
                    "label": "网页访问",
                    "password": "abc123",
                    "token": "",
                    "jsessionid": "",
                    "card_name": "",
                    "enabled": "1",
                },
                {
                    "type": "config",
                    "key": "admin",
                    "label": "用户管理",
                    "password": "abc123",
                    "token": "",
                    "jsessionid": "",
                    "card_name": "",
                    "enabled": "1",
                },
                {
                    "type": "user",
                    "key": DEFAULT_USER_KEY,
                    "label": DEFAULT_USER_LABEL,
                    "password": "",
                    "token": self.default_token,
                    "jsessionid": self.default_jsessionid,
                    "card_name": DEFAULT_CARD_NAME,
                    "enabled": "1",
                },
            ]
            self._write_rows_unlocked(rows)

    def verify_access(self, value: str) -> bool:
        return bool(value) and value == self.config_password("web_access")

    def verify_admin(self, value: str) -> bool:
        return bool(value) and value == self.config_password("admin")

    def config_password(self, key: str) -> str:
        with self.lock:
            for row in self._read_rows_unlocked():
                if row.get("type") == "config" and row.get("key") == key:
                    return clean_string(row.get("password"))
        return ""

    def list_users(self) -> list[UserAccount]:
        with self.lock:
            return [self._row_to_account(row) for row in self._read_rows_unlocked() if row.get("type") == "user"]

    def enabled_users(self) -> list[UserAccount]:
        return [user for user in self.list_users() if user.enabled]

    def get_user(self, user_key: str = "") -> UserAccount:
        users = self.enabled_users()
        if not users:
            raise EasySerpError("no enabled users")
        if not user_key:
            return users[0]
        for user in users:
            if user.key == user_key:
                return user
        raise EasySerpError("selected user is not available")

    def upsert_user(self, payload: dict[str, Any]) -> UserAccount:
        key = clean_user_key(payload.get("key"))
        label = clean_string(payload.get("label"))
        token = clean_string(payload.get("token"))
        jsessionid = clean_string(payload.get("jsessionid"))
        card_name = clean_string(payload.get("card_name")) or DEFAULT_CARD_NAME
        enabled = "1" if payload.get("enabled", True) else "0"
        if not key:
            key = clean_user_key(label)
        if not key:
            raise EasySerpError("user key is required")
        if not label:
            raise EasySerpError("user label is required")

        with self.lock:
            rows = self._read_rows_unlocked()
            updated = False
            saved_row = None
            for row in rows:
                if row.get("type") == "user" and row.get("key") == key:
                    if not token:
                        token = clean_string(row.get("token"))
                    if not jsessionid:
                        jsessionid = clean_string(row.get("jsessionid"))
                    if not token:
                        raise EasySerpError("token is required")
                    row.update(
                        {
                            "label": label,
                            "token": token,
                            "jsessionid": jsessionid,
                            "card_name": card_name,
                            "enabled": enabled,
                        }
                    )
                    updated = True
                    saved_row = dict(row)
                    break
            if not updated:
                if not token:
                    raise EasySerpError("token is required")
                rows.append(
                    {
                        "type": "user",
                        "key": key,
                        "label": label,
                        "password": "",
                        "token": token,
                        "jsessionid": jsessionid,
                        "card_name": card_name,
                        "enabled": enabled,
                    }
                )
                saved_row = dict(rows[-1])
            self._write_rows_unlocked(rows)
        return self._row_to_account(saved_row or {})

    def _read_rows_unlocked(self) -> list[dict[str, str]]:
        if not self.path.exists():
            return []
        with self.path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            rows = []
            for row in reader:
                rows.append({field: clean_string(row.get(field)) for field in USER_FIELDS})
            return rows

    def _write_rows_unlocked(self, rows: list[dict[str, str]]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = self.path.with_suffix(".tmp")
        with tmp_path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=USER_FIELDS)
            writer.writeheader()
            for row in rows:
                writer.writerow({field: row.get(field, "") for field in USER_FIELDS})
        tmp_path.replace(self.path)

    @staticmethod
    def _row_to_account(row: dict[str, str]) -> UserAccount:
        return UserAccount(
            key=clean_string(row.get("key")),
            label=clean_string(row.get("label")),
            token=clean_string(row.get("token")),
            jsessionid=clean_string(row.get("jsessionid")),
            card_name=clean_string(row.get("card_name")) or DEFAULT_CARD_NAME,
            enabled=clean_string(row.get("enabled")) != "0",
        )


class BookingHistoryStore:
    def __init__(self, path: Path):
        self.path = path
        self.lock = threading.Lock()

    def list(self, limit: int = 30, window_hours: int | None = LOG_DEFAULT_WINDOW_HOURS) -> list[dict[str, Any]]:
        with self.lock:
            records = self._read_unlocked()
            retained = self._retained_records(records)
            if len(retained) != len(records):
                self._write_unlocked(retained)
            records = retained
        cutoff = log_window_cutoff_ts(window_hours)
        if cutoff is not None:
            records = [record for record in records if self._record_ts(record) >= cutoff]
        records.sort(key=lambda item: item.get("requested_ts", 0), reverse=True)
        return records[:limit]

    def create(self, payload: dict[str, Any], job_id: int, command_label: str, user: UserAccount) -> str:
        now = time.time()
        record_id = f"{int(now * 1000)}-{job_id}"
        record = {
            "id": record_id,
            "job_id": job_id,
            "requested_at": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(now)),
            "requested_ts": now,
            "target_date": target_date_from_payload(payload),
            "target_time": clean_string(payload.get("time")) or "17-21",
            "user_key": user.key,
            "user_label": user.label,
            "success_target": "",
            "result": "运行中",
            "status": "running",
            "command_label": command_label,
        }
        with self.lock:
            records = self._read_unlocked()
            records.append(record)
            self._write_unlocked(records)
        return record_id

    def create_exact(self, payload: dict[str, Any], result: dict[str, Any], user: UserAccount) -> str:
        now = time.time()
        record_id = str(int(now * 1000))
        slots = payload.get("slots") if isinstance(payload.get("slots"), list) else []
        target_date = clean_string(slots[0].get("date")) if slots and isinstance(slots[0], dict) else ""
        target_time = "；".join(
            slot_label(slot)
            for slot in slots
            if isinstance(slot, dict)
        )
        record = {
            "id": record_id,
            "job_id": "",
            "requested_at": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(now)),
            "requested_ts": now,
            "target_date": target_date,
            "target_time": target_time,
            "user_key": user.key,
            "user_label": user.label,
            "success_target": "；".join(result.get("success_targets") or []),
            "result": clean_string(result.get("result_label")) or "失败",
            "status": clean_string(result.get("status")) or "failed",
            "command_label": "exact_booking",
            "finished_at": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(now)),
            "detail": exact_history_detail(result),
        }
        with self.lock:
            records = self._read_unlocked()
            records.append(record)
            self._write_unlocked(records)
        return record_id

    def finish(self, job: BookingJob) -> None:
        summary = summarize_job_history(job)
        with self.lock:
            records = self._read_unlocked()
            for record in records:
                if record.get("id") == job.history_id:
                    record.update(summary)
                    break
            self._write_unlocked(records)

    def mark_orphaned_running(self, active_job_ids: set[int]) -> None:
        now_text = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
        changed = False
        with self.lock:
            records = self._read_unlocked()
            for record in records:
                if clean_string(record.get("status")) != "running":
                    continue
                job_id = int_or_default(record.get("job_id"), -1)
                if job_id in active_job_ids:
                    continue
                record["status"] = "orphaned"
                record["result"] = "已失联"
                record["finished_at"] = now_text
                record["note"] = "Web 服务已重启或后台进程已结束，任务不再可停止。"
                changed = True
            if changed:
                self._write_unlocked(records)

    def _read_unlocked(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return []
        if not isinstance(data, list):
            return []
        return [item for item in data if isinstance(item, dict)]

    def _write_unlocked(self, records: list[dict[str, Any]]) -> None:
        records = self._retained_records(records)
        records.sort(key=lambda item: self._record_ts(item))
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = self.path.with_suffix(".tmp")
        tmp_path.write_text(json.dumps(records[-200:], ensure_ascii=False, indent=2), encoding="utf-8")
        tmp_path.replace(self.path)

    def _retained_records(self, records: list[dict[str, Any]]) -> list[dict[str, Any]]:
        cutoff = time.time() - LOG_RETENTION_SECONDS
        return [record for record in records if self._record_ts(record) >= cutoff]

    def _record_ts(self, record: dict[str, Any]) -> float:
        return numeric_ts(record.get("requested_ts")) or parse_log_datetime(record.get("requested_at")) or parse_log_datetime(record.get("finished_at")) or 0.0


class JobManager:
    def __init__(self, config: ServerConfig, history: BookingHistoryStore):
        self.config = config
        self.history = history
        self.lock = threading.Lock()
        self.jobs: dict[int, BookingJob] = {}
        self.next_id = 1

    def start(self, payload: dict[str, Any], user: UserAccount, card_index: str) -> BookingJob:
        with self.lock:
            command, command_label = build_booking_command(payload)
            env = os.environ.copy()
            env["DAYDAYUP_TOKEN"] = user.token
            env["DAYDAYUP_JSESSIONID"] = user.jsessionid
            env["DAYDAYUP_CARD_INDEX"] = card_index

            process = subprocess.Popen(
                command,
                cwd=ROOT,
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
            )
            job = BookingJob(self.next_id, process, time.time(), command_label, "")
            job.history_id = self.history.create(payload, job.id, command_label, user)
            self.next_id += 1
            self.jobs[job.id] = job
            self._trim_locked()
            threading.Thread(target=self._read_output, args=(job,), daemon=True).start()
            return job

    def snapshot(self) -> dict[str, Any]:
        with self.lock:
            for job in self.jobs.values():
                self._poll_locked(job)
            self._trim_locked()
            jobs = sorted(self.jobs.values(), key=lambda item: item.id)
            active_jobs = [job for job in jobs if job.status in {"running", "stopping"}]
            selected_job = active_jobs[-1] if active_jobs else (jobs[-1] if jobs else None)
            return {
                "running": bool(active_jobs),
                "active_count": len(active_jobs),
                "job": serialize_job(selected_job) if selected_job else None,
                "jobs": [serialize_job(job) for job in jobs],
            }

    def stop(self) -> dict[str, Any]:
        with self.lock:
            stoppable_jobs = [job for job in self.jobs.values() if job.status in {"running", "stopping"}]
            if not stoppable_jobs:
                return {"stopped": False, "message": "no running job"}
            for job in stoppable_jobs:
                if job.process.poll() is None:
                    job.process.terminate()
                job.status = "stopping"
            return {"stopped": True, "stopped_count": len(stoppable_jobs)}

    def _read_output(self, job: BookingJob) -> None:
        assert job.process.stdout is not None
        for line in job.process.stdout:
            cleaned = line.rstrip("\n")
            with self.lock:
                job.lines.append(cleaned)
                if len(job.lines) > 500:
                    job.lines = job.lines[-500:]
        with self.lock:
            self._poll_locked(job, finalize_history=True)

    def _poll_locked(self, job: BookingJob, finalize_history: bool = False) -> None:
        returncode = job.process.poll()
        if returncode is None:
            return
        job.returncode = returncode
        if job.status == "stopping":
            job.status = "stopped"
        elif job.status == "running" and returncode == 0:
            job.status = "completed"
        elif job.status == "running":
            job.status = "failed"
        if finalize_history and not job.history_finalized:
            self.history.finish(job)
            job.history_finalized = True

    def _trim_locked(self) -> None:
        if len(self.jobs) <= 20:
            return
        active_ids = {job.id for job in self.jobs.values() if job.status in {"running", "stopping"}}
        retained_ids = set(active_ids)
        completed_jobs = [job for job in self.jobs.values() if job.id not in active_ids]
        retained_ids.update(job.id for job in sorted(completed_jobs, key=lambda item: item.id)[-20:])
        self.jobs = {job_id: job for job_id, job in self.jobs.items() if job_id in retained_ids}

    def active_job_ids(self) -> set[int]:
        with self.lock:
            for job in self.jobs.values():
                self._poll_locked(job)
            return {job.id for job in self.jobs.values() if job.status in {"running", "stopping"}}


class FileLock:
    def __init__(self, handle: Any):
        self.handle = handle

    def __enter__(self) -> "FileLock":
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        try:
            fcntl.flock(self.handle, fcntl.LOCK_UN)
        finally:
            self.handle.close()


class JsonStore:
    def __init__(self, path: Path, default: Any):
        self.path = path
        self.default = default
        self.lock = threading.Lock()

    def read(self) -> Any:
        with self.lock:
            with self._file_lock_unlocked():
                return self._read_unlocked()

    def write(self, value: Any) -> None:
        with self.lock:
            with self._file_lock_unlocked():
                self._write_unlocked(value)

    def _file_lock_unlocked(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        lock_path = self.path.with_suffix(self.path.suffix + ".lock")
        handle = lock_path.open("a+", encoding="utf-8")
        fcntl.flock(handle, fcntl.LOCK_EX)
        return FileLock(handle)

    def _read_unlocked(self) -> Any:
        if not self.path.exists():
            return json.loads(json.dumps(self.default))
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return json.loads(json.dumps(self.default))

    def _write_unlocked(self, value: Any) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = self.path.with_suffix(".tmp")
        tmp_path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp_path.replace(self.path)


class ScanTaskStore(JsonStore):
    def __init__(self, path: Path):
        super().__init__(path, {"tasks": []})

    def list(self) -> list[dict[str, Any]]:
        data = self.read()
        tasks = data.get("tasks", []) if isinstance(data, dict) else []
        return [task for task in tasks if isinstance(task, dict)]

    def save_all(self, tasks: list[dict[str, Any]]) -> None:
        self.write({"tasks": tasks[-200:]})

    def mutate(self, update: Any) -> Any:
        with self.lock:
            with self._file_lock_unlocked():
                data = self._read_unlocked()
                tasks = data.get("tasks", []) if isinstance(data, dict) else []
                tasks = [task for task in tasks if isinstance(task, dict)]
                result = update(tasks)
                if result is not False:
                    self._write_unlocked({"tasks": tasks[-200:]})
                return result


class ScanEventStore(JsonStore):
    def __init__(self, path: Path):
        super().__init__(path, {"events": []})

    def list(self, limit: int = 80, window_hours: int | None = None) -> list[dict[str, Any]]:
        with self.lock:
            with self._file_lock_unlocked():
                data = self._read_unlocked()
                events = data.get("events", []) if isinstance(data, dict) else []
                events = [event for event in events if isinstance(event, dict)]
                retained = self._retained_events(events)
                if len(retained) != len(events):
                    self._write_unlocked({"events": retained[-500:]})
                valid = retained
        cutoff = log_window_cutoff_ts(window_hours)
        if cutoff is not None:
            valid = [event for event in valid if self._event_ts(event) >= cutoff]
        valid.sort(key=lambda item: item.get("created_ts", 0), reverse=True)
        return valid[:limit]

    def append(self, event: dict[str, Any]) -> None:
        with self.lock:
            with self._file_lock_unlocked():
                data = self._read_unlocked()
                events = data.get("events", []) if isinstance(data, dict) else []
                events = [item for item in events if isinstance(item, dict)]
                events.append(event)
                events = self._retained_events(events)
                self._write_unlocked({"events": events[-500:]})

    def recent_important(self, since_ts: float) -> list[dict[str, Any]]:
        events = self.list(limit=500)
        return [
            event
            for event in events
            if event.get("important") and event.get("created_ts", 0) >= since_ts and event.get("type") != "daily_summary"
        ]

    def has_summary_for(self, day_value: str) -> bool:
        return any(
            event.get("type") in {"daily_summary", "daily_summary_failed"} and event.get("summary_date") == day_value
            for event in self.list(limit=120)
        )

    def _retained_events(self, events: list[dict[str, Any]]) -> list[dict[str, Any]]:
        cutoff = time.time() - LOG_RETENTION_SECONDS
        return [event for event in events if self._event_ts(event) >= cutoff]

    def _event_ts(self, event: dict[str, Any]) -> float:
        return numeric_ts(event.get("created_ts")) or parse_log_datetime(event.get("created_at")) or 0.0


class ScanTaskManager:
    def __init__(self, app: "WebConsole", *, start_worker: bool = True):
        self.app = app
        self.tasks = ScanTaskStore(SCAN_TASKS_PATH)
        self.events = ScanEventStore(SCAN_EVENTS_PATH)
        self.lock = threading.Lock()
        self.stop_event = threading.Event()
        self.thread: threading.Thread | None = None
        if start_worker:
            self.start()

    def start(self) -> None:
        if self.thread and self.thread.is_alive():
            return
        self.thread = threading.Thread(target=self._loop, daemon=True)
        self.thread.start()

    def close(self) -> None:
        self.stop_event.set()
        if self.thread:
            self.thread.join(timeout=2)

    def snapshot(self, user_key: str = "", event_window_hours: int = LOG_DEFAULT_WINDOW_HOURS) -> dict[str, Any]:
        tasks = self.tasks.list()
        if user_key:
            tasks = [task for task in tasks if clean_string(task.get("user_key")) == user_key]
        events = self.events.list(limit=80, window_hours=event_window_hours)
        if user_key:
            events = [
                event
                for event in events
                if not clean_string(event.get("user_key")) or clean_string(event.get("user_key")) == user_key
            ]
        return {
            "tasks": tasks,
            "events": events,
            "updated_at": time.time(),
        }

    def create(self, payload: dict[str, Any]) -> dict[str, Any]:
        user = self.app.users.get_user(clean_string(payload.get("user_key")))
        task = normalize_scan_task(payload, user)
        now = datetime.now()
        task["created_at"] = format_datetime(now)
        task["updated_at"] = task["created_at"]
        task["next_scan_at"] = format_datetime(next_scan_time_for_task(task, now))
        def append_task(tasks: list[dict[str, Any]]) -> None:
            tasks.append(task)

        with self.lock:
            self.tasks.mutate(append_task)
        self.record_event(
            task,
            "task_created",
            "扫描任务已创建",
            scan_task_summary(task),
            important=False,
            send_mail=False,
        )
        return {"task": task, "tasks": self.snapshot(user.key)["tasks"]}

    def update(self, payload: dict[str, Any]) -> dict[str, Any]:
        task_id = clean_string(payload.get("id"))
        action = clean_string(payload.get("action"))
        if action not in {"pause", "resume", "stop"}:
            raise EasySerpError("invalid scan task action")
        def update_task(tasks: list[dict[str, Any]]) -> dict[str, Any]:
            task = next((item for item in tasks if item.get("id") == task_id), None)
            if not task:
                raise EasySerpError("scan task was not found")
            if action == "pause":
                if task.get("status") != "active":
                    raise EasySerpError("only active scan tasks can be paused")
                task["status"] = "paused"
            elif action == "resume":
                if task.get("status") != "paused":
                    raise EasySerpError("only paused scan tasks can be resumed")
                task["status"] = "active"
                task["next_scan_at"] = format_datetime(next_scan_time_for_task(task, datetime.now()))
            elif action == "stop":
                if task.get("status") not in {"active", "paused"}:
                    raise EasySerpError("only active or paused scan tasks can be stopped")
                task["status"] = "stopped"
            task["updated_at"] = format_datetime(datetime.now())
            return task

        with self.lock:
            task = self.tasks.mutate(update_task)
        self.record_event(
            task,
            f"task_{action}",
            scan_action_label(action),
            scan_task_summary(task),
            important=False,
            send_mail=False,
        )
        return {"task": task, "tasks": self.snapshot(clean_string(task.get("user_key")))["tasks"]}

    def _loop(self) -> None:
        while not self.stop_event.wait(30):
            self.run_pending_cycle()

    def run_forever(self, interval_seconds: float = 30.0) -> None:
        while True:
            self.run_pending_cycle()
            time.sleep(interval_seconds)

    def run_pending_cycle(self) -> None:
        try:
            self.scan_due_tasks()
            self.send_daily_summary_if_due()
        except Exception as exc:
            self.events.append(make_scan_event(None, "scan_loop_error", "扫描循环异常", redact_sensitive_text(exc), important=True))

    def scan_due_tasks(self, now: datetime | None = None) -> None:
        now = now or datetime.now()
        def scan_tasks(tasks: list[dict[str, Any]]) -> bool:
            changed = False
            for task in tasks:
                if task.get("status") != "active":
                    continue
                due_at = parse_datetime(clean_string(task.get("next_scan_at"))) or now
                if due_at > now:
                    continue
                self.process_task(task, now)
                changed = True
            return changed

        with self.lock:
            self.tasks.mutate(scan_tasks)

    def process_task(self, task: dict[str, Any], now: datetime) -> None:
        if quiet_window_active(now):
            task["next_scan_at"] = format_datetime(next_after_quiet_window(now))
            task["updated_at"] = format_datetime(now)
            return

        if task_completion_ready(task, now):
            task["status"] = "completed"
            task["completed_at"] = format_datetime(now)
            task["updated_at"] = format_datetime(now)
            self.record_event(task, "task_completed", "扫描任务已完成", scan_task_summary(task))
            return

        unfinished = [target for target in task.get("targets", []) if target.get("status") not in {"booked", "expired"}]
        if not task_satisfied(task) and unfinished and all(target_in_lockout(target, now) for target in unfinished):
            for target in unfinished:
                target["status"] = "expired"
                target["updated_at"] = format_datetime(now)
            task["status"] = "expired"
            task["updated_at"] = format_datetime(now)
            self.record_event(task, "task_expired", "扫描任务已退出", "所有未完成目标已进入 24 小时禁止区。")
            return

        actionable = [
            target
            for target in task.get("targets", [])
            if scan_target_actionable(target, now, allow_booked=bool(task.get("iterative_optimization")))
        ]
        if task.get("success_mode") == "any" and task_satisfied(task):
            actionable = [target for target in actionable if target.get("status") == "booked"]
        if not actionable:
            task["next_scan_at"] = format_datetime(next_scan_time_for_task(task, now))
            task["updated_at"] = format_datetime(now)
            return

        user = self.app.users.get_user(clean_string(task.get("user_key")))
        client = self.app.client(user)
        for target in actionable:
            self.process_target(task, target, user, client, now)
            if task_completion_ready(task, now):
                task["status"] = "completed"
                task["completed_at"] = format_datetime(now)
                self.record_event(task, "task_completed", "扫描任务已完成", scan_task_summary(task))
                break
        if task.get("status") == "active":
            task["next_scan_at"] = format_datetime(next_scan_time_for_task(task, now))
        task["updated_at"] = format_datetime(now)

    def process_target(
        self,
        task: dict[str, Any],
        target: dict[str, Any],
        user: UserAccount,
        client: EasySerpClient,
        now: datetime,
    ) -> None:
        places = self.app._fetch_places(client, user, target["date"])
        candidates = scan_candidates_for_target(task, target, places)
        if not candidates:
            target["last_scan_at"] = format_datetime(now)
            return
        best = candidates[0]
        current_slots = [slot for slot in target.get("booked_slots", []) if isinstance(slot, dict)]
        required_slots = scan_target_required_slots(target)
        if current_slots and not task.get("iterative_optimization"):
            if len(current_slots) >= required_slots:
                target["status"] = "booked"
                target["last_scan_at"] = format_datetime(now)
                return
            current_candidate = scan_candidate_with_current_slots(candidates, current_slots)
            if current_candidate:
                missing = scan_missing_slots(current_candidate["slots"], current_slots)
                if missing and len(current_slots) + len(missing) <= required_slots:
                    self.book_candidate(
                        task,
                        target,
                        {"slots": missing},
                        user,
                        now,
                        event_type="scan_booking_success",
                        existing_slots=current_slots,
                    )
                    return
            target["last_scan_at"] = format_datetime(now)
            return
        if current_slots and task.get("iterative_optimization"):
            if not scan_candidate_better(best, current_slots):
                target["last_scan_at"] = format_datetime(now)
                return
            self.optimize_target(task, target, best, user, now)
            return
        self.book_candidate(task, target, best, user, now, event_type="scan_booking_success")

    def book_candidate(
        self,
        task: dict[str, Any],
        target: dict[str, Any],
        candidate: dict[str, Any],
        user: UserAccount,
        now: datetime,
        event_type: str,
        existing_slots: list[dict[str, Any]] | None = None,
    ) -> None:
        payload = {"user_key": user.key, "slots": candidate["slots"]}
        result = self.app.book_exact(payload)
        successes = result.get("successes") or []
        failures = result.get("failures") or []
        is_rebook = event_type == "scan_rebook_success"
        if successes:
            success_slots = [success.get("slot") for success in successes if isinstance(success.get("slot"), dict)]
            target["booked_slots"] = merge_scan_slots(existing_slots or [], success_slots)
            target["status"] = "booked" if len(target["booked_slots"]) >= scan_target_required_slots(target) and not failures else "partial"
            target["last_decision_at"] = format_datetime(now)
            self.record_event(
                task,
                event_type,
                ("扫描重约成功" if is_rebook else "扫描预约成功")
                if not failures
                else ("扫描重约部分成功" if is_rebook else "扫描预约部分成功"),
                scan_booking_message(target, successes, failures),
            )
        if failures and not successes:
            target["status"] = "failed"
            target["last_decision_at"] = format_datetime(now)
            self.record_event(
                task,
                "scan_rebook_failed" if is_rebook else "scan_booking_failed",
                "扫描重约失败" if is_rebook else "扫描预约失败",
                scan_booking_message(target, successes, failures),
            )

    def optimize_target(
        self,
        task: dict[str, Any],
        target: dict[str, Any],
        candidate: dict[str, Any],
        user: UserAccount,
        now: datetime,
    ) -> None:
        old_slots = [slot for slot in target.get("booked_slots", []) if isinstance(slot, dict)]
        new_slots = candidate["slots"]
        old_by_key = {scan_slot_identity(slot): slot for slot in old_slots}
        new_by_key = {scan_slot_identity(slot): slot for slot in new_slots}
        to_cancel = [slot for key, slot in old_by_key.items() if key not in new_by_key]
        to_book = [slot for key, slot in new_by_key.items() if key not in old_by_key]

        for slot in to_cancel:
            bill_num = clean_string(slot.get("bill_num"))
            if not bill_num:
                self.record_event(task, "scan_rebook_failed", "扫描重约失败", "缺少可取消的 bill 编号。")
                return
            try:
                cancel_result = self.app.cancel(
                    {
                        "user_key": user.key,
                        "bill_num": bill_num,
                        "confirmation": "CANCEL",
                        "reason": "scan optimization",
                        "require_confirmed": True,
                    }
                )
                if not cancel_result.get("confirmed"):
                    raise EasySerpError("cancel was not confirmed")
                self.record_event(task, "scan_cancel_success", "扫描取消成功", f"{slot_label(slot)} bill={bill_num}")
            except EasySerpError as exc:
                self.record_event(task, "scan_cancel_failed", "扫描取消失败", redact_sensitive_text(exc))
                return

        preserved = [slot for key, slot in old_by_key.items() if key in new_by_key]
        target["booked_slots"] = preserved
        if to_book:
            self.book_candidate(
                task,
                target,
                {"slots": to_book},
                user,
                now,
                event_type="scan_rebook_success",
                existing_slots=preserved,
            )
        else:
            target["booked_slots"] = preserved
        target["status"] = "booked" if len(target.get("booked_slots") or []) >= scan_target_required_slots(target) else "partial"

    def record_event(
        self,
        task: dict[str, Any] | None,
        event_type: str,
        title: str,
        message: str,
        *,
        important: bool = True,
        send_mail: bool = True,
    ) -> None:
        event = make_scan_event(task, event_type, title, message, important=important)
        self.events.append(event)
        if important and send_mail:
            try:
                send_scan_email(title, message)
            except Exception as exc:
                failed = make_scan_event(task, "scan_mail_failed", "扫描邮件发送失败", redact_sensitive_text(exc), important=False)
                self.events.append(failed)

    def send_daily_summary_if_due(self, now: datetime | None = None) -> None:
        now = now or datetime.now()
        if now.time() < SCAN_SUMMARY_TIME:
            return
        day_value = now.strftime("%Y-%m-%d")
        if self.events.has_summary_for(day_value):
            return
        important = self.events.recent_important((now - timedelta(hours=24)).timestamp())
        if not important:
            return
        body = "\n".join(format_scan_event_line(event) for event in reversed(important))
        try:
            send_scan_email("Daydayup 扫描任务每日摘要", body)
        except Exception as exc:
            event = make_scan_event(None, "daily_summary_failed", "扫描任务每日摘要发送失败", redact_sensitive_text(exc), important=False)
            event["summary_date"] = day_value
            self.events.append(event)
            return
        event = make_scan_event(None, "daily_summary", "扫描任务每日摘要已发送", body, important=False)
        event["summary_date"] = day_value
        self.events.append(event)


def build_booking_command(payload: dict[str, Any]) -> tuple[list[str], str]:
    command = [sys.executable, str(BOOKING_SCRIPT)]
    labels: list[str] = []

    date_value = clean_string(payload.get("date"))
    in_days = clean_string(payload.get("in_days"))
    if date_value:
        command.extend(["-d", date_value])
        labels.append(f"date={date_value}")
    elif in_days:
        command.extend(["--in-days", in_days])
        labels.append(f"in_days={in_days}")
    else:
        command.extend(["--in-days", "4"])
        labels.append("in_days=4")

    time_range = clean_string(payload.get("time")) or "17-21"
    duration = clean_string(payload.get("duration")) or "1"
    command.extend(["-t", time_range, "--duration", duration])
    labels.extend([f"time={time_range}", f"duration={duration}"])

    booking_mode = clean_string(payload.get("booking_mode")) or "balanced"
    if booking_mode not in BOOKING_MODES:
        raise EasySerpError("invalid booking mode")
    command.extend(["--booking-mode", booking_mode])
    labels.append(f"mode={booking_mode}")

    for flag, key in (("-p", "priority"), ("--backup", "backup")):
        values = parse_int_list(payload.get(key))
        if values:
            command.append(flag)
            command.extend(str(value) for value in values)
            labels.append(f"{key}={','.join(str(value) for value in values)}")

    force = bool(payload.get("force", False))
    dry_run = bool(payload.get("dry_run", False))
    all_court = bool(payload.get("all_court", False))
    if force:
        command.append("--force")
        labels.append("force")
    if dry_run:
        command.append("--dry-run")
        labels.append("dry_run")
    if all_court:
        command.append("--all-court")
        labels.append("all_court")

    for cli_name, payload_key, default_value in (
        ("--window-seconds", "window_seconds", "60"),
        ("--poll-interval", "poll_interval", "0.08"),
        ("--direct-spec-adjacent-delay", "direct_spec_adjacent_delay", "0.2"),
        ("--guide-interval", "guide_interval", "0.5"),
        ("--guide-max-inflight", "guide_max_inflight", "4"),
        ("--error-backoff", "error_backoff", "0.25"),
    ):
        value = clean_string(payload.get(payload_key)) or default_value
        command.extend([cli_name, value])

    return command, " ".join(labels)


def parse_int_list(value: Any) -> list[int]:
    if value is None:
        return []
    if isinstance(value, list):
        raw_values = value
    else:
        raw_values = str(value).replace(",", " ").split()
    result: list[int] = []
    for item in raw_values:
        try:
            result.append(int(item))
        except (TypeError, ValueError):
            continue
    return result


def clean_string(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def env_flag(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def clean_user_key(value: Any) -> str:
    text = clean_string(value).lower()
    text = re.sub(r"[^a-z0-9_]+", "_", text)
    return re.sub(r"_+", "_", text).strip("_")


def single_query(query: dict[str, list[str]], key: str) -> str:
    return clean_string(query.get(key, [""])[0])


def extract_oauth_code(payload: dict[str, Any]) -> str:
    code = clean_string(payload.get("code"))
    if code:
        return code
    redirect_url = clean_string(payload.get("redirect_url"))
    if not redirect_url:
        return ""
    return clean_string(parse_qs(urlsplit(redirect_url).query).get("code", [""])[0])


def serialize_job(job: BookingJob) -> dict[str, Any]:
    return {
        "id": job.id,
        "status": job.status,
        "returncode": job.returncode,
        "started_at": job.started_at,
        "command_label": job.command_label,
        "lines": job.lines[-180:],
    }


def target_date_from_payload(payload: dict[str, Any]) -> str:
    date_value = clean_string(payload.get("date"))
    if date_value:
        return date_value
    in_days = clean_string(payload.get("in_days"))
    try:
        offset = int(in_days) if in_days else 4
    except ValueError:
        offset = 4
    return (date.today() + timedelta(days=offset)).strftime("%Y-%m-%d")


def summarize_job_history(job: BookingJob) -> dict[str, Any]:
    success_targets = []
    for line in job.lines:
        match = re.search(r"\[成功\].*?日期=([^|]+)\s*\|\s*时段=([^|]+)\s*\|\s*场地=([^|\n]+)", line)
        if not match:
            continue
        target = f"{match.group(3).strip()} {match.group(2).strip()}"
        if target not in success_targets:
            success_targets.append(target)

    has_dry_run = any("[dry-run]" in line for line in job.lines)
    has_no_hit = any("[结束] 第一阶段未抢到" in line for line in job.lines)
    has_partial = any("[结束] 第一阶段成功，但第二阶段未抢到相邻小时" in line for line in job.lines)

    if job.status == "stopped":
        result = "已停止"
    elif success_targets and has_partial:
        result = "部分成功"
    elif success_targets:
        result = "成功"
    elif has_dry_run:
        result = "演练完成"
    elif has_no_hit:
        result = "未抢到"
    elif job.returncode == 0:
        result = "完成"
    else:
        result = "失败"

    return {
        "result": result,
        "status": job.status,
        "returncode": job.returncode,
        "success_target": "；".join(success_targets),
        "finished_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }


def normalize_scan_task(payload: dict[str, Any], user: UserAccount) -> dict[str, Any]:
    targets = normalize_scan_targets(payload.get("targets"))
    interval = int_or_default(payload.get("scan_interval_minutes"), SCAN_DEFAULT_INTERVAL_MINUTES)
    interval = max(SCAN_MIN_INTERVAL_MINUTES, min(SCAN_MAX_INTERVAL_MINUTES, interval))
    success_mode = clean_string(payload.get("success_mode")) or "any"
    if success_mode not in {"any", "all"}:
        raise EasySerpError("invalid success mode")
    court_mode = clean_string(payload.get("court_mode")) or "selected"
    if court_mode not in {"selected", "all"}:
        raise EasySerpError("invalid court mode")
    selected_courts = parse_int_list(payload.get("selected_courts"))
    if not selected_courts:
        selected_courts = SAFE_COURTS[:]
    selected_courts = [court for court in selected_courts if court in ALL_COURTS]
    if court_mode == "selected" and not selected_courts:
        raise EasySerpError("selected courts are required")
    now_ms = int(time.time() * 1000)
    name = clean_string(payload.get("name")) or f"扫描任务 {now_ms}"
    return {
        "id": f"scan_{now_ms}",
        "name": name,
        "user_key": user.key,
        "user_label": user.label,
        "status": "active",
        "targets": targets,
        "success_mode": success_mode,
        "scan_interval_minutes": interval,
        "court_mode": court_mode,
        "selected_courts": selected_courts,
        "same_court_required": bool(payload.get("same_court_required", False)),
        "iterative_optimization": bool(payload.get("iterative_optimization", False)),
    }


def normalize_scan_targets(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list) or not value:
        raise EasySerpError("scan targets are required")
    result: list[dict[str, Any]] = []
    for index, item in enumerate(value, start=1):
        if not isinstance(item, dict):
            raise EasySerpError("scan target must be an object")
        date_value = clean_string(item.get("date"))
        try:
            date.fromisoformat(date_value)
        except ValueError as exc:
            raise EasySerpError("invalid scan target date") from exc
        start_time = normalize_slot_time(item.get("start_time"))
        end_time = normalize_slot_time(item.get("end_time"))
        if end_time <= start_time:
            raise EasySerpError("scan target end time must be after start time")
        if hour_from_time(end_time) - hour_from_time(start_time) < 1:
            raise EasySerpError("scan target must include at least one hour")
        result.append(
            {
                "id": f"target_{index}",
                "date": date_value,
                "start_time": start_time,
                "end_time": end_time,
                "status": "pending",
                "booked_slots": [],
            }
        )
    return result


def int_or_default(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def parse_log_window_hours(value: Any) -> int:
    hours = int_or_default(value, LOG_DEFAULT_WINDOW_HOURS)
    return hours if hours in LOG_WINDOW_CHOICES_HOURS else LOG_DEFAULT_WINDOW_HOURS


def log_window_cutoff_ts(window_hours: int | None) -> float | None:
    if window_hours is None:
        return None
    return time.time() - max(0, window_hours) * 60 * 60


def numeric_ts(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def parse_log_datetime(value: Any) -> float:
    text = clean_string(value)
    if not text:
        return 0.0
    try:
        return datetime.strptime(text, "%Y-%m-%d %H:%M:%S").timestamp()
    except ValueError:
        return 0.0


def parse_datetime(value: str) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None


def format_datetime(value: datetime) -> str:
    return value.strftime("%Y-%m-%d %H:%M:%S")


def target_start_datetime(target: dict[str, Any]) -> datetime:
    return datetime.strptime(f"{target['date']} {target['start_time']}", "%Y-%m-%d %H:%M")


def target_release_datetime(target: dict[str, Any]) -> datetime:
    target_day = date.fromisoformat(target["date"])
    release_day = target_day - timedelta(days=5)
    return datetime.combine(release_day, SCAN_SILENT_END)


def target_in_lockout(target: dict[str, Any], now: datetime) -> bool:
    return target_start_datetime(target) - now <= timedelta(hours=24)


def quiet_window_active(now: datetime) -> bool:
    current = now.time()
    return SCAN_SILENT_START <= current < SCAN_SILENT_END


def next_after_quiet_window(now: datetime) -> datetime:
    return datetime.combine(now.date(), SCAN_SILENT_END) + timedelta(seconds=1)


def scan_target_actionable(target: dict[str, Any], now: datetime, *, allow_booked: bool = False) -> bool:
    if target.get("status") in {"expired"}:
        return False
    if target.get("status") == "booked" and not allow_booked:
        return False
    if target_start_datetime(target) - now <= timedelta(hours=24):
        return False
    return now >= target_release_datetime(target)


def next_scan_time_for_task(task: dict[str, Any], now: datetime) -> datetime:
    if quiet_window_active(now):
        return next_after_quiet_window(now)
    interval = int_or_default(task.get("scan_interval_minutes"), SCAN_DEFAULT_INTERVAL_MINUTES)
    interval = max(SCAN_MIN_INTERVAL_MINUTES, min(SCAN_MAX_INTERVAL_MINUTES, interval))
    candidates = []
    for target in task.get("targets", []):
        if not isinstance(target, dict) or target.get("status") == "expired":
            continue
        if target.get("status") == "booked" and not task.get("iterative_optimization"):
            continue
        if target_in_lockout(target, now):
            continue
        candidates.append(max(now + timedelta(minutes=interval), target_release_datetime(target)))
    if not candidates:
        return now + timedelta(minutes=interval)
    candidate = min(candidates)
    if quiet_window_active(candidate):
        return next_after_quiet_window(candidate)
    return candidate


def task_satisfied(task: dict[str, Any]) -> bool:
    targets = [target for target in task.get("targets", []) if isinstance(target, dict)]
    booked = [target for target in targets if target.get("status") == "booked"]
    if task.get("success_mode") == "all":
        return bool(targets) and len(booked) == len(targets)
    return bool(booked)


def task_completion_ready(task: dict[str, Any], now: datetime) -> bool:
    if not task_satisfied(task):
        return False
    if not task.get("iterative_optimization"):
        return True
    booked = [target for target in task.get("targets", []) if isinstance(target, dict) and target.get("status") == "booked"]
    return bool(booked) and all(target_in_lockout(target, now) for target in booked)


def scan_court_pool(task: dict[str, Any]) -> set[int]:
    if task.get("court_mode") == "all":
        return set(ALL_COURTS)
    values = parse_int_list(task.get("selected_courts")) or SAFE_COURTS
    return {court for court in values if court in ALL_COURTS}


def scan_candidates_for_target(
    task: dict[str, Any],
    target: dict[str, Any],
    places: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    day = serialize_availability_day(target["date"], places)
    hours = [
        hour
        for hour in day.get("hours", [])
        if clean_string(hour.get("start_time")) >= target["start_time"] and clean_string(hour.get("end_time")) <= target["end_time"]
    ]
    hours.sort(key=lambda item: clean_string(item.get("start_time")))
    pool = scan_court_pool(task)
    candidates: list[dict[str, Any]] = []
    if scan_target_required_slots(target) == 1:
        for hour in hours:
            for court in hour.get("courts", []):
                if court.get("number") not in pool:
                    continue
                slots = [scan_slot_from_parts(day, hour, court)]
                candidates.append({"slots": slots, "score": scan_candidate_score(slots)})
        candidates.sort(key=lambda item: item["score"], reverse=True)
        return candidates
    for left, right in zip(hours, hours[1:]):
        if left.get("end_time") != right.get("start_time"):
            continue
        left_courts = [court for court in left.get("courts", []) if court.get("number") in pool]
        right_courts = [court for court in right.get("courts", []) if court.get("number") in pool]
        if task.get("same_court_required"):
            pairs = [(a, b) for a in left_courts for b in right_courts if a.get("id") == b.get("id")]
        else:
            pairs = [(a, b) for a in left_courts for b in right_courts]
        for first, second in pairs:
            slots = [scan_slot_from_parts(day, left, first), scan_slot_from_parts(day, right, second)]
            candidates.append({"slots": slots, "score": scan_candidate_score(slots)})
    candidates.sort(key=lambda item: item["score"], reverse=True)
    return candidates


def scan_target_required_slots(target: dict[str, Any]) -> int:
    duration_hours = hour_from_time(clean_string(target.get("end_time"))) - hour_from_time(clean_string(target.get("start_time")))
    return 1 if duration_hours <= 1 else 2


def scan_slot_from_parts(day: dict[str, Any], hour: dict[str, Any], court: dict[str, Any]) -> dict[str, Any]:
    return {
        "date": day["date"],
        "time": hour["time"],
        "start_time": court.get("start_time") or hour.get("start_time"),
        "end_time": court.get("end_time") or hour.get("end_time"),
        "id": court["id"],
        "name": court["name"],
        "number": court.get("number"),
        "wall": bool(court.get("wall")),
        "price_value": float_or_zero(court.get("price_value")),
        "pay_value": float_or_zero(court.get("pay_value")),
    }


def scan_candidate_score(slots: list[dict[str, Any]]) -> tuple[int, int, int]:
    start_hours = [hour_from_time(slot["start_time"]) for slot in slots]
    same_court = 1 if len({slot.get("id") for slot in slots}) == 1 else 0
    court_penalty = sum(scan_court_rank(slot.get("number")) for slot in slots)
    return (max(start_hours), same_court, -court_penalty)


def scan_court_rank(value: Any) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return 999
    if number in SAFE_COURTS:
        return SAFE_COURTS.index(number)
    if number in ALL_COURTS:
        return 100 + ALL_COURTS.index(number)
    return 999


def scan_candidate_better(candidate: dict[str, Any], current_slots: list[dict[str, Any]]) -> bool:
    return candidate.get("score", scan_candidate_score(candidate.get("slots", []))) > scan_candidate_score(current_slots)


def scan_candidate_with_current_slots(candidates: list[dict[str, Any]], current_slots: list[dict[str, Any]]) -> dict[str, Any] | None:
    current_keys = {scan_slot_identity(slot) for slot in current_slots}
    for candidate in candidates:
        candidate_keys = {scan_slot_identity(slot) for slot in candidate.get("slots", [])}
        if current_keys & candidate_keys:
            return candidate
    return None


def scan_missing_slots(candidate_slots: list[dict[str, Any]], current_slots: list[dict[str, Any]]) -> list[dict[str, Any]]:
    current_keys = {scan_slot_identity(slot) for slot in current_slots}
    return [slot for slot in candidate_slots if scan_slot_identity(slot) not in current_keys]


def merge_scan_slots(existing_slots: list[dict[str, Any]], new_slots: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    for slot in existing_slots + new_slots:
        merged[scan_slot_identity(slot)] = slot
    return sorted(merged.values(), key=lambda item: (clean_string(item.get("date")), clean_string(item.get("start_time"))))


def scan_slot_identity(slot: dict[str, Any]) -> str:
    return "|".join(
        [
            clean_string(slot.get("date")),
            clean_string(slot.get("start_time")),
            clean_string(slot.get("end_time")),
            clean_string(slot.get("id")),
        ]
    )


def scan_task_summary(task: dict[str, Any]) -> str:
    targets = task.get("targets", [])
    done = sum(1 for target in targets if isinstance(target, dict) and target.get("status") == "booked")
    return f"{clean_string(task.get('name'))}：{done}/{len(targets)} 个目标完成"


def scan_booking_message(target: dict[str, Any], successes: list[dict[str, Any]], failures: list[dict[str, Any]]) -> str:
    lines = [f"目标 {target['date']} {target['start_time']}-{target['end_time']}"]
    for item in successes:
        slot = item.get("slot") or {}
        bill_num = clean_string(slot.get("bill_num"))
        suffix = f" bill={bill_num}" if bill_num else ""
        lines.append(f"成功：{slot_label(slot)}{suffix}")
    for item in failures:
        lines.append(f"失败：{clean_string(item.get('error'))}")
    return "\n".join(lines)


def scan_action_label(action: str) -> str:
    return {"pause": "扫描任务已暂停", "resume": "扫描任务已恢复", "stop": "扫描任务已停止"}.get(action, "扫描任务已更新")


def make_scan_event(
    task: dict[str, Any] | None,
    event_type: str,
    title: str,
    message: Any,
    *,
    important: bool,
) -> dict[str, Any]:
    now = datetime.now()
    return {
        "id": f"event_{int(time.time() * 1000)}",
        "type": event_type,
        "title": title,
        "message": clean_string(message),
        "important": important,
        "task_id": clean_string(task.get("id")) if task else "",
        "task_name": clean_string(task.get("name")) if task else "",
        "user_key": clean_string(task.get("user_key")) if task else "",
        "created_at": format_datetime(now),
        "created_ts": now.timestamp(),
    }


def format_scan_event_line(event: dict[str, Any]) -> str:
    task_name = clean_string(event.get("task_name"))
    prefix = f"[{event.get('created_at')}]"
    if task_name:
        prefix = f"{prefix} {task_name}"
    return f"{prefix} {event.get('title')}: {event.get('message')}"


def load_key_value_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip()
    return values


def send_scan_email(subject: str, body: str) -> None:
    config = load_key_value_env(MAIL_CONFIG_PATH)
    host = config.get("SMTP_HOST", "smtp.qq.com")
    port = int(config.get("SMTP_PORT", "465"))
    username = config.get("SMTP_USER", "")
    password = config.get("SMTP_PASSWORD", "")
    recipient = config.get("MAIL_TO", "")
    if not username or not password or not recipient:
        raise EasySerpError(f"mail config is incomplete: {MAIL_CONFIG_PATH}")
    message = MIMEText(body, "plain", "utf-8")
    message["Subject"] = subject
    message["From"] = username
    message["To"] = recipient
    with smtplib.SMTP_SSL(host, port, timeout=20) as server:
        server.login(username, password)
        server.sendmail(username, [recipient], message.as_string())


class WebConsole:
    def __init__(self, config: ServerConfig, users: UserStore, *, start_scan_worker: bool = True):
        self.config = config
        self.users = users
        self.history = BookingHistoryStore(HISTORY_PATH)
        self.jobs = JobManager(config, self.history)
        self.scans = ScanTaskManager(self, start_worker=start_scan_worker)

    def close(self) -> None:
        self.scans.close()

    def client(self, user: UserAccount) -> EasySerpClient:
        return EasySerpClient(
            self.config.base_url,
            user.token,
            user.jsessionid,
            self.config.timeout,
        )

    def auth_login(self, payload: dict[str, Any]) -> dict[str, Any]:
        password = clean_string(payload.get("password"))
        if not self.users.verify_access(password):
            raise EasySerpAuthError("invalid access password")
        return {"ok": True}

    def user_list(self) -> dict[str, Any]:
        users = self.users.list_users()
        default_user = self.users.get_user("")
        return {
            "users": [serialize_user(user) for user in users],
            "default_user_key": default_user.key,
            "updated_at": time.time(),
        }

    def unlock_users(self, payload: dict[str, Any]) -> dict[str, Any]:
        admin_password = clean_string(payload.get("admin_password"))
        if not self.users.verify_admin(admin_password):
            raise EasySerpAuthError("invalid admin password")
        return {"ok": True}

    def save_user(self, payload: dict[str, Any]) -> dict[str, Any]:
        admin_password = clean_string(payload.get("admin_password"))
        if not self.users.verify_admin(admin_password):
            raise EasySerpAuthError("invalid admin password")
        user = self.users.upsert_user(payload)
        return {"user": serialize_user(user), "users": [serialize_user(item) for item in self.users.list_users()]}

    def token_auth_url(self, payload: dict[str, Any]) -> dict[str, Any]:
        admin_password = clean_string(payload.get("admin_password"))
        if not self.users.verify_admin(admin_password):
            raise EasySerpAuthError("invalid admin password")
        club_member_code = clean_string(payload.get("club_member_code")) or "bdyxbtyg7"
        redirect_url = clean_string(payload.get("redirect_url")) or DEFAULT_OAUTH_REDIRECT_URL
        client = EasySerpClient(self.config.base_url, "", "", self.config.timeout)
        data = require_success(
            client.get("wechar/getWXConfigInfo", params={"clubMemberCode": club_member_code}),
            "getWXConfigInfo",
        )
        if not isinstance(data, dict) or not data.get("appid"):
            raise EasySerpError("missing appid")
        auth_url = (
            "https://open.weixin.qq.com/connect/oauth2/authorize"
            f"?appid={quote(clean_string(data.get('appid')), safe='')}"
            f"&redirect_uri={quote(redirect_url, safe='')}"
            "&response_type=code&scope=snsapi_userinfo&state=123#wechat_redirect"
        )
        return {"auth_url": auth_url, "redirect_url": redirect_url, "club_member_code": club_member_code}

    def token_exchange(self, payload: dict[str, Any]) -> dict[str, Any]:
        admin_password = clean_string(payload.get("admin_password"))
        if not self.users.verify_admin(admin_password):
            raise EasySerpAuthError("invalid admin password")
        username = clean_string(payload.get("username"))
        password = clean_string(payload.get("password"))
        code = extract_oauth_code(payload)
        club_member_code = clean_string(payload.get("club_member_code")) or "bdyxbtyg7"
        name = clean_string(payload.get("name")) or "wx"
        if not username:
            raise EasySerpError("username is required")
        if not password:
            raise EasySerpError("password is required")
        if not code:
            raise EasySerpError("oauth code is required")

        client = EasySerpClient(self.config.base_url, "", "", self.config.timeout)
        token = require_success(
            client.get(
                "wechar/member",
                params={"code": code, "clubMemberCode": club_member_code, "name": name},
            ),
            "wechar/member",
        )
        if not isinstance(token, str) or not token:
            raise EasySerpError("token response is empty")
        require_success(
            client.get(
                "wechar/saveClubInfoByToken",
                params={"token": token, "clubMemberCode": club_member_code, "shopNum": self.config.shop_num},
            ),
            "saveClubInfoByToken",
        )
        require_success(
            client.get(
                "memberLogin/logined",
                params={"userName": username, "passWord": password, "token": token},
            ),
            "memberLogin/logined",
        )
        return {"token": token, "credential_status": credential_state(token)}

    def status(self, user_key: str = "") -> dict[str, Any]:
        user = self.users.get_user(user_key)
        return {
            "user": serialize_user(user),
            "token": credential_state(user.token),
            "jsessionid": credential_state(user.jsessionid),
            "card_name": user.card_name,
            "base_url": self.config.base_url,
            "shop_num": self.config.shop_num,
        }

    def cards(self, user_key: str = "") -> dict[str, Any]:
        user = self.users.get_user(user_key)
        data = require_success(
            self.client(user).get(
                "card/getCardByUser",
                params={"shopNum": self.config.shop_num, "token": user.token},
            ),
            "getCardByUser",
        )
        if not isinstance(data, list):
            raise EasySerpError("card response is not a list")
        cards = [serialize_card(card) for card in data]
        primary = select_primary_card(cards, user.card_name)
        return {
            "cards": cards,
            "primary_card": primary,
            "user": serialize_user(user),
            "updated_at": time.time(),
        }

    def bookings(self, user_key: str = "", include_cancelled: bool = False, success_only: bool = False) -> dict[str, Any]:
        user = self.users.get_user(user_key)
        orders = fetch_orders(
            self.client(user),
            token=user.token,
            shop_num=self.config.shop_num,
            page_size=20,
            max_pages=5,
        )
        if success_only or not include_cancelled:
            orders = [order for order in orders if not is_cancelled(order)]
        return {"bookings": [serialize_order(order) for order in orders], "user": serialize_user(user), "updated_at": time.time()}

    def availability(self, user_key: str = "", days: int = 5) -> dict[str, Any]:
        user = self.users.get_user(user_key)
        if not user.token:
            raise EasySerpError("token is required for availability query")
        days = max(1, min(days, 7))
        client = self.client(user)
        results = []
        today = date.today()
        for offset in range(days):
            target_day = today + timedelta(days=offset)
            date_value = target_day.strftime("%Y-%m-%d")
            try:
                payload = client.get(
                    "datediscount/getPlaceInfoByShortNameDiscount",
                    params={
                        "shopNum": self.config.shop_num,
                        "dateymd": date_value,
                        "shortName": DEFAULT_SHORT_NAME,
                        "token": user.token,
                    },
                )
                data = require_success(payload, "getPlaceInfoByShortNameDiscount")
                places = data.get("placeArray", []) if isinstance(data, dict) else []
                results.append(serialize_availability_day(date_value, places))
            except EasySerpError as exc:
                results.append(
                    {
                        "date": date_value,
                        "label": relative_day_label(offset),
                        "total": 0,
                        "hours": [],
                        "error": redact_sensitive_text(exc),
                    }
                )
        return {"days": results, "user": serialize_user(user), "updated_at": time.time()}

    def availability_analytics(
        self,
        *,
        metric: str,
        window_days: int | str = 7,
        start_hour: int | str = availability_analytics.DEFAULT_START_HOUR,
        end_hour: int | str = availability_analytics.DEFAULT_END_HOUR,
        courts: list[int] | None = None,
        slots: list[str] | None = None,
        cache_ttl_seconds: int = availability_analytics.DEFAULT_CACHE_TTL_SECONDS,
    ) -> dict[str, Any]:
        try:
            return availability_analytics.get_analytics(
                metric,
                window_days=window_days,
                start_hour=start_hour,
                end_hour=end_hour,
                courts=courts,
                slots=slots,
                cache_ttl_seconds=cache_ttl_seconds,
            )
        except ValueError as exc:
            raise EasySerpError(str(exc))

    def cancel_preview(self, bill_num: str, user_key: str = "") -> dict[str, Any]:
        user = self.users.get_user(user_key)
        order = self._find_recent_order(bill_num, user)
        if not order:
            raise EasySerpError("bill number was not found in recent bookings")
        if order and is_cancelled(order):
            raise EasySerpError("booking is already cancelled")

        short_name = DEFAULT_SHORT_NAME
        if order:
            short_name = summarize_order(order).short_name or short_name

        refund_rule = None
        if short_name:
            rule_data = require_success(
                self.client(user).get(
                    "common/getRefundTime",
                    params={
                        "shortName": short_name,
                        "shopNum": self.config.shop_num,
                        "token": user.token,
                        "type": "place",
                    },
                ),
                "getRefundTime",
            )
            if isinstance(rule_data, list) and rule_data:
                refund_rule = rule_data[0]

        refund_money = require_success(
            self.client(user).get(
                "place/getCanclePlaceMoney",
                params={"billNum": bill_num, "token": user.token},
            ),
            "getCanclePlaceMoney",
        )
        return {
            "booking": serialize_order(order) if order else None,
            "refund": serialize_refund(refund_money),
            "rule": serialize_refund_rule(refund_rule),
            "user": serialize_user(user),
        }

    def cancel(self, payload: dict[str, Any]) -> dict[str, Any]:
        user = self.users.get_user(clean_string(payload.get("user_key")))
        bill_num = clean_string(payload.get("bill_num"))
        reason = clean_string(payload.get("reason")) or "weather"
        confirmation = clean_string(payload.get("confirmation"))
        if confirmation != "CANCEL":
            raise EasySerpError("confirmation must be CANCEL")
        if not bill_num:
            raise EasySerpError("missing bill number")
        order = self._find_recent_order(bill_num, user)
        if not order:
            raise EasySerpError("bill number was not found in recent bookings")
        if is_cancelled(order):
            raise EasySerpError("booking is already cancelled")

        response = self.client(user).post(
            "place/canclePlaceAppointment",
            data={
                "outtradeno": bill_num,
                "token": user.token,
                "reason": reason,
                "affiliateCard": clean_string(payload.get("affiliate_card")),
            },
        )
        response_data = require_success(response, "canclePlaceAppointment")
        time.sleep(0.8)
        bookings = self.bookings(user.key, include_cancelled=True)
        cards = self.cards(user.key)
        order = next((item for item in bookings["bookings"] if item["bill_num"] == bill_num), None)
        confirmed = order is None or "取消" in (order.get("status") or "")
        if payload.get("require_confirmed") and not confirmed:
            raise EasySerpError("cancel was not confirmed")
        return {
            "response": {"msg": response.get("msg"), "data": response_data},
            "confirmed": confirmed,
            "booking": order,
            "bookings": bookings["bookings"],
            "cards": cards["cards"],
            "primary_card": cards["primary_card"],
            "user": serialize_user(user),
            "updated_at": time.time(),
        }

    def start_booking(self, payload: dict[str, Any]) -> dict[str, Any]:
        user = self.users.get_user(clean_string(payload.get("user_key")))
        if not user.token:
            raise EasySerpError("token is required for booking")
        card = self.resolve_booking_card(user)
        job = self.jobs.start(payload, user, card["card_index_raw"])
        return {"job": serialize_job(job), "user": serialize_user(user), "card": mask_booking_card(card)}

    def book_exact(self, payload: dict[str, Any]) -> dict[str, Any]:
        user = self.users.get_user(clean_string(payload.get("user_key")))
        if not user.token:
            raise EasySerpError("token is required for booking")
        slots = normalize_exact_slots(payload.get("slots"))
        card = self.resolve_booking_card(user)
        total_pay = round(sum(slot["pay_value"] for slot in slots), 2)
        balance = float_or_zero(card.get("cash_balance_value"))
        if total_pay > balance:
            raise EasySerpError(f"selected total {total_pay:.2f} exceeds card balance {balance:.2f}")

        dry_run = bool(payload.get("dry_run", False))
        client = self.client(user)
        places_by_date: dict[str, list[dict[str, Any]]] = {}
        successes: list[dict[str, Any]] = []
        failures: list[dict[str, Any]] = []

        for slot in slots:
            places = places_by_date.get(slot["date"])
            if places is None:
                places = self._fetch_places(client, user, slot["date"])
                places_by_date[slot["date"]] = places
            current = find_current_slot(slot, places)
            if not current:
                failures.append({"slot": public_exact_slot(slot), "error": "selected slot is no longer bookable"})
                continue
            if dry_run:
                successes.append({"slot": public_exact_slot(current), "dry_run": True})
                continue
            try:
                self._reserve_exact_slot(client, user, card["card_index_raw"], current)
                success_slot = public_exact_slot(current)
                try:
                    booking = self._find_recent_booking_for_slot(user, success_slot)
                    if booking:
                        success_slot["bill_num"] = booking.get("bill_num", "")
                        success_slot["booking"] = booking
                except EasySerpError as exc:
                    success_slot["booking_match_error"] = redact_sensitive_text(exc)
                successes.append({"slot": success_slot})
            except EasySerpError as exc:
                failures.append({"slot": public_exact_slot(current), "error": redact_sensitive_text(exc)})

        status, result_label = exact_result_status(successes, failures, dry_run)
        result = {
            "ok": bool(successes) and not failures,
            "status": status,
            "result_label": result_label,
            "dry_run": dry_run,
            "successes": successes,
            "failures": failures,
            "success_targets": [slot_success_target(item["slot"]) for item in successes],
            "total_pay": format_amount(total_pay),
            "total_pay_value": total_pay,
            "card": mask_booking_card(card),
            "user": serialize_user(user),
            "updated_at": time.time(),
        }
        result["history_id"] = self.history.create_exact({"slots": slots}, result, user)
        return result

    def booking_history(self, user_key: str = "", log_window_hours: int = LOG_DEFAULT_WINDOW_HOURS) -> dict[str, Any]:
        self.history.mark_orphaned_running(self.jobs.active_job_ids())
        history = self.history.list(window_hours=log_window_hours)
        if user_key:
            default_key = self.users.get_user("").key
            history = [
                item
                for item in history
                if item.get("user_key") == user_key or (not item.get("user_key") and user_key == default_key)
            ]
        return {"history": history, "updated_at": time.time()}

    def scan_tasks(self, user_key: str = "", log_window_hours: int = LOG_DEFAULT_WINDOW_HOURS) -> dict[str, Any]:
        return self.scans.snapshot(clean_string(user_key), event_window_hours=log_window_hours)

    def create_scan_task(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self.scans.create(payload)

    def update_scan_task(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self.scans.update(payload)

    def resolve_booking_card(self, user: UserAccount) -> dict[str, Any]:
        data = require_success(
            self.client(user).get(
                "card/getCardByUser",
                params={"shopNum": self.config.shop_num, "token": user.token},
            ),
            "getCardByUser",
        )
        if not isinstance(data, list):
            raise EasySerpError("card response is not a list")
        matches = [card for card in data if card_name_matches(card, user.card_name) and float_or_zero(card.get("cardcash")) > 0]
        if not matches:
            raise EasySerpError(f"no positive balance card matched {user.card_name}")
        raw_index = clean_string(matches[0].get("cardindex"))
        if not raw_index:
            raise EasySerpError("selected card has no card index")
        card = serialize_card(matches[0])
        card["card_index_raw"] = raw_index
        return card

    def _fetch_places(self, client: EasySerpClient, user: UserAccount, date_value: str) -> list[dict[str, Any]]:
        payload = client.get(
            "datediscount/getPlaceInfoByShortNameDiscount",
            params={
                "shopNum": self.config.shop_num,
                "dateymd": date_value,
                "shortName": DEFAULT_SHORT_NAME,
                "token": user.token,
            },
        )
        data = require_success(payload, "getPlaceInfoByShortNameDiscount")
        places = data.get("placeArray", []) if isinstance(data, dict) else []
        if not isinstance(places, list):
            raise EasySerpError("placeArray response is not a list")
        return places

    def _reserve_exact_slot(
        self,
        client: EasySerpClient,
        user: UserAccount,
        card_index: str,
        slot: dict[str, Any],
    ) -> None:
        canbook_fields = [
            {
                "day": slot["date"],
                "startTime": slot["start_time"],
                "endTime": slot["end_time"],
                "placeShortName": slot["id"],
            }
        ]
        field_info_full = [
            {
                "day": slot["date"],
                "startTime": slot["start_time"],
                "endTime": slot["end_time"],
                "placeShortName": slot["id"],
                "name": slot["name"],
                "stageTypeShortName": DEFAULT_SHORT_NAME,
            }
        ]
        field_info = json.dumps(field_info_full, ensure_ascii=False)
        require_success(
            client.post(
                "place/canBook",
                data={
                    "fieldinfo": json.dumps(canbook_fields, ensure_ascii=False),
                    "shopNum": self.config.shop_num,
                    "token": user.token,
                },
            ),
            "canBook",
        )
        time.sleep(BOOKING_CALL_DELAY_SECONDS)
        require_success(
            client.post(
                "common/getOfferInfo",
                data={
                    "token": user.token,
                    "payMoney": format_amount(slot["price_value"]),
                    "shopNum": self.config.shop_num,
                    "projectType": PROJECT_TYPE,
                    "projectInfo": field_info,
                },
            ),
            "getOfferInfo",
        )
        time.sleep(BOOKING_CALL_DELAY_SECONDS)
        require_success(
            client.post(
                "common/getUseCardInfo",
                data={
                    "token": user.token,
                    "shopNum": self.config.shop_num,
                    "projectType": PROJECT_TYPE,
                    "projectInfo": field_info,
                },
            ),
            "getUseCardInfo",
        )
        time.sleep(BOOKING_CALL_DELAY_SECONDS)
        require_success(
            client.post(
                "place/reservationPlace",
                data={
                    "token": user.token,
                    "shopNum": self.config.shop_num,
                    "fieldinfo": field_info,
                    "oldTotal": format_amount(slot["price_value"]),
                    "cardPayType": "0",
                    "type": BOOKING_ITEM_TYPE,
                    "offerId": card_index,
                    "offerType": PROJECT_TYPE,
                    "total": format_amount(slot["pay_value"]),
                    "premerother": "",
                    "cardIndex": card_index,
                    "masterCardNum": "",
                    "zengzhiMoney": "0",
                },
            ),
            "reservationPlace",
        )

    def _find_recent_order(self, bill_num: str, user: UserAccount) -> dict[str, Any] | None:
        orders = fetch_orders(
            self.client(user),
            token=user.token,
            shop_num=self.config.shop_num,
            page_size=20,
            max_pages=5,
        )
        return find_order(orders, bill_num)

    def _find_recent_booking_for_slot(self, user: UserAccount, slot: dict[str, Any]) -> dict[str, Any] | None:
        time.sleep(0.8)
        bookings = self.bookings(user.key, include_cancelled=False, success_only=True).get("bookings", [])
        slot_number = court_number_from_text(slot.get("id")) or court_number_from_text(slot.get("name"))
        for booking in bookings:
            if booking.get("date") != slot.get("date"):
                continue
            if booking.get("time_range") != slot.get("time"):
                continue
            booking_number = court_number_from_text(booking.get("court"))
            if slot_number is not None and booking_number == slot_number:
                return booking
            if clean_string(slot.get("name")) and clean_string(slot.get("name")) == clean_string(booking.get("court")):
                return booking
        return None


def credential_state(value: str) -> dict[str, Any]:
    return {"present": bool(value), "length": len(value or "")}


class EasySerpAuthError(EasySerpError):
    pass


def serialize_user(user: UserAccount) -> dict[str, Any]:
    return {
        "key": user.key,
        "label": user.label,
        "enabled": user.enabled,
        "card_name": user.card_name,
        "credential_status": {
            "token": credential_state(user.token),
            "jsessionid": credential_state(user.jsessionid),
        },
    }


def serialize_card(card: dict[str, Any]) -> dict[str, Any]:
    cash = float_or_zero(card.get("cardcash"))
    return {
        "card_index": mask_card(card.get("cardindex")),
        "card_name": card.get("cardname") or card.get("shortcardname") or "",
        "status": card.get("cardstatus") or "",
        "cash_balance": format_amount(card.get("cardcash")),
        "cash_balance_value": cash,
        "end_date": card.get("enddate") or "",
        "times_balance": card.get("cardtime") or card.get("shouChuCiShu") or "",
        "paid_amount": format_amount(card.get("shouChuJinE")),
    }


def select_primary_card(cards: list[dict[str, Any]], card_name: str) -> dict[str, Any] | None:
    preferred = [card for card in cards if card.get("cash_balance_value", 0) > 0 and card_name in str(card.get("card_name", ""))]
    if preferred:
        return preferred[0]
    positive = [card for card in cards if card.get("cash_balance_value", 0) > 0]
    return positive[0] if positive else (cards[0] if cards else None)


def card_name_matches(card: dict[str, Any], card_name: str) -> bool:
    text = " ".join(str(card.get(key) or "") for key in ("cardname", "shortcardname"))
    return card_name in text


def mask_booking_card(card: dict[str, Any]) -> dict[str, Any]:
    result = dict(card)
    result.pop("card_index_raw", None)
    return result


def serialize_order(order: dict[str, Any]) -> dict[str, Any]:
    summary = summarize_order(order)
    return {
        "bill_num": summary.bill_num,
        "status": summary.status,
        "date": summary.date,
        "time_range": summary.time_range,
        "court": summary.court,
        "amount": summary.amount,
        "pay_type": summary.pay_type,
        "created_at": summary.created_at,
        "short_name": summary.short_name,
        "cancelled": is_cancelled(order),
    }


def serialize_refund(refund: Any) -> dict[str, Any] | None:
    if not isinstance(refund, dict):
        return None
    return {
        "pay_money": format_amount(refund.get("payMoney")),
        "place_money": format_amount(refund.get("placeMoney")),
        "refund_money": format_amount(refund.get("reFundMoney")),
        "extra_money": format_amount(refund.get("zengzhiMoney")),
    }


def serialize_refund_rule(rule: Any) -> dict[str, Any] | None:
    if not isinstance(rule, dict):
        return None
    return {
        "refund_percentage": rule.get("refundPercentage"),
        "cancel_time": rule.get("canceltime"),
        "last_day_open_time": rule.get("lastDayOpenTime"),
    }


def serialize_availability_day(date_value: str, places: list[dict[str, Any]]) -> dict[str, Any]:
    hour_map: dict[str, dict[str, Any]] = {}
    for place in places:
        project = place.get("projectName", {}) if isinstance(place, dict) else {}
        court_id = str(project.get("shortname") or "")
        court_name = str(project.get("name") or court_id or "场地")
        court_number = court_number_from_text(court_id) or court_number_from_text(court_name)
        for slot in place.get("projectInfo", []):
            if not isinstance(slot, dict) or str(slot.get("state")) != "1":
                continue
            time_range = slot_time_range(slot)
            if not time_range:
                continue
            start_time, end_time = split_time_range(time_range)
            hour = hour_map.setdefault(
                time_range,
                {"time": time_range, "start_time": start_time, "end_time": end_time, "count": 0, "courts": []},
            )
            price_value = slot_price_value(slot, date_value, start_time)
            pay_value = slot_pay_value(date_value, start_time)
            hour["courts"].append(
                {
                    "id": court_id,
                    "name": court_name,
                    "number": court_number,
                    "wall": court_number in WALL_COURTS if court_number is not None else False,
                    "price": format_amount(price_value),
                    "price_value": price_value,
                    "pay": format_amount(pay_value),
                    "pay_value": pay_value,
                    "start_time": start_time,
                    "end_time": end_time,
                }
            )
            hour["count"] += 1

    hours = list(hour_map.values())
    for hour in hours:
        hour["courts"].sort(key=lambda item: (item["number"] is None, item["number"] or 999, item["name"]))
    hours.sort(key=lambda item: item["time"])
    return {
        "date": date_value,
        "label": relative_day_label((date.fromisoformat(date_value) - date.today()).days),
        "total": sum(hour["count"] for hour in hours),
        "hours": hours,
        "error": "",
    }


def slot_time_range(slot: dict[str, Any]) -> str:
    start = trim_time(slot.get("starttime") or slot.get("startTime") or slot.get("start") or "")
    end = trim_time(slot.get("endtime") or slot.get("endTime") or slot.get("end") or "")
    if not start and not end:
        return ""
    return f"{start}-{end}"


def split_time_range(time_range: str) -> tuple[str, str]:
    if "-" not in time_range:
        return time_range, ""
    start, end = time_range.split("-", 1)
    return trim_time(start), trim_time(end)


def slot_price_value(slot: dict[str, Any], date_value: str, start_time: str) -> float:
    for key in ("oldMoney", "money"):
        value = float_or_none(slot.get(key))
        if value is not None:
            return value
    return slot_pay_value(date_value, start_time)


def slot_pay_value(date_value: str, start_time: str) -> float:
    try:
        target_day = date.fromisoformat(date_value)
    except ValueError as exc:
        raise EasySerpError("invalid slot date") from exc
    hour = hour_from_time(start_time)
    if target_day.weekday() < 5 and hour < 16:
        return 20.0
    return 30.0


def hour_from_time(value: str) -> int:
    try:
        return int(trim_time(value).split(":", 1)[0])
    except (TypeError, ValueError, IndexError) as exc:
        raise EasySerpError("invalid slot time") from exc


def normalize_exact_slots(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        raise EasySerpError("slots must be a list")
    if not 1 <= len(value) <= 2:
        raise EasySerpError("select one or two slots")

    dates: set[str] = set()
    starts: set[str] = set()
    result: list[dict[str, Any]] = []
    for item in value:
        if not isinstance(item, dict):
            raise EasySerpError("slot must be an object")
        date_value = clean_string(item.get("date"))
        try:
            date.fromisoformat(date_value)
        except ValueError as exc:
            raise EasySerpError("invalid slot date") from exc
        start_time = normalize_slot_time(item.get("start_time"))
        end_time = normalize_slot_time(item.get("end_time"))
        if end_time <= start_time:
            raise EasySerpError("slot end time must be after start time")
        court_id = clean_string(item.get("id") or item.get("court_id"))
        if not court_id:
            raise EasySerpError("slot court id is required")
        key = f"{date_value}|{start_time}"
        if key in starts:
            raise EasySerpError("only one court can be selected for each hour")
        dates.add(date_value)
        starts.add(key)
        result.append(
            {
                "date": date_value,
                "start_time": start_time,
                "end_time": end_time,
                "time": f"{start_time}-{end_time}",
                "id": court_id,
                "name": clean_string(item.get("name")) or court_id,
                "number": court_number_from_text(court_id),
                "price_value": float_or_zero(item.get("price_value")),
                "pay_value": slot_pay_value(date_value, start_time),
                "wall": False,
            }
        )
    if len(dates) != 1:
        raise EasySerpError("selected slots must be on the same date")
    result.sort(key=lambda item: (item["date"], item["start_time"], item["id"]))
    return result


def normalize_slot_time(value: Any) -> str:
    text = trim_time(value)
    match = re.fullmatch(r"(\d{1,2}):(\d{2})", text)
    if not match:
        raise EasySerpError("invalid slot time")
    return f"{int(match.group(1)):02d}:{match.group(2)}"


def find_current_slot(target: dict[str, Any], places: list[dict[str, Any]]) -> dict[str, Any] | None:
    for place in places:
        project = place.get("projectName", {}) if isinstance(place, dict) else {}
        court_id = clean_string(project.get("shortname"))
        if court_id != target["id"]:
            continue
        court_name = clean_string(project.get("name")) or court_id
        court_number = court_number_from_text(court_id) or court_number_from_text(court_name)
        for slot in place.get("projectInfo", []):
            if not isinstance(slot, dict) or str(slot.get("state")) != "1":
                continue
            time_range = slot_time_range(slot)
            if not time_range:
                continue
            start_time, end_time = split_time_range(time_range)
            if start_time == target["start_time"] and end_time == target["end_time"]:
                price_value = slot_price_value(slot, target["date"], start_time)
                pay_value = slot_pay_value(target["date"], start_time)
                return {
                    **target,
                    "name": court_name,
                    "number": court_number,
                    "wall": court_number in WALL_COURTS if court_number is not None else False,
                    "price_value": price_value,
                    "pay_value": pay_value,
                    "price": format_amount(price_value),
                    "pay": format_amount(pay_value),
                }
    return None


def public_exact_slot(slot: dict[str, Any]) -> dict[str, Any]:
    return {
        "date": slot.get("date"),
        "time": slot.get("time") or f"{slot.get('start_time')}-{slot.get('end_time')}",
        "start_time": slot.get("start_time"),
        "end_time": slot.get("end_time"),
        "id": slot.get("id"),
        "name": slot.get("name"),
        "number": slot.get("number"),
        "price": format_amount(slot.get("price_value")),
        "price_value": float_or_zero(slot.get("price_value")),
        "pay": format_amount(slot.get("pay_value")),
        "pay_value": float_or_zero(slot.get("pay_value")),
        "wall": bool(slot.get("wall")),
    }


def exact_result_status(
    successes: list[dict[str, Any]],
    failures: list[dict[str, Any]],
    dry_run: bool,
) -> tuple[str, str]:
    if dry_run and successes and not failures:
        return "dry_run", "演练完成"
    if successes and failures:
        return "partial_success", "部分成功"
    if successes:
        return "success", "成功"
    return "failed", "失败"


def exact_history_detail(result: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "exact_booking",
        "dry_run": bool(result.get("dry_run")),
        "total_pay": clean_string(result.get("total_pay")),
        "successes": [
            {
                "slot": public_history_slot(item.get("slot")),
                "bill_num": clean_string((item.get("slot") or {}).get("bill_num")) if isinstance(item, dict) else "",
            }
            for item in result.get("successes") or []
            if isinstance(item, dict)
        ],
        "failures": [
            {
                "slot": public_history_slot(item.get("slot")),
                "error": clean_string(item.get("error")) if isinstance(item, dict) else "",
            }
            for item in result.get("failures") or []
            if isinstance(item, dict)
        ],
    }


def public_history_slot(value: Any) -> dict[str, Any]:
    slot = value if isinstance(value, dict) else {}
    return {
        "date": clean_string(slot.get("date")),
        "time": clean_string(slot.get("time")) or slot_time_from_parts(slot),
        "name": clean_string(slot.get("name")) or clean_string(slot.get("id")),
        "id": clean_string(slot.get("id")),
    }


def slot_success_target(slot: dict[str, Any]) -> str:
    return f"{clean_string(slot.get('name')) or clean_string(slot.get('id'))} {clean_string(slot.get('time'))}"


def slot_label(slot: dict[str, Any]) -> str:
    return f"{clean_string(slot.get('time')) or slot_time_from_parts(slot)} {clean_string(slot.get('name')) or clean_string(slot.get('id'))}"


def slot_time_from_parts(slot: dict[str, Any]) -> str:
    start_time = clean_string(slot.get("start_time"))
    end_time = clean_string(slot.get("end_time"))
    return f"{start_time}-{end_time}" if start_time or end_time else ""


def court_number_from_text(value: Any) -> int | None:
    match = re.search(r"(\d+)", str(value or ""))
    if not match:
        return None
    try:
        return int(match.group(1))
    except ValueError:
        return None


def relative_day_label(offset: int) -> str:
    if offset == 0:
        return "今天"
    if offset == 1:
        return "明天"
    return f"{offset} 天后"


def float_or_zero(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def float_or_none(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


class RequestHandler(BaseHTTPRequestHandler):
    server_version = "DaydayupWebConsole/0.1"

    def do_GET(self) -> None:
        try:
            self.route_get()
        except EasySerpAuthError as exc:
            self.write_json({"error": redact_sensitive_text(exc)}, HTTPStatus.UNAUTHORIZED)
        except EasySerpError as exc:
            self.write_json({"error": redact_sensitive_text(exc)}, HTTPStatus.BAD_REQUEST)
        except Exception as exc:
            self.write_json({"error": f"unexpected error: {redact_sensitive_text(exc)}"}, HTTPStatus.INTERNAL_SERVER_ERROR)

    def do_POST(self) -> None:
        try:
            self.route_post()
        except EasySerpAuthError as exc:
            self.write_json({"error": redact_sensitive_text(exc)}, HTTPStatus.UNAUTHORIZED)
        except EasySerpError as exc:
            self.write_json({"error": redact_sensitive_text(exc)}, HTTPStatus.BAD_REQUEST)
        except Exception as exc:
            self.write_json({"error": f"unexpected error: {redact_sensitive_text(exc)}"}, HTTPStatus.INTERNAL_SERVER_ERROR)

    def route_get(self) -> None:
        parsed = urlsplit(self.path)
        query = parse_qs(parsed.query)
        app = self.console

        if parsed.path == "/api/status":
            if not self.authorized():
                return
            self.write_json(app.status(single_query(query, "user_key")))
        elif parsed.path == "/api/users":
            if not self.authorized():
                return
            self.write_json(app.user_list())
        elif parsed.path == "/api/cards":
            if not self.authorized():
                return
            self.write_json(app.cards(single_query(query, "user_key")))
        elif parsed.path == "/api/bookings":
            if not self.authorized():
                return
            self.write_json(
                app.bookings(
                    user_key=single_query(query, "user_key"),
                    include_cancelled=query.get("all", ["0"])[0] == "1",
                    success_only=query.get("success", ["0"])[0] == "1",
                )
            )
        elif parsed.path == "/api/availability":
            if not self.authorized():
                return
            days = int(query.get("days", ["5"])[0] or "5")
            self.write_json(app.availability(single_query(query, "user_key"), days=days))
        elif parsed.path == "/api/booking/history":
            if not self.authorized():
                return
            self.write_json(
                app.booking_history(
                    single_query(query, "user_key"),
                    log_window_hours=parse_log_window_hours(single_query(query, "log_window_hours")),
                )
            )
        elif parsed.path == "/api/booking/job":
            if not self.authorized():
                return
            self.write_json(app.jobs.snapshot())
        elif parsed.path == "/api/analytics/availability":
            if not self.authorized():
                return
            metric = single_query(query, "metric")
            if not metric:
                raise EasySerpError("metric is required")
            window_days = single_query(query, "window_days")
            start_hour = single_query(query, "start_hour")
            end_hour = single_query(query, "end_hour")
            courts = parse_int_list(query.get("courts"))
            raw_slots = query.get("slots", [])
            slots: list[str] = []
            for raw_slot in raw_slots:
                slots.extend([item for item in str(raw_slot).replace(",", " ").split() if item])
            if not slots:
                slots = []
            cache_ttl = single_query(query, "cache_ttl_seconds")
            self.write_json(
                app.availability_analytics(
                    metric=metric,
                    window_days=window_days,
                    start_hour=start_hour,
                    end_hour=end_hour,
                    courts=courts,
                    slots=slots,
                    cache_ttl_seconds=int_or_default(cache_ttl, availability_analytics.DEFAULT_CACHE_TTL_SECONDS),
                )
            )
        elif parsed.path == "/api/scan/tasks":
            if not self.authorized():
                return
            self.write_json(
                app.scan_tasks(
                    single_query(query, "user_key"),
                    log_window_hours=parse_log_window_hours(single_query(query, "log_window_hours")),
                )
            )
        elif parsed.path == "/":
            self.serve_static(WEB_DIR / "index.html", "text/html; charset=utf-8")
        elif parsed.path in ("/app.js", "/styles.css"):
            content_type = "application/javascript; charset=utf-8" if parsed.path.endswith(".js") else "text/css; charset=utf-8"
            self.serve_static(WEB_DIR / parsed.path.lstrip("/"), content_type)
        else:
            self.write_json({"error": "not found"}, HTTPStatus.NOT_FOUND)

    def route_post(self) -> None:
        app = self.console
        payload = self.read_json()
        if self.path == "/api/auth/login":
            self.write_json(app.auth_login(payload))
            return
        if self.path.startswith("/api/") and not self.authorized():
            return
        if self.path == "/api/users/unlock":
            self.write_json(app.unlock_users(payload))
        elif self.path == "/api/users":
            self.write_json(app.save_user(payload))
        elif self.path == "/api/token/auth-url":
            self.write_json(app.token_auth_url(payload))
        elif self.path == "/api/token/exchange":
            self.write_json(app.token_exchange(payload))
        elif self.path == "/api/cancel/preview":
            bill_num = clean_string(payload.get("bill_num"))
            if not bill_num:
                raise EasySerpError("missing bill number")
            self.write_json(app.cancel_preview(bill_num, clean_string(payload.get("user_key"))))
        elif self.path == "/api/cancel":
            self.write_json(app.cancel(payload))
        elif self.path == "/api/booking/start":
            self.write_json(app.start_booking(payload))
        elif self.path == "/api/booking/exact":
            self.write_json(app.book_exact(payload))
        elif self.path == "/api/booking/stop":
            self.write_json(app.jobs.stop())
        elif self.path == "/api/scan/tasks":
            self.write_json(app.create_scan_task(payload))
        elif self.path == "/api/scan/tasks/update":
            self.write_json(app.update_scan_task(payload))
        else:
            self.write_json({"error": "not found"}, HTTPStatus.NOT_FOUND)

    def read_json(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0"))
        if length <= 0:
            return {}
        raw = self.rfile.read(length).decode("utf-8")
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise EasySerpError("invalid JSON body") from exc
        if not isinstance(payload, dict):
            raise EasySerpError("JSON body must be an object")
        return payload

    def write_json(self, payload: dict[str, Any], status: HTTPStatus = HTTPStatus.OK) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def authorized(self) -> bool:
        if self.console.users.verify_access(self.headers.get("X-Daydayup-Key", "")):
            return True
        self.write_json({"error": "access key required"}, HTTPStatus.UNAUTHORIZED)
        return False

    def serve_static(self, path: Path, content_type: str) -> None:
        if not path.exists() or not path.is_file():
            self.write_json({"error": "not found"}, HTTPStatus.NOT_FOUND)
            return
        body = path.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: Any) -> None:
        sys.stderr.write("[%s] %s\n" % (self.log_date_time_string(), format % args))

    @property
    def console(self) -> WebConsole:
        return self.server.console  # type: ignore[attr-defined]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the local booking web console")
    parser.add_argument("--host", default="127.0.0.1", help="host to bind")
    parser.add_argument("--port", type=int, default=DEFAULT_WEB_PORT, help="port to bind")
    parser.add_argument("-k", "--token", default=DEFAULT_TOKEN, help="wechat token")
    parser.add_argument("-j", "--jsessionid", default=DEFAULT_JSESSIONID, help="JSESSIONID")
    parser.add_argument("--shop-num", default=DEFAULT_SHOP_NUM, help="shop number")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL, help="EasySERP API base URL")
    parser.add_argument("--timeout", type=float, default=10.0, help="request timeout in seconds")
    parser.add_argument("--users-csv", default=str(USERS_PATH), help="local users CSV path")
    parser.add_argument(
        "--scan-worker",
        action="store_true",
        default=env_flag("DAYDAYUP_WEB_SCAN_WORKER"),
        help="run scan tasks inside the web service process",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    config = ServerConfig(
        shop_num=args.shop_num,
        base_url=args.base_url,
        timeout=args.timeout,
    )
    users = UserStore(Path(args.users_csv), default_token=args.token, default_jsessionid=args.jsessionid)
    httpd = ThreadingHTTPServer((args.host, args.port), RequestHandler)
    httpd.console = WebConsole(config, users, start_scan_worker=args.scan_worker)  # type: ignore[attr-defined]
    print(f"Daydayup web console running at http://{args.host}:{args.port}")
    print(f"Users CSV={users.path}")
    print(f"Enabled users={len(users.enabled_users())}")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped")
        return 130
    finally:
        httpd.console.close()  # type: ignore[attr-defined]
        httpd.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
