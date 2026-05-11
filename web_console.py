#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Local web console for booking operations.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from datetime import date, timedelta
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


ROOT = Path(__file__).resolve().parent
WEB_DIR = ROOT / "web"
BOOKING_SCRIPT = ROOT / "enhanced_book_smart_v2.py"
CONFIG_PATH = ROOT / "local" / "config.json"
HISTORY_PATH = ROOT / "logs" / "booking_history.json"
DEFAULT_WEB_PORT = int(os.getenv("DAYDAYUP_WEB_PORT", "8788"))
DEFAULT_CARD_NAME = "学生球类卡"
DEFAULT_OAUTH_REDIRECT_URL = "https://www.147soft.cn/easyserp/index.html"
WALL_COURTS = {1, 5, 12}


@dataclass
class ServerConfig:
    shop_num: str
    base_url: str
    timeout: float


@dataclass(frozen=True)
class LocalAccount:
    token: str
    jsessionid: str
    card_name: str


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


class ConfigStore:
    def __init__(self, path: Path, *, token: str, jsessionid: str, card_name: str):
        self.path = path
        self.lock = threading.Lock()
        self.defaults = {
            "token": clean_string(token),
            "jsessionid": clean_string(jsessionid),
            "card_name": clean_string(card_name) or DEFAULT_CARD_NAME,
        }
        self.ensure_exists()

    def ensure_exists(self) -> None:
        with self.lock:
            if self.path.exists():
                return
            self._write_unlocked(self.defaults)

    def get(self) -> LocalAccount:
        with self.lock:
            data = self._read_unlocked()
        return LocalAccount(
            token=clean_string(data.get("token")),
            jsessionid=clean_string(data.get("jsessionid")),
            card_name=clean_string(data.get("card_name")) or DEFAULT_CARD_NAME,
        )

    def update(self, payload: dict[str, Any]) -> LocalAccount:
        with self.lock:
            data = self._read_unlocked()
            for key in ("token", "jsessionid", "card_name"):
                if key in payload:
                    data[key] = clean_string(payload.get(key))
            if not clean_string(data.get("card_name")):
                data["card_name"] = DEFAULT_CARD_NAME
            self._write_unlocked(data)
        return self.get()

    def _read_unlocked(self) -> dict[str, Any]:
        if not self.path.exists():
            return dict(self.defaults)
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return dict(self.defaults)
        return data if isinstance(data, dict) else dict(self.defaults)

    def _write_unlocked(self, data: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = self.path.with_suffix(".tmp")
        tmp_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp_path.replace(self.path)


class BookingHistoryStore:
    def __init__(self, path: Path):
        self.path = path
        self.lock = threading.Lock()

    def list(self, limit: int = 30) -> list[dict[str, Any]]:
        with self.lock:
            records = self._read_unlocked()
        records.sort(key=lambda item: item.get("requested_ts", 0), reverse=True)
        return records[:limit]

    def create(self, payload: dict[str, Any], job_id: int, command_label: str) -> str:
        now = time.time()
        record_id = str(int(now * 1000))
        record = {
            "id": record_id,
            "job_id": job_id,
            "requested_at": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(now)),
            "requested_ts": now,
            "target_date": target_date_from_payload(payload),
            "target_time": clean_string(payload.get("time")) or "17-21",
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

    def finish(self, job: BookingJob) -> None:
        summary = summarize_job_history(job)
        with self.lock:
            records = self._read_unlocked()
            for record in records:
                if record.get("id") == job.history_id:
                    record.update(summary)
                    break
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
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = self.path.with_suffix(".tmp")
        tmp_path.write_text(json.dumps(records[-200:], ensure_ascii=False, indent=2), encoding="utf-8")
        tmp_path.replace(self.path)


class JobManager:
    def __init__(self, config: ServerConfig, history: BookingHistoryStore):
        self.config = config
        self.history = history
        self.lock = threading.Lock()
        self.job: BookingJob | None = None
        self.next_id = 1

    def start(self, payload: dict[str, Any], account: LocalAccount, card_index: str) -> BookingJob:
        with self.lock:
            if self.job and self.job.status in ("running", "stopping"):
                raise EasySerpError("a booking job is already running")

            command, command_label = build_booking_command(payload)
            env = os.environ.copy()
            env["DAYDAYUP_TOKEN"] = account.token
            env["DAYDAYUP_JSESSIONID"] = account.jsessionid
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
            job.history_id = self.history.create(payload, job.id, command_label)
            self.next_id += 1
            self.job = job
            threading.Thread(target=self._read_output, args=(job,), daemon=True).start()
            return job

    def snapshot(self) -> dict[str, Any]:
        with self.lock:
            if not self.job:
                return {"running": False, "job": None}
            self._poll_locked(self.job)
            return {"running": self.job.status == "running", "job": serialize_job(self.job)}

    def stop(self) -> dict[str, Any]:
        with self.lock:
            if not self.job or self.job.status != "running":
                return {"stopped": False, "message": "no running job"}
            self.job.process.terminate()
            self.job.status = "stopping"
            return {"stopped": True}

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


class WebConsole:
    def __init__(self, config: ServerConfig, store: ConfigStore):
        self.config = config
        self.store = store
        self.history = BookingHistoryStore(HISTORY_PATH)
        self.jobs = JobManager(config, self.history)

    def client(self, account: LocalAccount | None = None) -> EasySerpClient:
        account = account or self.store.get()
        return EasySerpClient(self.config.base_url, account.token, account.jsessionid, self.config.timeout)

    def config_status(self) -> dict[str, Any]:
        account = self.store.get()
        return {
            "configured": bool(account.token),
            "token": credential_state(account.token),
            "jsessionid": credential_state(account.jsessionid),
            "card_name": account.card_name,
            "base_url": self.config.base_url,
            "shop_num": self.config.shop_num,
        }

    def save_config(self, payload: dict[str, Any]) -> dict[str, Any]:
        account = self.store.update(payload)
        return {"config": self.config_status(), "account": serialize_account(account)}

    def token_auth_url(self, payload: dict[str, Any]) -> dict[str, Any]:
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
        username = clean_string(payload.get("username"))
        password = clean_string(payload.get("password"))
        code = extract_oauth_code(payload)
        club_member_code = clean_string(payload.get("club_member_code")) or "bdyxbtyg7"
        name = clean_string(payload.get("name")) or "wx"
        card_name = clean_string(payload.get("card_name")) or DEFAULT_CARD_NAME
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
        account = self.store.update({"token": token, "card_name": card_name})
        return {"config": self.config_status(), "account": serialize_account(account)}

    def require_token(self) -> LocalAccount:
        account = self.store.get()
        if not account.token:
            raise EasySerpError("token is required")
        return account

    def status(self) -> dict[str, Any]:
        return self.config_status()

    def cards(self) -> dict[str, Any]:
        account = self.require_token()
        data = require_success(
            self.client(account).get(
                "card/getCardByUser",
                params={"shopNum": self.config.shop_num, "token": account.token},
            ),
            "getCardByUser",
        )
        if not isinstance(data, list):
            raise EasySerpError("card response is not a list")
        cards = [serialize_card(card) for card in data]
        primary = select_primary_card(cards, account.card_name)
        return {"cards": cards, "primary_card": primary, "updated_at": time.time()}

    def bookings(self, include_cancelled: bool = False, success_only: bool = False) -> dict[str, Any]:
        account = self.require_token()
        orders = fetch_orders(
            self.client(account),
            token=account.token,
            shop_num=self.config.shop_num,
            page_size=20,
            max_pages=5,
        )
        if success_only or not include_cancelled:
            orders = [order for order in orders if not is_cancelled(order)]
        return {"bookings": [serialize_order(order) for order in orders], "updated_at": time.time()}

    def availability(self, days: int = 5) -> dict[str, Any]:
        account = self.require_token()
        days = max(1, min(days, 7))
        client = self.client(account)
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
                        "token": account.token,
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
        return {"days": results, "updated_at": time.time()}

    def cancel_preview(self, bill_num: str) -> dict[str, Any]:
        account = self.require_token()
        order = self._find_recent_order(bill_num, account)
        if not order:
            raise EasySerpError("bill number was not found in recent bookings")
        if is_cancelled(order):
            raise EasySerpError("booking is already cancelled")

        short_name = summarize_order(order).short_name or DEFAULT_SHORT_NAME
        refund_rule = None
        rule_data = require_success(
            self.client(account).get(
                "common/getRefundTime",
                params={
                    "shortName": short_name,
                    "shopNum": self.config.shop_num,
                    "token": account.token,
                    "type": "place",
                },
            ),
            "getRefundTime",
        )
        if isinstance(rule_data, list) and rule_data:
            refund_rule = rule_data[0]

        refund_money = require_success(
            self.client(account).get(
                "place/getCanclePlaceMoney",
                params={"billNum": bill_num, "token": account.token},
            ),
            "getCanclePlaceMoney",
        )
        return {
            "booking": serialize_order(order),
            "refund": serialize_refund(refund_money),
            "rule": serialize_refund_rule(refund_rule),
        }

    def cancel(self, payload: dict[str, Any]) -> dict[str, Any]:
        account = self.require_token()
        bill_num = clean_string(payload.get("bill_num"))
        reason = clean_string(payload.get("reason")) or "weather"
        confirmation = clean_string(payload.get("confirmation"))
        if confirmation != "CANCEL":
            raise EasySerpError("confirmation must be CANCEL")
        if not bill_num:
            raise EasySerpError("missing bill number")
        order = self._find_recent_order(bill_num, account)
        if not order:
            raise EasySerpError("bill number was not found in recent bookings")
        if is_cancelled(order):
            raise EasySerpError("booking is already cancelled")

        response = self.client(account).post(
            "place/canclePlaceAppointment",
            data={
                "outtradeno": bill_num,
                "token": account.token,
                "reason": reason,
                "affiliateCard": clean_string(payload.get("affiliate_card")),
            },
        )
        time.sleep(0.8)
        bookings = self.bookings(include_cancelled=True)
        cards = self.cards()
        order = next((item for item in bookings["bookings"] if item["bill_num"] == bill_num), None)
        confirmed = order is None or "取消" in (order.get("status") or "")
        return {
            "response": {"msg": response.get("msg"), "data": response.get("data")},
            "confirmed": confirmed,
            "booking": order,
            "bookings": bookings["bookings"],
            "cards": cards["cards"],
            "primary_card": cards["primary_card"],
            "updated_at": time.time(),
        }

    def start_booking(self, payload: dict[str, Any]) -> dict[str, Any]:
        account = self.require_token()
        card = self.resolve_booking_card(account)
        job = self.jobs.start(payload, account, card["card_index_raw"])
        return {"job": serialize_job(job), "card": mask_booking_card(card)}

    def booking_history(self) -> dict[str, Any]:
        return {"history": self.history.list(), "updated_at": time.time()}

    def resolve_booking_card(self, account: LocalAccount) -> dict[str, Any]:
        data = require_success(
            self.client(account).get(
                "card/getCardByUser",
                params={"shopNum": self.config.shop_num, "token": account.token},
            ),
            "getCardByUser",
        )
        if not isinstance(data, list):
            raise EasySerpError("card response is not a list")
        matches = [card for card in data if card_name_matches(card, account.card_name) and float_or_zero(card.get("cardcash")) > 0]
        if not matches:
            raise EasySerpError(f"no positive balance card matched {account.card_name}")
        raw_index = clean_string(matches[0].get("cardindex"))
        if not raw_index:
            raise EasySerpError("selected card has no card index")
        card = serialize_card(matches[0])
        card["card_index_raw"] = raw_index
        return card

    def _find_recent_order(self, bill_num: str, account: LocalAccount) -> dict[str, Any] | None:
        orders = fetch_orders(
            self.client(account),
            token=account.token,
            shop_num=self.config.shop_num,
            page_size=20,
            max_pages=5,
        )
        return find_order(orders, bill_num)


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
    duration = clean_string(payload.get("duration")) or "2"
    command.extend(["-t", time_range, "--duration", duration])
    labels.extend([f"time={time_range}", f"duration={duration}"])

    for flag, key in (("-p", "priority"), ("--backup", "backup")):
        values = parse_int_list(payload.get(key))
        if values:
            command.append(flag)
            command.extend(str(value) for value in values)
            labels.append(f"{key}={','.join(str(value) for value in values)}")

    force = bool(payload.get("force", True))
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
        ("--error-backoff", "error_backoff", "0.25"),
    ):
        value = clean_string(payload.get(payload_key)) or default_value
        command.extend([cli_name, value])

    return command, " ".join(labels)


def parse_int_list(value: Any) -> list[int]:
    if value is None:
        return []
    raw_values = value if isinstance(value, list) else str(value).replace(",", " ").split()
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


def credential_state(value: str) -> dict[str, Any]:
    return {"present": bool(value), "length": len(value or "")}


def serialize_account(account: LocalAccount) -> dict[str, Any]:
    return {
        "card_name": account.card_name,
        "credential_status": {
            "token": credential_state(account.token),
            "jsessionid": credential_state(account.jsessionid),
        },
    }


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
            hour = hour_map.setdefault(time_range, {"time": time_range, "count": 0, "courts": []})
            price = format_amount(slot.get("oldMoney") or slot.get("money"))
            hour["courts"].append(
                {
                    "id": court_id,
                    "name": court_name,
                    "number": court_number,
                    "wall": court_number in WALL_COURTS if court_number is not None else False,
                    "price": price,
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


class RequestHandler(BaseHTTPRequestHandler):
    server_version = "DaydayupWebConsole/0.1"

    def do_GET(self) -> None:
        try:
            self.route_get()
        except EasySerpError as exc:
            self.write_json({"error": redact_sensitive_text(exc)}, HTTPStatus.BAD_REQUEST)
        except Exception as exc:
            self.write_json({"error": f"unexpected error: {redact_sensitive_text(exc)}"}, HTTPStatus.INTERNAL_SERVER_ERROR)

    def do_POST(self) -> None:
        try:
            self.route_post()
        except EasySerpError as exc:
            self.write_json({"error": redact_sensitive_text(exc)}, HTTPStatus.BAD_REQUEST)
        except Exception as exc:
            self.write_json({"error": f"unexpected error: {redact_sensitive_text(exc)}"}, HTTPStatus.INTERNAL_SERVER_ERROR)

    def route_get(self) -> None:
        parsed = urlsplit(self.path)
        query = parse_qs(parsed.query)
        app = self.console

        if parsed.path == "/api/status":
            self.write_json(app.status())
        elif parsed.path == "/api/config":
            self.write_json(app.config_status())
        elif parsed.path == "/api/cards":
            self.write_json(app.cards())
        elif parsed.path == "/api/bookings":
            self.write_json(
                app.bookings(
                    include_cancelled=query.get("all", ["0"])[0] == "1",
                    success_only=query.get("success", ["0"])[0] == "1",
                )
            )
        elif parsed.path == "/api/availability":
            days = int(query.get("days", ["5"])[0] or "5")
            self.write_json(app.availability(days=days))
        elif parsed.path == "/api/booking/history":
            self.write_json(app.booking_history())
        elif parsed.path == "/api/booking/job":
            self.write_json(app.jobs.snapshot())
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
        if self.path == "/api/config":
            self.write_json(app.save_config(payload))
        elif self.path == "/api/token/auth-url":
            self.write_json(app.token_auth_url(payload))
        elif self.path == "/api/token/exchange":
            self.write_json(app.token_exchange(payload))
        elif self.path == "/api/cancel/preview":
            bill_num = clean_string(payload.get("bill_num"))
            if not bill_num:
                raise EasySerpError("missing bill number")
            self.write_json(app.cancel_preview(bill_num))
        elif self.path == "/api/cancel":
            self.write_json(app.cancel(payload))
        elif self.path == "/api/booking/start":
            self.write_json(app.start_booking(payload))
        elif self.path == "/api/booking/stop":
            self.write_json(app.jobs.stop())
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
    parser.add_argument("--card-name", default=os.getenv("DAYDAYUP_CARD_NAME", DEFAULT_CARD_NAME), help="card name")
    parser.add_argument("--shop-num", default=DEFAULT_SHOP_NUM, help="shop number")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL, help="EasySERP API base URL")
    parser.add_argument("--timeout", type=float, default=10.0, help="request timeout in seconds")
    parser.add_argument("--config", default=str(CONFIG_PATH), help="local config JSON path")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    config = ServerConfig(shop_num=args.shop_num, base_url=args.base_url, timeout=args.timeout)
    store = ConfigStore(Path(args.config), token=args.token, jsessionid=args.jsessionid, card_name=args.card_name)
    httpd = ThreadingHTTPServer((args.host, args.port), RequestHandler)
    httpd.console = WebConsole(config, store)  # type: ignore[attr-defined]
    print(f"Daydayup web console running at http://{args.host}:{args.port}")
    print(f"Config={store.path}")
    print(f"Token configured={store.get().token != ''}")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped")
        return 130
    finally:
        httpd.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
