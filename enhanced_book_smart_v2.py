#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Badminton court booking script with balanced retry behavior.
"""

import argparse
import hashlib
import http.client
import json
import logging
import math
import os
import queue
import re
import ssl
import sys
import threading
import time
import traceback
import urllib.parse
from collections import Counter, defaultdict, deque
from dataclasses import dataclass
from datetime import datetime, timedelta
from logging.handlers import RotatingFileHandler

from network_alias import DEFAULT_HOST_ALIASES, install_host_aliases_with_defaults


install_host_aliases_with_defaults(default_aliases=DEFAULT_HOST_ALIASES)


DEFAULT_BASE_URL = "https://www.147soft.cn/easyserpClient"
DEFAULT_TOKEN = os.getenv("DAYDAYUP_TOKEN", "")
DEFAULT_JSESSIONID = os.getenv("DAYDAYUP_JSESSIONID", "")
DEFAULT_CARD_INDEX = os.getenv("DAYDAYUP_CARD_INDEX", "")
SHOP_NUM = "1001"
SHORT_NAME = "ymq"
PROJECT_TYPE = "3"
BOOKING_MODE_BALANCED = "balanced"
BOOKING_MODE_DIRECT_FAST = "direct-fast"
BOOKING_MODE_GUIDED_FAST = "guided-fast"
BOOKING_MODES = (BOOKING_MODE_BALANCED, BOOKING_MODE_DIRECT_FAST, BOOKING_MODE_GUIDED_FAST)
BOOKING_ENGINE_VERSION = "3.5.0"
PREWARM_SECONDS = 6.0
BUSY_RETRY_TEXT = "当前排队人数较多"
FAST_RETRY_TEXT = "操作过快"
TAKEN_RETRY_TEXT = "下手太晚"
DEFAULT_DIRECT_SPEC_ADJACENT_DELAY = 0.0
DEFAULT_DIRECT_MAX_INFLIGHT = 3
DEFAULT_DIRECT_MAX_ATTEMPTS = 2
DEFAULT_RESERVATION_PLACE_GAP = 0.35
DEFAULT_RESERVATION_PLACE_FAST_RETRY_GAP = 0.8
USER_AGENT = (
    "Mozilla/5.0 (Linux; Android 16; V2366HA Build/BP2A.250605.031.A3; wv) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 Chrome/146.0.7680.177 "
    "Mobile Safari/537.36 XWEB/1460075 MMWEBSDK/20260202 MMWEBID/5120 "
    "REV/89918ef4d19865ac6236e9f77c99567b0ec6d85b "
    "MicroMessenger/8.0.70.3060(0x28004652) WeChat/arm64 Weixin "
    "NetType/WIFI Language/zh_CN ABI/arm64"
)


@dataclass
class HttpResult:
    status: int
    text: str
    elapsed: float
    json_data: dict | None = None
    json_error: bool = False


@dataclass
class DirectClientSlot:
    slot_id: int
    client: object
    fail_stats: Counter


class GuidedBookingState:
    def __init__(self, court_rank):
        self.court_rank = dict(court_rank)
        self.lock = threading.Lock()
        self.slot_states = {}
        self.failure_counts = Counter()
        self.snapshot_count = 0
        self.last_snapshot_at = 0.0

    @staticmethod
    def _key(candidate):
        return candidate["court_id"], candidate["hour"]

    def update_snapshot(self, hour_table, hours, court_pool):
        states = {}
        now = time.monotonic()
        for court_id in court_pool:
            info = hour_table.get(court_id)
            for hour in hours:
                if not info:
                    states[(court_id, hour)] = ("missing", now)
                    continue
                states[(court_id, hour)] = (info["states"].get(hour, "missing"), now)
        with self.lock:
            self.slot_states.update(states)
            self.snapshot_count += 1
            self.last_snapshot_at = now

    def record_attempt_result(self, candidate, result):
        if result in ("success", "dry_run"):
            return
        key = self._key(candidate)
        with self.lock:
            self.failure_counts[(key[0], key[1], result)] += 1

    def sort_candidates(self, candidates):
        with self.lock:
            states = dict(self.slot_states)
            failures = Counter(self.failure_counts)

        def score(item):
            state, _updated_at = states.get(self._key(item), (None, 0.0))
            state_score = 0
            if state == 1:
                state_score = 1000
            elif state == "missing":
                state_score = -200
            elif state is not None:
                state_score = -1000

            failure_penalty = (
                failures[(item["court_id"], item["hour"], "candidate_taken")] * 80
                + failures[(item["court_id"], item["hour"], "business_fail")] * 120
                + failures[(item["court_id"], item["hour"], "retry_delay")] * 40
                + failures[(item["court_id"], item["hour"], "server_retry")] * 20
            )
            return (
                -(state_score - failure_penalty),
                item.get("first_hour_priority", 0),
                self.court_rank.get(item["court_id"], 999),
                item["hour"],
            )

        return sorted(candidates, key=score)


class ReservationPlaceGate:
    def __init__(self, gap_seconds, fast_retry_gap_seconds, logger=None, required_hours=2):
        self.gap_seconds = max(float(gap_seconds or 0), 0.0)
        self.fast_retry_gap_seconds = max(float(fast_retry_gap_seconds or 0), 0.0)
        self.required_hours = 1 if int(required_hours or 1) <= 1 else 2
        self.logger = logger
        self.condition = threading.Condition()
        self.active_label = None
        self.next_allowed_at = 0.0
        self.successes = {}
        self.submit_sequence = 0
        self.retry_owner_key = None
        self.retry_owner_label = None
        self.retry_owner_until = 0.0

    @staticmethod
    def _candidate_key(candidate):
        return candidate["hour"], candidate["court_id"]

    def _has_contiguous_pair_locked(self):
        hours = sorted({candidate["hour"] for candidate in self.successes.values()})
        return any(right - left == 1 for left, right in zip(hours, hours[1:]))

    def _skip_reason_locked(self, candidate):
        if self.required_hours == 1 and self.successes:
            return "single_hour_complete"
        if self._has_contiguous_pair_locked():
            return "contiguous_pair_complete"
        success_hours = {item["hour"] for item in self.successes.values()}
        if not success_hours:
            return ""
        hour = candidate["hour"]
        if hour in success_hours:
            return "hour_already_booked"
        if not any(abs(hour - success_hour) == 1 for success_hour in success_hours):
            return "not_adjacent_to_success"
        return ""

    def _clear_expired_retry_owner_locked(self, now):
        if self.retry_owner_key is not None and self.retry_owner_until > 0 and now >= self.retry_owner_until:
            if self.logger:
                self.logger.warning(
                    f"[reservation gate] retry owner expired label={self.retry_owner_label}"
                )
            self.retry_owner_key = None
            self.retry_owner_label = None
            self.retry_owner_until = 0.0

    def skip_reason(self, candidate):
        with self.condition:
            return self._skip_reason_locked(candidate)

    def successful_candidates(self):
        with self.condition:
            return list(self.successes.values())

    def wait_for_turn(self, candidate, label, retry=False, deadline=None):
        logged_wait = False
        with self.condition:
            while True:
                skip_reason = self._skip_reason_locked(candidate)
                if skip_reason:
                    if self.logger:
                        self.logger.info(
                            f"[reservation gate] skip label={label} reason={skip_reason} "
                            f"hour={candidate['hour']} court={candidate['court_id']}"
                        )
                    return False

                now = time.monotonic()
                if deadline is not None and now >= deadline:
                    if self.logger:
                        self.logger.info(
                            f"[reservation gate] skip label={label} reason=deadline_expired "
                            f"hour={candidate['hour']} court={candidate['court_id']}"
                        )
                    return False
                self._clear_expired_retry_owner_locked(now)
                candidate_key = self._candidate_key(candidate)
                retry_owner_wait = (
                    self.retry_owner_key is not None
                    and not (retry and candidate_key == self.retry_owner_key)
                )
                cooldown = max(self.next_allowed_at - now, 0.0)
                if self.active_label is None and cooldown <= 0 and not retry_owner_wait:
                    self.active_label = label
                    self.submit_sequence += 1
                    if retry and candidate_key == self.retry_owner_key:
                        self.retry_owner_key = None
                        self.retry_owner_label = None
                        self.retry_owner_until = 0.0
                    if self.logger:
                        self.logger.info(
                            f"[reservation gate] allow seq={self.submit_sequence} label={label} "
                            f"retry={int(bool(retry))}"
                        )
                    return True

                if not logged_wait and self.logger:
                    self.logger.info(
                        f"[reservation gate] wait label={label} retry={int(bool(retry))} "
                        f"active={self.active_label or '-'} cooldown={cooldown:.3f}s "
                        f"retry_owner={self.retry_owner_label or '-'}"
                    )
                    logged_wait = True

                wait_seconds = 0.02
                if self.active_label is None and cooldown > 0:
                    wait_seconds = min(max(cooldown, 0.001), 0.05)
                if deadline is not None:
                    wait_seconds = min(wait_seconds, max(deadline - now, 0.001))
                self.condition.wait(wait_seconds)

    def record_response(self, candidate, label, result, fast_retry=False, defer_retry=False):
        with self.condition:
            candidate_key = self._candidate_key(candidate)
            if result == "success":
                self.successes[candidate_key] = candidate
            if fast_retry and not defer_retry:
                self.retry_owner_key = candidate_key
                self.retry_owner_label = label
                self.retry_owner_until = time.monotonic() + self.fast_retry_gap_seconds + 2.0
            elif self.retry_owner_key == candidate_key or result == "success":
                self.retry_owner_key = None
                self.retry_owner_label = None
                self.retry_owner_until = 0.0
            gap = self.fast_retry_gap_seconds if fast_retry else self.gap_seconds
            self.next_allowed_at = time.monotonic() + gap
            self.active_label = None
            if self.logger:
                success_hours = sorted({item["hour"] for item in self.successes.values()})
                self.logger.info(
                    f"[reservation gate] release label={label} result={result} "
                    f"next_gap={gap:.3f}s success_hours={success_hours}"
                )
            self.condition.notify_all()


class KeepAliveClient:
    def __init__(self, base_url, headers, timeout, logger, fail_stats, event_callback=None):
        parsed = urllib.parse.urlsplit(base_url.rstrip("/"))
        if parsed.scheme not in ("http", "https"):
            raise ValueError("--base-url must start with http:// or https://")

        self.scheme = parsed.scheme
        self.host = parsed.hostname
        self.port = parsed.port
        self.base_path = parsed.path.rstrip("/")
        self.headers = dict(headers)
        self.timeout = timeout
        self.logger = logger
        self.fail_stats = fail_stats
        self.event_callback = event_callback
        self.conn = None

    def close(self):
        if self.conn:
            try:
                self.conn.close()
            except Exception:
                pass
        self.conn = None

    def _open(self):
        if self.scheme == "https":
            context = ssl.create_default_context()
            self.conn = http.client.HTTPSConnection(
                self.host,
                self.port,
                timeout=self.timeout,
                context=context,
            )
        else:
            self.conn = http.client.HTTPConnection(
                self.host,
                self.port,
                timeout=self.timeout,
            )

    def request(self, method, endpoint, *, params=None, data=None, timeout=None, label=""):
        path = self._build_path(endpoint, params)
        body = None
        headers = dict(self.headers)

        if data is not None:
            body = urllib.parse.urlencode(data).encode("utf-8")
            headers["Content-Type"] = "application/x-www-form-urlencoded"

        start = time.perf_counter()
        try:
            return self._send_once(method, path, body, headers, label, start)
        except (OSError, http.client.HTTPException):
            self.close()
            try:
                return self._send_once(method, path, body, headers, label, start)
            except Exception as exc:
                elapsed = time.perf_counter() - start
                self.fail_stats[f"{label}_exception"] += 1
                self.logger.error(
                    f"[HTTP] label={label} method={method} exception={redact_text(repr(exc))} "
                    f"elapsed={elapsed:.3f}s outcome=exception"
                )
                self.logger.error(redact_text(traceback.format_exc()))
                if self.event_callback:
                    self.event_callback(
                        label=label,
                        method=method,
                        status=0,
                        elapsed=elapsed,
                        response_bytes=0,
                        outcome="exception",
                    )
                return HttpResult(status=0, text="", elapsed=elapsed)
        except Exception as exc:
            elapsed = time.perf_counter() - start
            self.fail_stats[f"{label}_exception"] += 1
            self.logger.error(
                f"[HTTP] label={label} method={method} exception={redact_text(repr(exc))} "
                f"elapsed={elapsed:.3f}s outcome=exception"
            )
            self.logger.error(redact_text(traceback.format_exc()))
            if self.event_callback:
                self.event_callback(
                    label=label,
                    method=method,
                    status=0,
                    elapsed=elapsed,
                    response_bytes=0,
                    outcome="exception",
                )
            return HttpResult(status=0, text="", elapsed=elapsed)

    def _send_once(self, method, path, body, headers, label, start):
        if self.conn is None:
            self._open()

        self.conn.request(method, path, body=body, headers=headers)
        resp = self.conn.getresponse()
        raw = resp.read()
        elapsed = time.perf_counter() - start
        encoding = self._response_encoding(resp)
        text = raw.decode(encoding, errors="replace")
        try:
            json_data = json.loads(text)
            json_error = False
        except Exception:
            self.fail_stats[f"{label}_json_decode_error"] += 1
            json_data = None
            json_error = True

        outcome, message, data_shape = response_log_summary(resp.status, json_data, json_error)
        self.logger.info(
            f"[HTTP] label={label} method={method} status={resp.status} elapsed={elapsed:.3f}s "
            f"bytes={len(raw)} outcome={outcome} data_shape={data_shape} message={message or '-'}"
        )
        if self.event_callback:
            self.event_callback(
                label=label,
                method=method,
                status=resp.status,
                elapsed=elapsed,
                response_bytes=len(raw),
                outcome=outcome,
            )
        if json_error:
            self.logger.error(f"[HTTP] label={label} JSON解析失败")
            return HttpResult(resp.status, text, elapsed, json_error=True)
        return HttpResult(resp.status, text, elapsed, json_data=json_data)

    def _build_path(self, endpoint, params):
        path = f"{self.base_path}/{endpoint.lstrip('/')}"
        if params:
            query = urllib.parse.urlencode(params)
            path = f"{path}?{query}"
        return path

    @staticmethod
    def _response_encoding(resp):
        content_type = resp.headers.get("Content-Type", "")
        for item in content_type.split(";"):
            item = item.strip()
            if item.lower().startswith("charset="):
                return item.split("=", 1)[1]
        return "utf-8"


def redact_text(text):
    redacted = str(text)
    sensitive_keys = r"token|jsessionid|cardindex|offerid|mastercardnum"
    redacted = re.sub(
        rf"(?i)({sensitive_keys})(=|%3[dD])([^&\s'\"<>]+)",
        lambda match: f"{match.group(1)}{match.group(2)}<redacted>",
        redacted,
    )
    redacted = re.sub(
        rf'(?i)([\"\'](?:{sensitive_keys})[\"\']\s*:\s*[\"\'])[^\"\']*',
        lambda match: f"{match.group(1)}<redacted>",
        redacted,
    )
    return redacted


def response_log_summary(status, payload, json_error=False):
    if json_error:
        return "invalid_json", "", "invalid"
    if not isinstance(payload, dict):
        return "unexpected_json", "", type(payload).__name__

    message = safe_response_message(payload) if payload.get("msg") != "success" else ""
    message = " ".join(message.split())[:160]
    data = payload.get("data")
    if isinstance(data, list):
        data_shape = f"list:{len(data)}"
    elif isinstance(data, dict):
        data_shape = f"dict:{len(data)}"
    else:
        data_shape = type(data).__name__

    if payload.get("msg") == "success":
        return "success", "", data_shape
    if TAKEN_RETRY_TEXT in message:
        return "taken", message, data_shape
    if FAST_RETRY_TEXT in message:
        return "too_fast", message, data_shape
    if "数据错误" in message:
        return "data_error", message, data_shape
    if BUSY_RETRY_TEXT in message:
        return "busy", message, data_shape
    if status >= 500:
        return "server_error", message, data_shape
    return "business_error", message, data_shape


def safe_response_message(payload):
    if not isinstance(payload, dict):
        return ""
    raw_message = payload.get("data")
    if not isinstance(raw_message, (str, int, float, bool)):
        raw_message = payload.get("msg", "")
    return " ".join(redact_text(str(raw_message)).split())[:160]


def redact_json_string_key(text, key):
    marker = f'"{key}":"'
    start = 0
    result = text
    while True:
        idx = result.find(marker, start)
        if idx < 0:
            return result
        value_start = idx + len(marker)
        value_end = result.find('"', value_start)
        if value_end < 0:
            return result
        result = result[:value_start] + "<redacted>" + result[value_end:]
        start = value_start + len("<redacted>")


def fingerprint(value):
    if not value:
        return "empty"
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()[:8]
    return f"len={len(value)} sha256_8={digest}"


class SmartBookingBotV2:
    def __init__(self, args):
        self.args = args
        if not hasattr(self.args, "direct_spec_adjacent_delay"):
            self.args.direct_spec_adjacent_delay = DEFAULT_DIRECT_SPEC_ADJACENT_DELAY
        if not hasattr(self.args, "reservation_place_gap"):
            self.args.reservation_place_gap = DEFAULT_RESERVATION_PLACE_GAP
        if not hasattr(self.args, "reservation_place_fast_retry_gap"):
            self.args.reservation_place_fast_retry_gap = DEFAULT_RESERVATION_PLACE_FAST_RETRY_GAP
        if not hasattr(self.args, "direct_max_inflight"):
            self.args.direct_max_inflight = DEFAULT_DIRECT_MAX_INFLIGHT
        if not hasattr(self.args, "direct_max_attempts"):
            self.args.direct_max_attempts = DEFAULT_DIRECT_MAX_ATTEMPTS
        self.base_url = args.base_url.rstrip("/")
        self.origin = self._origin_from_base_url(self.base_url)
        self.run_started_monotonic = time.monotonic()
        self.run_id = f"{datetime.now().strftime('%Y%m%dT%H%M%S.%f')}-{os.getpid()}"
        self.fail_stats = Counter()
        self.outcome_stats = Counter()
        self.http_metrics = defaultdict(list)
        self.metrics_lock = threading.Lock()
        self.first_booking = None
        self.second_booking = None
        self.reservation_place_gate = None
        self.direct_deadline = None
        self.direct_client_queue = queue.Queue()
        self.direct_client_slots = []
        self.dry_run_candidate = None
        self.last_get_places_error = None
        self.last_get_places_message = ""

        if args.date:
            self.target_date = args.date
        elif args.in_days is not None:
            self.target_date = (datetime.now() + timedelta(days=args.in_days)).strftime("%Y-%m-%d")
        else:
            self.target_date = (datetime.now() + timedelta(days=4)).strftime("%Y-%m-%d")

        self.dt = datetime.strptime(self.target_date, "%Y-%m-%d")
        self.weekday = self.dt.weekday()
        self.range_start_h, self.range_end_h = self._parse_time_range(args.time)
        self.target_duration = args.duration
        self._validate_args()

        excluded_courts = set() if args.all_court else {"ymq4", "ymq5", "ymq12"}
        self.priority_list = [
            court_id for court_id in [f"ymq{i}" for i in (args.priority or [7, 8, 9, 1, 6])]
            if court_id not in excluded_courts
        ]
        self.backup_list = [
            court_id for court_id in [f"ymq{i}" for i in (args.backup or [2, 3, 4, 5, 10, 11, 12])]
            if court_id not in excluded_courts
        ]
        self.court_pool = list(dict.fromkeys(self.priority_list + self.backup_list))
        if not self.court_pool:
            raise ValueError("场地池为空；如需包含靠墙场地，请使用 --all-court")
        self.court_rank = {court_id: idx for idx, court_id in enumerate(self.court_pool)}
        self.excluded_courts = sorted(excluded_courts)

        self._setup_logger()
        self.client = KeepAliveClient(
            self.base_url,
            self._headers(),
            timeout=args.timeout,
            logger=self.logger,
            fail_stats=self.fail_stats,
            event_callback=self._record_http_event,
        )

        self._log_config()
        self._warn_about_credentials()

    def _setup_logger(self):
        os.makedirs("logs", exist_ok=True)
        log_name = (
            f"booking_smart_v2_{self.target_date}_{self.range_start_h}-{self.range_end_h}"
            f"_d{self.target_duration}_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}_{os.getpid()}.log"
        )
        self.log_path = os.path.join("logs", log_name)
        self.logger = logging.getLogger(f"smart_booking_bot_v2_{id(self)}")
        self.logger.setLevel(logging.INFO)
        self.logger.propagate = False
        formatter = logging.Formatter(
            "%(asctime)s.%(msecs)03d | %(levelname)-7s | %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )

        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setLevel(logging.INFO)
        console_handler.setFormatter(formatter)

        file_handler = RotatingFileHandler(
            self.log_path,
            maxBytes=5 * 1024 * 1024,
            backupCount=3,
            encoding="utf-8",
        )
        file_handler.setLevel(logging.INFO)
        file_handler.setFormatter(formatter)

        for handler in self.logger.handlers:
            handler.close()
        self.logger.handlers.clear()
        self.logger.addHandler(console_handler)
        self.logger.addHandler(file_handler)

    def log_event(self, event, **fields):
        payload = {
            "event": event,
            "engine_version": BOOKING_ENGINE_VERSION,
            "run_id": self.run_id,
            "offset_ms": round((time.monotonic() - self.run_started_monotonic) * 1000),
        }
        payload.update(fields)
        self.logger.info(f"[EVENT] {json.dumps(payload, ensure_ascii=False, sort_keys=True)}")

    def _record_http_event(self, *, label, method, status, elapsed, response_bytes, outcome):
        endpoint = next(
            (
                name
                for name in (
                    "reservationPlace",
                    "getUseCardInfo",
                    "getOfferInfo",
                    "canBook",
                    "get_places",
                    "getPlaceType",
                    "getPenaltyRules",
                )
                if name in label
            ),
            "other",
        )
        with self.metrics_lock:
            self.http_metrics[endpoint].append(float(elapsed))
            self.outcome_stats[f"{endpoint}:{outcome}"] += 1
        self.log_event(
            "http_response",
            label=label,
            endpoint=endpoint,
            method=method,
            status=status,
            elapsed_ms=round(elapsed * 1000),
            response_bytes=response_bytes,
            outcome=outcome,
        )

    def _prepare_direct_clients(self, *, prewarm=False):
        if self.direct_client_slots:
            return
        for slot_id in range(1, self.args.direct_max_inflight + 1):
            fail_stats = Counter()
            client = KeepAliveClient(
                self.base_url,
                self._headers(),
                timeout=self.args.timeout,
                logger=self.logger,
                fail_stats=fail_stats,
                event_callback=self._record_http_event,
            )
            slot = DirectClientSlot(slot_id=slot_id, client=client, fail_stats=fail_stats)
            self.direct_client_slots.append(slot)
            if prewarm:
                result = client.request(
                    "GET",
                    "/place/getPlaceType",
                    params={"token": self.args.token, "shopNum": SHOP_NUM},
                    label=f"prewarm_worker_{slot_id}_getPlaceType",
                )
                self.log_event(
                    "worker_client_prewarm",
                    client_slot=slot_id,
                    ok=int(self._response_success(result)),
                    elapsed_ms=round(result.elapsed * 1000),
                )
            self.direct_client_queue.put(slot)

    def _close_direct_clients(self):
        for slot in self.direct_client_slots:
            slot.client.close()
        self.direct_client_slots.clear()
        while not self.direct_client_queue.empty():
            try:
                self.direct_client_queue.get_nowait()
            except queue.Empty:
                break

    def _headers(self):
        headers = {
            "Connection": "keep-alive",
            "User-Agent": USER_AGENT,
            "Accept": "application/json, text/plain, */*",
            "Content-Type": "application/x-www-form-urlencoded",
            "Origin": self.origin,
            "X-Requested-With": "com.tencent.mm",
            "Referer": f"{self.origin}/easyserp/index.html?token={self.args.token}",
            "Accept-Encoding": "identity",
            "Accept-Language": "zh-CN,zh;q=0.9,en-US;q=0.8,en;q=0.7",
        }
        if self.args.jsessionid:
            headers["Cookie"] = f"JSESSIONID={self.args.jsessionid}"
        return headers

    def _log_config(self):
        self.logger.info("=" * 110)
        self.logger.info(
            f"羽毛球场地预约脚本启动（engine={BOOKING_ENGINE_VERSION} mode={self.args.booking_mode} run_id={self.run_id}）"
        )
        self.logger.info(
            f"[配置] 日期={self.target_date} ({self._weekday_name()}) | "
            f"目标范围={self.range_start_h}:00-{self.range_end_h}:00 | "
            f"目标时长={self.target_duration}小时 | dry_run={self.args.dry_run} | check_session={self.args.check_session}"
        )
        self.logger.info(
            f"[配置] base_url={self.base_url} | window_seconds={self.args.window_seconds}s | "
            f"poll_interval={self.args.poll_interval}s | error_backoff={self.args.error_backoff}s"
        )
        self.logger.info(
            f"[配置] rounds={self.args.rounds} | second_rounds={self.args.second_rounds} | "
            f"step_sleep={self.args.step_sleep}s"
        )
        if self.args.booking_mode in (BOOKING_MODE_DIRECT_FAST, BOOKING_MODE_GUIDED_FAST):
            self.logger.info(
                f"[配置] direct_spec_adjacent_delay={self.args.direct_spec_adjacent_delay}s | "
                f"direct_max_inflight={self.args.direct_max_inflight} | "
                f"direct_max_attempts={self.args.direct_max_attempts} | "
                f"reservation_place_gap={self.args.reservation_place_gap}s | "
                f"reservation_place_fast_retry_gap={self.args.reservation_place_fast_retry_gap}s"
            )
        if self.args.booking_mode == BOOKING_MODE_GUIDED_FAST:
            self.logger.info(
                f"[配置] guide_interval={self.args.guide_interval}s | "
                f"guide_max_inflight={self.args.guide_max_inflight}"
            )
        self.logger.info(f"[配置] 场地池={self.court_pool}")
        if self.excluded_courts:
            self.logger.info(f"[配置] 默认排除靠墙场地={self.excluded_courts}；使用 --all-court 可包含")
        self.logger.info(
            f"[凭证] token={fingerprint(self.args.token)} | JSESSIONID={fingerprint(self.args.jsessionid)} | "
            f"card_index={fingerprint(self.args.card_index)}"
        )
        self.log_event(
            "run_config",
            mode=self.args.booking_mode,
            target_date=self.target_date,
            range_start=self.range_start_h,
            range_end=self.range_end_h,
            duration=self.target_duration,
            window_seconds=self.args.window_seconds,
            direct_max_inflight=self.args.direct_max_inflight,
            direct_max_attempts=self.args.direct_max_attempts,
            reservation_place_gap=self.args.reservation_place_gap,
            reservation_place_fast_retry_gap=self.args.reservation_place_fast_retry_gap,
            court_count=len(self.court_pool),
        )
        self.logger.info("=" * 110)

    def _warn_about_credentials(self):
        if self.args.jsessionid and len(self.args.jsessionid) != 32:
            self.logger.warning("[凭证] JSESSIONID 长度不是32，可能复制了多余字符；建议使用 -j 传入刚抓包的值")

    def check_session(self):
        self.logger.info("[会话检测] 开始检测预约前置链路")
        failures = []
        project_info = self._check_project_info()

        checks = [
            (
                "getPlaceType",
                "GET",
                "/place/getPlaceType",
                {"token": self.args.token, "shopNum": SHOP_NUM},
                None,
            ),
            (
                "getPlaceByShopNum",
                "GET",
                "/place/getPlaceByShopNum",
                {"shopNum": SHOP_NUM, "token": self.args.token, "typeId": ""},
                None,
            ),
            (
                "get_places",
                "GET",
                "/datediscount/getPlaceInfoByShortNameDiscount",
                {
                    "shopNum": SHOP_NUM,
                    "dateymd": self.target_date,
                    "shortName": SHORT_NAME,
                    "token": self.args.token,
                },
                None,
            ),
            (
                "getUseCardInfo",
                "POST",
                "/common/getUseCardInfo",
                None,
                {
                    "token": self.args.token,
                    "shopNum": SHOP_NUM,
                    "projectType": PROJECT_TYPE,
                    "projectInfo": json.dumps(project_info, ensure_ascii=False),
                },
            ),
            (
                "getOfferInfo",
                "POST",
                "/common/getOfferInfo",
                None,
                {
                    "token": self.args.token,
                    "payMoney": "120.00",
                    "shopNum": SHOP_NUM,
                    "projectType": PROJECT_TYPE,
                    "projectInfo": json.dumps(project_info, ensure_ascii=False),
                },
            ),
        ]

        for name, method, endpoint, params, data in checks:
            result = self.client.request(method, endpoint, params=params, data=data, label=f"check_{name}")
            ok, reason = self._session_check_result(result)
            if ok:
                self.logger.info(f"[会话检测] {name}=ok")
            else:
                failures.append(f"{name}:{reason}")
                self.logger.warning(f"[会话检测] {name}=fail reason={reason}")

        if failures:
            self.logger.warning("[会话检测] 结论：预约前置链路异常；请手动登录后重新抓包提取 JSESSIONID")
            self.logger.warning(f"[会话检测] 失败项：{failures}")
            return False

        self.logger.info("[会话检测] 结论：预约前置链路当前可用")
        self.logger.info("[会话检测] 说明：当前系统的前置接口主要认 token，JSESSIONID 只能做格式和随请求携带检查")
        return True

    def _check_project_info(self):
        court_id = self.court_pool[0] if self.court_pool else "ymq7"
        court_number = court_id.replace("ymq", "")
        start_time = f"{self.range_start_h:02d}:00"
        end_time = f"{self.range_start_h + 1:02d}:00"
        return [
            {
                "day": self.target_date,
                "startTime": start_time,
                "endTime": end_time,
                "placeShortName": court_id,
                "name": f"羽毛球{court_number}",
                "stageTypeShortName": SHORT_NAME,
            }
        ]

    @staticmethod
    def _session_check_result(result):
        if not result:
            return False, "no_result"
        if result.status >= 500 or result.status == 0:
            return False, f"http_{result.status}"
        if result.json_error or result.json_data is None:
            return False, "invalid_json"
        if not isinstance(result.json_data, dict):
            return False, f"unexpected_json_type={type(result.json_data).__name__}"
        msg = result.json_data.get("msg")
        if msg == "success":
            return True, ""
        data = safe_response_message(result.json_data)
        if any(word in data for word in ("登录", "session", "Session", "JSESSIONID", "无效", "过期")):
            return False, data
        return False, f"msg={msg} data={data}"

    @staticmethod
    def _origin_from_base_url(base_url):
        parsed = urllib.parse.urlsplit(base_url)
        return f"{parsed.scheme}://{parsed.netloc}"

    @staticmethod
    def _parse_time_range(value):
        try:
            start_txt, end_txt = value.split("-", 1)
            return int(start_txt), int(end_txt)
        except Exception as exc:
            raise ValueError("时间范围格式必须类似 17-21") from exc

    def _validate_args(self):
        if self.range_end_h <= self.range_start_h:
            raise ValueError("结束时间必须大于开始时间")
        if self.target_duration not in (1, 2):
            raise ValueError("--duration 只能是 1 或 2")
        if self.target_duration == 2 and self.range_end_h - self.range_start_h < 2:
            raise ValueError("当 --duration 2 时，时间范围长度至少为2小时")
        if self.args.window_seconds <= 0:
            raise ValueError("--window-seconds 必须大于0")
        if self.args.poll_interval <= 0:
            raise ValueError("--poll-interval 必须大于0")
        if self.args.error_backoff <= 0:
            raise ValueError("--error-backoff 必须大于0")
        if self.args.guide_interval <= 0:
            raise ValueError("--guide-interval 必须大于0")
        if self.args.guide_max_inflight <= 0:
            raise ValueError("--guide-max-inflight 必须大于0")
        if self.args.direct_spec_adjacent_delay < 0:
            raise ValueError("--direct-spec-adjacent-delay 不能小于0")
        if self.args.reservation_place_gap < 0:
            raise ValueError("--reservation-place-gap 不能小于0")
        if self.args.reservation_place_fast_retry_gap < 0:
            raise ValueError("--reservation-place-fast-retry-gap 不能小于0")
        if self.args.direct_max_inflight <= 0:
            raise ValueError("--direct-max-inflight 必须大于0")
        if self.args.direct_max_attempts <= 0:
            raise ValueError("--direct-max-attempts 必须大于0")

    def _weekday_name(self):
        return ["周一", "周二", "周三", "周四", "周五", "周六", "周日"][self.weekday]

    def wait_for_start(self):
        if self.args.force:
            self.logger.info("[模式] force 模式，立即执行")
            self.prewarm()
            return

        now = datetime.now()
        target = now.replace(hour=12, minute=0, second=0, microsecond=1000)
        prewarm_at = target - timedelta(seconds=PREWARM_SECONDS)

        if now >= target:
            self.logger.info("[时间] 当前已过 12:00，预热后立即执行")
            self.prewarm()
            return

        if now < prewarm_at:
            self.logger.info(
                f"[等待] 当前时间 {now.strftime('%H:%M:%S.%f')[:-3]}，等待到预热时间 "
                f"{prewarm_at.strftime('%H:%M:%S.%f')[:-3]}"
            )
            self._wait_until(prewarm_at)

        self.prewarm()
        self.logger.info("[等待] 预热完成，等待到 12:00:00.001")
        self._wait_until(target)
        self.logger.info(f"[触发] 实际启动时间 {datetime.now().strftime('%H:%M:%S.%f')[:-3]}")

    def _wait_until(self, target):
        last_print_sec = None
        while True:
            now = datetime.now()
            if now >= target:
                return

            diff = (target - now).total_seconds()
            if diff > 1:
                time.sleep(min(0.2, diff - 0.5))
            elif diff > 0.1:
                time.sleep(0.02)
            else:
                time.sleep(0.001)

            now2 = datetime.now()
            if now2.second != last_print_sec and now2.microsecond < 50000:
                last_print_sec = now2.second
                remaining = max((target - now2).total_seconds(), 0)
                self.logger.info(f"[等待] 距离目标时间还有 {remaining:.3f}s")

    def prewarm(self):
        self.logger.info("[预热] 开始预热 TLS/session")
        self.client.request(
            "GET",
            "/place/getPlaceType",
            params={"token": self.args.token, "shopNum": SHOP_NUM},
            label="prewarm_getPlaceType",
        )
        self.client.request(
            "GET",
            "/place/getPenaltyRules",
            params={
                "stId": "1",
                "stShortName": SHORT_NAME,
                "token": self.args.token,
                "shopNum": SHOP_NUM,
                "systemDate": datetime.now().strftime("%Y-%m-%d"),
            },
            label="prewarm_getPenaltyRules",
        )
        if (
            self.args.booking_mode in (BOOKING_MODE_DIRECT_FAST, BOOKING_MODE_GUIDED_FAST)
            and not self.args.dry_run
            and not self.args.check_session
        ):
            self._prepare_direct_clients(prewarm=True)
        self.logger.info("[预热] 结束")

    def get_places(self):
        self.last_get_places_error = None
        self.last_get_places_message = ""
        result = self.client.request(
            "GET",
            "/datediscount/getPlaceInfoByShortNameDiscount",
            params={
                "shopNum": SHOP_NUM,
                "dateymd": self.target_date,
                "shortName": SHORT_NAME,
                "token": self.args.token,
            },
            label="get_places",
        )

        if result.status >= 500 or result.status == 0:
            self.last_get_places_error = "server"
            self.fail_stats["get_places_server_error"] += 1
            self.logger.warning(f"[get_places] 服务错误 status={result.status}")
            return None

        if result.json_error or not result.json_data:
            self.last_get_places_error = "json"
            self.fail_stats["get_places_json_error"] += 1
            self.logger.warning("[get_places] 返回不是有效 JSON")
            return None

        msg = result.json_data.get("msg")
        if msg != "success":
            message = safe_response_message(result.json_data)
            self.last_get_places_message = message
            if BUSY_RETRY_TEXT in message:
                self.last_get_places_error = "busy"
            else:
                self.last_get_places_error = "business"
            self.fail_stats[f"get_places_{self.last_get_places_error}"] += 1
            self.logger.warning(f"[get_places] msg={msg} data={message}")
            return None

        places = result.json_data.get("data", {}).get("placeArray", [])
        self.logger.info(f"[get_places] 成功，返回场地数={len(places)}，耗时={result.elapsed:.3f}s")
        if not places:
            self.fail_stats["get_places_empty"] += 1
        return places

    def build_hour_slot_table(self, places):
        result = {}
        for place in places:
            project = place.get("projectName", {})
            court_id = project.get("shortname")
            if court_id not in self.court_pool:
                continue

            hour_slots = {}
            hour_states = {}
            for slot in place.get("projectInfo", []):
                hour = self._slot_start_hour(slot)
                if hour is None:
                    continue
                if self.range_start_h <= hour < self.range_end_h:
                    hour_slots[hour] = slot
                    hour_states[hour] = slot.get("state")

            result[court_id] = {
                "fullname": project.get("name", court_id),
                "slots": hour_slots,
                "states": hour_states,
            }
        return result

    @staticmethod
    def _slot_start_hour(slot):
        start_raw = slot.get("starttime", "")
        if not start_raw:
            return None
        try:
            return int(start_raw[:5].split(":")[0])
        except Exception:
            return None

    def log_snapshot(self, hour_table, hours=None):
        if hours is None:
            hours = range(self.range_start_h, self.range_end_h)
        for court_id in self.court_pool:
            info = hour_table.get(court_id)
            if not info:
                self.logger.info(f"[场地快照] {court_id} -> court_not_found")
                continue
            summary = [
                f"{h}:00-{h + 1}:00:state={info['states'].get(h, 'missing')}"
                for h in hours
            ]
            self.logger.info(f"[场地快照] {info['fullname']}({court_id}) -> {summary}")

    def log_bookable_hours(self, hour_table, hours=None):
        if hours is None:
            hours = list(range(self.range_start_h, self.range_end_h))
        any_bookable = False
        for hour in hours:
            courts = []
            for court_id in self.court_pool:
                info = hour_table.get(court_id)
                if not info:
                    continue
                slot = info["slots"].get(hour)
                if slot and slot.get("state") == 1:
                    courts.append(f"{info['fullname']}({court_id})")
            if courts:
                any_bookable = True
            self.logger.info(f"[可约小时] {hour}:00-{hour + 1}:00 -> {courts}")
        if not any_bookable:
            self.logger.info("[可约小时汇总] 目标范围内没有任何 state=1 的小时段")

    def get_bookable_count_for_hour(self, hour_table, hour):
        count = 0
        for court_id in self.court_pool:
            info = hour_table.get(court_id)
            if not info:
                continue
            slot = info["slots"].get(hour)
            if slot and slot.get("state") == 1:
                count += 1
        return count

    def first_hour_priority(self, hour):
        if self.target_duration != 2 or self.range_end_h - self.range_start_h <= 2:
            return 0
        center_twice = self.range_start_h + self.range_end_h - 1
        return abs(hour * 2 - center_twice)

    def generate_first_candidates(self, hour_table):
        candidates = []
        for hour in range(self.range_start_h, self.range_end_h):
            left_count = self.get_bookable_count_for_hour(hour_table, hour - 1)
            right_count = self.get_bookable_count_for_hour(hour_table, hour + 1)
            adjacency_count = left_count + right_count
            adjacency_max = max(left_count, right_count)

            for court_id in self.court_pool:
                info = hour_table.get(court_id)
                if not info:
                    continue
                slot = info["slots"].get(hour)
                if slot and slot.get("state") == 1:
                    candidates.append(
                        {
                            "hour": hour,
                            "court_id": court_id,
                            "court_name": info["fullname"],
                            "slot": slot,
                            "left_count": left_count,
                            "right_count": right_count,
                            "adjacency_count": adjacency_count,
                            "adjacency_max": adjacency_max,
                            "first_hour_priority": self.first_hour_priority(hour),
                        }
                    )

        candidates.sort(
            key=lambda item: (
                item["first_hour_priority"],
                -item["adjacency_count"] if self.target_duration == 2 else 0,
                -item["adjacency_max"] if self.target_duration == 2 else 0,
                self.court_rank.get(item["court_id"], 999),
                item["hour"],
            )
        )
        return candidates

    def generate_second_target_hours(self, booked_hour):
        target_hours = []
        if booked_hour - 1 >= self.range_start_h:
            target_hours.append(booked_hour - 1)
        if booked_hour + 1 < self.range_end_h:
            target_hours.append(booked_hour + 1)
        return target_hours

    def generate_second_candidates(self, hour_table, booked_hour):
        target_hours = self.generate_second_target_hours(booked_hour)
        hour_counts = {hour: self.get_bookable_count_for_hour(hour_table, hour) for hour in target_hours}
        preferred_hours = sorted(target_hours, key=lambda hour: (-hour_counts[hour], hour))
        candidates = []
        for hour in preferred_hours:
            for court_id in self.court_pool:
                info = hour_table.get(court_id)
                if not info:
                    continue
                slot = info["slots"].get(hour)
                if slot and slot.get("state") == 1:
                    candidates.append(
                        {
                            "hour": hour,
                            "court_id": court_id,
                            "court_name": info["fullname"],
                            "slot": slot,
                            "bookable_count_same_hour": hour_counts[hour],
                        }
                    )
        candidates.sort(
            key=lambda item: (
                -item["bookable_count_same_hour"],
                self.court_rank.get(item["court_id"], 999),
                item["hour"],
            )
        )
        return candidates

    def generate_direct_first_candidates(self):
        candidates = []
        for court_id in self.court_pool:
            for hour in range(self.range_start_h, self.range_end_h):
                if self.target_duration == 2 and not self.generate_second_target_hours(hour):
                    continue
                candidates.append(
                    self._synthetic_candidate(
                        hour,
                        court_id,
                        left_count=0,
                        right_count=0,
                        first_hour_priority=self.first_hour_priority(hour),
                    )
                )
        candidates.sort(
            key=lambda item: (
                item["first_hour_priority"],
                self.court_rank.get(item["court_id"], 999),
                item["hour"],
            )
        )
        return candidates

    def generate_direct_second_candidates(self, booked_hour):
        candidates = []
        for court_id in self.court_pool:
            for hour in self.generate_second_target_hours(booked_hour):
                candidates.append(
                    self._synthetic_candidate(
                        hour,
                        court_id,
                        bookable_count_same_hour=0,
                    )
                )
        return candidates

    def generate_direct_speculative_center_candidates(self, candidates, center_hour, per_hour_limit=3):
        return [candidate for candidate in candidates if candidate["hour"] == center_hour][:per_hour_limit]

    def generate_direct_speculative_adjacent_candidates(self, center_hour, per_hour_limit=3):
        candidates = []
        for hour in self.generate_second_target_hours(center_hour):
            for court_id in self.court_pool[:per_hour_limit]:
                candidates.append(
                    self._synthetic_candidate(
                        hour,
                        court_id,
                        bookable_count_same_hour=0,
                        speculative_anchor_hour=center_hour,
                    )
                )
        return candidates

    def _synthetic_candidate(self, hour, court_id, **extra):
        left_count = extra.pop("left_count", 0)
        right_count = extra.pop("right_count", 0)
        candidate = {
            "hour": hour,
            "court_id": court_id,
            "court_name": self._court_name(court_id),
            "slot": {
                "starttime": f"{hour:02d}:00",
                "endtime": f"{hour + 1:02d}:00",
            },
            "left_count": left_count,
            "right_count": right_count,
            "adjacency_count": left_count + right_count,
            "adjacency_max": max(left_count, right_count),
        }
        candidate.update(extra)
        return candidate

    @staticmethod
    def _court_name(court_id):
        number = str(court_id).replace("ymq", "")
        return f"羽毛球{number}" if number else str(court_id)

    def log_first_candidates(self, candidates):
        if not candidates:
            self.logger.info("[第一阶段候选] 未生成任何单小时候选")
            return
        for item in candidates[:30]:
            self.logger.info(
                f"[第一阶段候选] {item['hour']}:00-{item['hour'] + 1}:00 | "
                f"{item['court_name']}({item['court_id']}) | "
                f"left={item['left_count']} right={item['right_count']} "
                f"adj_total={item['adjacency_count']} adj_max={item['adjacency_max']}"
            )
        if len(candidates) > 30:
            self.logger.info(f"[第一阶段候选] 仅显示前30个，候选总数={len(candidates)}")

    def log_second_candidates(self, candidates, booked_hour):
        target_hours = self.generate_second_target_hours(booked_hour)
        if not candidates:
            self.logger.info(
                f"[第二阶段候选] 围绕已预约小时 {booked_hour}:00-{booked_hour + 1}:00，"
                f"相邻目标小时={target_hours}，未生成任何候选"
            )
            return
        self.logger.info(
            f"[第二阶段候选] 围绕已预约小时 {booked_hour}:00-{booked_hour + 1}:00，"
            f"相邻目标小时={target_hours}"
        )
        for item in candidates[:30]:
            self.logger.info(
                f"[第二阶段候选] {item['hour']}:00-{item['hour'] + 1}:00 | "
                f"{item['court_name']}({item['court_id']}) | "
                f"same_hour_bookable={item['bookable_count_same_hour']}"
            )
        if len(candidates) > 30:
            self.logger.info(f"[第二阶段候选] 仅显示前30个，候选总数={len(candidates)}")

    @staticmethod
    def _booking_candidate_text(candidate):
        return (
            f"{candidate['hour']}:00-{candidate['hour'] + 1}:00 "
            f"{candidate['court_name']}({candidate['court_id']})"
        )

    def log_direct_speculative_candidates(self, center_candidates, adjacent_candidates):
        grouped_hours = {}
        for candidate in center_candidates + adjacent_candidates:
            grouped_hours.setdefault(candidate["hour"], []).append(candidate)

        for candidate in center_candidates:
            self.logger.info(
                f"[直抢投机] 中间小时候选：{self._booking_candidate_text(candidate)}"
            )
        if not center_candidates and not adjacent_candidates:
            self.logger.info("[直抢投机] 范围内没有可投机的相邻小时候选")
            return

        hours_text = ", ".join(
            f"{hour}:00={len(candidates)}个场地" for hour, candidates in sorted(grouped_hours.items())
        )
        self.logger.info(f"[直抢投机] 初始投机候选：{hours_text}")
        for candidate in adjacent_candidates:
            self.logger.info(f"[直抢投机候选] {self._booking_candidate_text(candidate)}")

    def attempt_single_hour_booking(
        self,
        candidate,
        label,
        round_index,
        candidate_index,
        candidate_total,
        client=None,
        failure_stats=None,
    ):
        request_client = client or self.client
        hour = candidate["hour"]
        court_id = candidate["court_id"]
        court_name = candidate["court_name"]
        slot = candidate["slot"]
        old_total, actual_total = self._price_for_slot(slot, hour)
        start_time = slot["starttime"][:5]
        end_time = slot["endtime"][:5]

        self.logger.info(
            f"[尝试] label={label} | 轮次={round_index} | 候选={candidate_index}/{candidate_total} | "
            f"时段={start_time}-{end_time} | 场地={court_name}({court_id})"
        )
        self.log_event(
            "candidate_attempt_start",
            label=label,
            round=round_index,
            candidate_index=candidate_index,
            candidate_total=candidate_total,
            hour=hour,
            court=court_id,
        )

        canbook_fields = [
            {
                "day": self.target_date,
                "startTime": start_time,
                "endTime": end_time,
                "placeShortName": court_id,
            }
        ]
        field_info_full = [
            {
                "day": self.target_date,
                "startTime": start_time,
                "endTime": end_time,
                "placeShortName": court_id,
                "name": court_name,
                "stageTypeShortName": SHORT_NAME,
            }
        ]

        self.logger.info(
            f"[下单参数] label={label} | canBook_fieldinfo={canbook_fields} | "
            f"reservation_fieldinfo={field_info_full} | oldTotal={old_total:.2f} | total={actual_total:.2f}"
        )

        if self.args.dry_run:
            self.dry_run_candidate = candidate
            self.logger.info("[dry-run] 已生成下单参数，未调用 canBook/getOfferInfo/getUseCardInfo/reservationPlace")
            return "dry_run"

        r0 = request_client.request(
            "POST",
            "/place/canBook",
            data={
                "fieldinfo": json.dumps(canbook_fields, ensure_ascii=False),
                "shopNum": SHOP_NUM,
                "token": self.args.token,
            },
            label=f"{label}_canBook",
        )
        if not self._response_success(r0):
            return self._classify_booking_failure(r0, f"{label}_canBook", failure_stats)

        self._sleep_between_booking_calls()
        r1 = request_client.request(
            "POST",
            "/common/getOfferInfo",
            data={
                "token": self.args.token,
                "payMoney": f"{old_total:.2f}",
                "shopNum": SHOP_NUM,
                "projectType": PROJECT_TYPE,
                "projectInfo": json.dumps(field_info_full, ensure_ascii=False),
            },
            label=f"{label}_getOfferInfo",
        )
        if not self._response_success(r1):
            return self._classify_booking_failure(r1, f"{label}_getOfferInfo", failure_stats)

        self._sleep_between_booking_calls()
        r2 = request_client.request(
            "POST",
            "/common/getUseCardInfo",
            data={
                "token": self.args.token,
                "shopNum": SHOP_NUM,
                "projectType": PROJECT_TYPE,
                "projectInfo": json.dumps(field_info_full, ensure_ascii=False),
            },
            label=f"{label}_getUseCardInfo",
        )
        if not self._response_success(r2):
            return self._classify_booking_failure(r2, f"{label}_getUseCardInfo", failure_stats)

        self._sleep_between_booking_calls()
        reservation_data = {
            "token": self.args.token,
            "shopNum": SHOP_NUM,
            "fieldinfo": json.dumps(field_info_full, ensure_ascii=False),
            "oldTotal": f"{old_total:.2f}",
            "cardPayType": "0",
            "type": "羽毛球",
            "offerId": self.args.card_index,
            "offerType": PROJECT_TYPE,
            "total": f"{actual_total:.2f}",
            "premerother": "",
            "cardIndex": self.args.card_index,
            "masterCardNum": "",
            "zengzhiMoney": "0",
        }
        reservation_label = f"{label}_reservationPlace"
        max_reservation_attempts = 1 if self.reservation_place_gate else 2
        for reservation_attempt in range(1, max_reservation_attempts + 1):
            active_gate = self.reservation_place_gate
            is_retry_attempt = reservation_attempt > 1
            if active_gate and not active_gate.wait_for_turn(
                candidate,
                reservation_label,
                retry=is_retry_attempt,
                deadline=self.direct_deadline,
            ):
                return "candidate_skipped"

            r3 = None
            try:
                r3 = request_client.request(
                    "POST",
                    "/place/reservationPlace",
                    data=reservation_data,
                    label=reservation_label,
                )
            finally:
                if active_gate:
                    is_fast_response = (
                        r3 is not None
                        and not self._response_success(r3)
                        and self._failure_data(r3).find(FAST_RETRY_TEXT) >= 0
                    )
                    gate_result = "success" if self._response_success(r3) else "fast_retry" if is_fast_response else "failed"
                    active_gate.record_response(
                        candidate,
                        reservation_label,
                        gate_result,
                        fast_retry=is_fast_response,
                        defer_retry=max_reservation_attempts == 1,
                    )

            if self._response_success(r3):
                self.logger.info(
                    f"[成功] label={label} 预约成功 | 日期={self.target_date} | "
                    f"时段={start_time}-{end_time} | 场地={court_name}({court_id})"
                )
                return "success"

            if (
                reservation_attempt < max_reservation_attempts
                and self._failure_data(r3).find(FAST_RETRY_TEXT) >= 0
            ):
                self._record_booking_failure(r3, reservation_label, failure_stats)
                self.logger.warning(
                    f"[重试] label={reservation_label} 操作过快，等待后重试同一候选 | "
                    f"attempt={reservation_attempt + 1}/{max_reservation_attempts}"
                )
                self._sleep_after_fast_retry()
                continue

            return self._classify_booking_failure(
                r3,
                reservation_label,
                failure_stats,
                sleep_on_fast_retry=False,
            )

    def _price_for_slot(self, slot, hour):
        old_total = self._number_from_slot(slot, "oldMoney")
        if old_total is None:
            old_total = self._number_from_slot(slot, "money")
        if old_total is None:
            old_total = 120.0 if hour >= 15 else 80.0

        actual_total = self._discounted_total(old_total)
        return old_total, actual_total

    @staticmethod
    def _number_from_slot(slot, key):
        try:
            value = slot.get(key)
            if value is None:
                return None
            return float(value)
        except Exception:
            return None

    @staticmethod
    def _discounted_total(old_total):
        return round(old_total * 0.25, 2)

    @staticmethod
    def _response_success(result):
        return bool(result and result.json_data and result.json_data.get("msg") == "success")

    @staticmethod
    def _failure_data(result):
        if result and result.json_data:
            return safe_response_message(result.json_data)
        return ""

    def _record_booking_failure(self, result, label, failure_stats=None):
        stats = failure_stats if failure_stats is not None else self.fail_stats
        stats[f"{label}_not_success"] += 1
        data = self._failure_data(result)
        self.logger.warning(f"[失败] label={label} data={data or 'empty'}")
        return data

    def _classify_booking_failure(self, result, label, failure_stats=None, sleep_on_fast_retry=True):
        data = self._record_booking_failure(result, label, failure_stats)
        if TAKEN_RETRY_TEXT in data:
            return "candidate_taken"
        if FAST_RETRY_TEXT in data:
            if sleep_on_fast_retry:
                self._sleep_after_fast_retry()
            return "retry_delay"
        if result and result.status >= 500:
            return "server_retry"
        return "business_fail"

    def _sleep_after_fast_retry(self):
        time.sleep(0.8)

    def _sleep_between_booking_calls(self):
        if self.args.step_sleep > 0:
            time.sleep(self.args.step_sleep)

    def run_first_stage(self):
        self.logger.info("-" * 110)
        self.logger.info("[第一阶段] 开始搜索目标范围内的可约小时")
        deadline = time.monotonic() + self.args.window_seconds
        round_index = 0
        max_rounds = self._max_rounds(self.args.rounds)
        server_backoff = self.args.error_backoff

        while time.monotonic() < deadline and round_index < max_rounds:
            round_index += 1
            self.logger.info("-" * 110)
            self.logger.info(f"[第一阶段] 开始第 {round_index}/{max_rounds} 轮")
            places = self.get_places()
            if not places:
                server_backoff = self._sleep_after_get_places_failure(server_backoff, deadline)
                continue

            server_backoff = self.args.error_backoff
            hour_table = self.build_hour_slot_table(places)
            self.log_snapshot(hour_table)
            self.log_bookable_hours(hour_table)
            candidates = self.generate_first_candidates(hour_table)
            self.logger.info(f"[第一阶段] 候选总数={len(candidates)}")
            self.log_first_candidates(candidates)

            if self.args.dry_run:
                if candidates:
                    self.attempt_single_hour_booking(candidates[0], "first", round_index, 1, len(candidates))
                else:
                    self.logger.info("[dry-run] 未发现候选，已完成一次安全查询")
                return "dry_run"

            for idx, candidate in enumerate(candidates, start=1):
                result = self.attempt_single_hour_booking(candidate, "first", round_index, idx, len(candidates))
                if result == "success":
                    self.first_booking = candidate
                    return "success"
                if result in ("candidate_taken", "candidate_skipped"):
                    continue
                if result in ("retry_delay", "server_retry"):
                    break

            self._sleep_until_next_poll(deadline)

        return "failed"

    def run_second_stage(self):
        if self.target_duration != 2:
            return "skipped"
        if not self.first_booking:
            return "failed"

        booked_hour = self.first_booking["hour"]
        target_hours = self.generate_second_target_hours(booked_hour)
        self.logger.info("-" * 110)
        self.logger.info(
            f"[第二阶段] 第一单已成功：{booked_hour}:00-{booked_hour + 1}:00 "
            f"{self.first_booking['court_name']}({self.first_booking['court_id']})"
        )
        self.logger.info(f"[第二阶段] 开始只搜索相邻小时={target_hours}")

        if not target_hours:
            self.logger.warning("[第二阶段] 第一单位于边界，范围内不存在相邻小时")
            return "failed"

        deadline = time.monotonic() + self.args.window_seconds
        round_index = 0
        max_rounds = self._max_rounds(self.args.second_rounds)
        server_backoff = self.args.error_backoff

        while time.monotonic() < deadline and round_index < max_rounds:
            round_index += 1
            self.logger.info("-" * 110)
            self.logger.info(f"[第二阶段] 开始第 {round_index}/{max_rounds} 轮 | 目标小时={target_hours}")

            places = self.get_places()
            if not places:
                server_backoff = self._sleep_after_get_places_failure(server_backoff, deadline)
                continue

            server_backoff = self.args.error_backoff
            hour_table = self.build_hour_slot_table(places)
            self.log_bookable_hours(hour_table, hours=target_hours)
            candidates = self.generate_second_candidates(hour_table, booked_hour)
            self.logger.info(f"[第二阶段] 候选总数={len(candidates)}")
            self.log_second_candidates(candidates, booked_hour)

            for idx, candidate in enumerate(candidates, start=1):
                result = self.attempt_single_hour_booking(candidate, "second", round_index, idx, len(candidates))
                if result == "success":
                    self.second_booking = candidate
                    return "success"
                if result in ("candidate_taken", "candidate_skipped"):
                    continue
                if result in ("retry_delay", "server_retry"):
                    break

            self._sleep_until_next_poll(deadline)

        return "failed"

    def run_direct_first_stage(self, guide_state=None):
        self.logger.info("-" * 110)
        self.logger.info("[直抢第一阶段] 跳过 get_places，按目标范围和场地池直接生成候选")
        deadline = time.monotonic() + self.args.window_seconds
        round_index = 0
        max_rounds = self._max_rounds(self.args.rounds)

        while time.monotonic() < deadline and round_index < max_rounds:
            round_index += 1
            candidates = self.generate_direct_first_candidates()
            if guide_state:
                candidates = guide_state.sort_candidates(candidates)
            self.logger.info("-" * 110)
            self.logger.info(f"[直抢第一阶段] 开始第 {round_index}/{max_rounds} 轮 | 候选总数={len(candidates)}")
            self.log_first_candidates(candidates)

            if self.args.dry_run:
                if candidates:
                    self.attempt_single_hour_booking(candidates[0], "direct_first", round_index, 1, len(candidates))
                else:
                    self.logger.info("[dry-run] 直抢模式未生成候选")
                return "dry_run"

            for idx, candidate in enumerate(candidates, start=1):
                result = self.attempt_single_hour_booking(candidate, "direct_first", round_index, idx, len(candidates))
                if guide_state:
                    guide_state.record_attempt_result(candidate, result)
                if result == "success":
                    self.first_booking = candidate
                    return "success"
                if result in ("candidate_taken", "candidate_skipped", "business_fail"):
                    continue
                if result in ("retry_delay", "server_retry"):
                    break

            self._sleep_until_next_poll(deadline)

        return "failed"

    def run_direct_second_stage(self, guide_state=None):
        if self.target_duration != 2:
            return "skipped"
        if not self.first_booking:
            return "failed"

        booked_hour = self.first_booking["hour"]
        target_hours = self.generate_second_target_hours(booked_hour)
        self.logger.info("-" * 110)
        self.logger.info(
            f"[直抢第二阶段] 第一单已成功：{booked_hour}:00-{booked_hour + 1}:00 "
            f"{self.first_booking['court_name']}({self.first_booking['court_id']})"
        )
        self.logger.info(f"[直抢第二阶段] 跳过 get_places，只抢相邻小时={target_hours}")

        if not target_hours:
            self.logger.warning("[直抢第二阶段] 第一单位于边界，范围内不存在相邻小时")
            return "failed"

        deadline = time.monotonic() + self.args.window_seconds
        round_index = 0
        max_rounds = self._max_rounds(self.args.second_rounds)

        while time.monotonic() < deadline and round_index < max_rounds:
            round_index += 1
            candidates = self.generate_direct_second_candidates(booked_hour)
            if guide_state:
                candidates = guide_state.sort_candidates(candidates)
            self.logger.info("-" * 110)
            self.logger.info(
                f"[直抢第二阶段] 开始第 {round_index}/{max_rounds} 轮 | "
                f"目标小时={target_hours} | 候选总数={len(candidates)}"
            )
            self.log_second_candidates(candidates, booked_hour)

            for idx, candidate in enumerate(candidates, start=1):
                result = self.attempt_single_hour_booking(candidate, "direct_second", round_index, idx, len(candidates))
                if guide_state:
                    guide_state.record_attempt_result(candidate, result)
                if result == "success":
                    self.second_booking = candidate
                    return "success"
                if result in ("candidate_taken", "candidate_skipped", "business_fail"):
                    continue
                if result in ("retry_delay", "server_retry"):
                    break

            self._sleep_until_next_poll(deadline)

        return "failed"

    def _direct_speculative_label(self, center_hour, candidate):
        if candidate["hour"] < center_hour:
            base_label = "direct_spec_left"
        elif candidate["hour"] > center_hour:
            base_label = "direct_spec_right"
        else:
            base_label = "direct_spec_center"
        return f"{base_label}_{candidate['hour']}_{candidate['court_id']}"

    def _direct_speculative_booking_worker(
        self,
        candidate,
        label,
        wave_index,
        attempt_index,
        candidate_index,
        candidate_total,
        results,
        result_lock,
        start_delay=0.0,
    ):
        delay_seconds = max(float(start_delay or 0), 0.0)
        if delay_seconds > 0:
            self.log_event(
                "candidate_start_delay",
                label=label,
                wave=wave_index,
                delay_ms=round(delay_seconds * 1000),
            )
            time.sleep(delay_seconds)

        started_at = time.monotonic()
        result = "exception"
        slot = None
        try:
            remaining = None if self.direct_deadline is None else max(self.direct_deadline - time.monotonic(), 0.001)
            slot = self.direct_client_queue.get(timeout=remaining)
            slot.fail_stats.clear()
            self.log_event(
                "candidate_client_acquired",
                label=label,
                wave=wave_index,
                attempt=attempt_index,
                client_slot=slot.slot_id,
                queue_wait_ms=round((time.monotonic() - started_at) * 1000),
            )
            result = self.attempt_single_hour_booking(
                candidate,
                label,
                wave_index,
                candidate_index,
                candidate_total,
                client=slot.client,
                failure_stats=slot.fail_stats,
            )
        except queue.Empty:
            result = "deadline_expired"
        except Exception as exc:
            if slot is not None:
                slot.fail_stats[f"{label}_exception"] += 1
            self.logger.error(f"[直抢投机] worker exception label={label} error={repr(exc)}")
            self.logger.error(traceback.format_exc())
        finally:
            elapsed = time.monotonic() - started_at
            with result_lock:
                if slot is not None:
                    self.fail_stats.update(slot.fail_stats)
                results.append(
                    {
                        "candidate": candidate,
                        "label": label,
                        "result": result,
                        "wave": wave_index,
                        "attempt": attempt_index,
                        "client_slot": slot.slot_id if slot is not None else 0,
                        "elapsed": elapsed,
                        "completed_at": time.monotonic(),
                    }
                )
            self.log_event(
                "candidate_attempt_complete",
                label=label,
                wave=wave_index,
                attempt=attempt_index,
                hour=candidate["hour"],
                court=candidate["court_id"],
                result=result,
                elapsed_ms=round(elapsed * 1000),
                client_slot=slot.slot_id if slot is not None else 0,
            )
            if slot is not None:
                self.direct_client_queue.put(slot)

    def _select_direct_speculative_pair(self, successful_candidates, center_hour):
        best = None
        for left_idx, left_candidate in enumerate(successful_candidates):
            for right_candidate in successful_candidates[left_idx + 1:]:
                if abs(left_candidate["hour"] - right_candidate["hour"]) != 1:
                    continue
                pair = sorted([left_candidate, right_candidate], key=lambda item: item["hour"])
                contains_center = any(item["hour"] == center_hour for item in pair)
                combined_rank = sum(self.court_rank.get(item["court_id"], 999) for item in pair)
                key = (0 if contains_center else 1, combined_rank, pair[0]["hour"])
                if best is None or key < best[0]:
                    best = (key, pair)
        return best[1] if best else None

    def _select_direct_speculative_anchor(self, successful_candidates, center_hour):
        return min(
            successful_candidates,
            key=lambda item: (
                0 if item["hour"] == center_hour else 1,
                -len(self.generate_second_target_hours(item["hour"])),
                self.court_rank.get(item["court_id"], 999),
                item["hour"],
            ),
        )

    def _log_direct_speculative_results(self, wave_index, results, successful_candidates):
        self.logger.info(
            f"[直抢波次] wave={wave_index} 完成 | total={len(results)} | success={len(successful_candidates)} | "
            f"failed={len(results) - len(successful_candidates)}"
        )
        for item in sorted(results, key=lambda result: result["completed_at"]):
            self.logger.info(
                f"[直抢波次结果] wave={wave_index} | label={item['label']} | result={item['result']} | "
                f"elapsed={item['elapsed']:.3f}s | {self._booking_candidate_text(item['candidate'])}"
            )

    @staticmethod
    def _candidate_key(candidate):
        return candidate["hour"], candidate["court_id"]

    def _take_direct_wave(self, pending, attempt_counts):
        eligible = []
        while pending:
            candidate = pending.popleft()
            skip_reason = self.reservation_place_gate.skip_reason(candidate)
            if skip_reason:
                self.log_event(
                    "candidate_scheduler_skip",
                    hour=candidate["hour"],
                    court=candidate["court_id"],
                    reason=skip_reason,
                )
                continue
            key = self._candidate_key(candidate)
            if attempt_counts[key] < self.args.direct_max_attempts:
                eligible.append(candidate)

        selected = []
        selected_keys = set()
        selected_hours = set()
        for candidate in eligible:
            if len(selected) >= self.args.direct_max_inflight:
                break
            key = self._candidate_key(candidate)
            if candidate["hour"] in selected_hours:
                continue
            selected.append(candidate)
            selected_keys.add(key)
            selected_hours.add(candidate["hour"])

        for candidate in eligible:
            if len(selected) >= self.args.direct_max_inflight:
                break
            key = self._candidate_key(candidate)
            if key in selected_keys:
                continue
            selected.append(candidate)
            selected_keys.add(key)

        for candidate in eligible:
            if self._candidate_key(candidate) not in selected_keys:
                pending.append(candidate)
        for candidate in selected:
            attempt_counts[self._candidate_key(candidate)] += 1
        return selected

    def _apply_direct_successes(self, successful_candidates, center_hour):
        if not successful_candidates:
            return False
        if self.target_duration == 1:
            self.first_booking = successful_candidates[0]
            return True

        pair = self._select_direct_speculative_pair(successful_candidates, center_hour)
        if pair:
            self.first_booking = pair[0]
            self.second_booking = pair[1]
            return True

        self.first_booking = self._select_direct_speculative_anchor(successful_candidates, center_hour)
        return False

    def run_direct_speculative_mode(self, guide_state=None):
        self.logger.info("-" * 110)
        self.logger.info(
            f"[直抢波次] direct-fast 启用：duration={self.target_duration} | "
            f"max_inflight={self.args.direct_max_inflight} | max_attempts={self.args.direct_max_attempts}"
        )
        candidates = self.generate_direct_first_candidates()
        if guide_state:
            candidates = guide_state.sort_candidates(candidates)
        self.logger.info(f"[直抢波次] 完整候选总数={len(candidates)}")
        self.log_first_candidates(candidates)
        if not candidates:
            self.logger.warning("[结束] 直抢波次未生成候选")
            return "failed"

        center_candidate = candidates[0]
        center_hour = center_candidate["hour"]

        if self.args.dry_run:
            self.attempt_single_hour_booking(center_candidate, "direct_spec_center", 1, 1, len(candidates))
            return "dry_run"

        self._prepare_direct_clients(prewarm=False)
        pending = deque(candidates)
        attempt_counts = Counter()
        candidate_total = len(candidates)
        observed_successes = []
        wave_index = 0
        final_status = "failed"
        previous_gate = self.reservation_place_gate
        previous_deadline = self.direct_deadline
        self.direct_deadline = time.monotonic() + self.args.window_seconds
        self.reservation_place_gate = ReservationPlaceGate(
            self.args.reservation_place_gap,
            self.args.reservation_place_fast_retry_gap,
            logger=self.logger,
            required_hours=self.target_duration,
        )
        self.log_event(
            "direct_scheduler_start",
            candidate_total=candidate_total,
            max_inflight=self.args.direct_max_inflight,
            max_attempts=self.args.direct_max_attempts,
            deadline_ms=round(self.args.window_seconds * 1000),
            center_hour=center_hour,
        )

        try:
            while pending and time.monotonic() < self.direct_deadline:
                wave_candidates = self._take_direct_wave(pending, attempt_counts)

                if not wave_candidates:
                    continue

                wave_index += 1
                results = []
                result_lock = threading.Lock()
                threads = []
                self.log_event(
                    "direct_wave_start",
                    wave=wave_index,
                    wave_size=len(wave_candidates),
                    pending=len(pending),
                    remaining_ms=max(round((self.direct_deadline - time.monotonic()) * 1000), 0),
                    candidates=[f"{item['hour']}:{item['court_id']}" for item in wave_candidates],
                )

                for idx, candidate in enumerate(wave_candidates, start=1):
                    attempt_index = attempt_counts[self._candidate_key(candidate)]
                    label = (
                        f"{self._direct_speculative_label(center_hour, candidate)}"
                        f"_w{wave_index}_a{attempt_index}"
                    )
                    start_delay = (
                        self.args.direct_spec_adjacent_delay
                        if candidate["hour"] != center_hour
                        else 0.0
                    )
                    thread = threading.Thread(
                        target=self._direct_speculative_booking_worker,
                        args=(
                            candidate,
                            label,
                            wave_index,
                            attempt_index,
                            idx,
                            candidate_total,
                            results,
                            result_lock,
                            start_delay,
                        ),
                    )
                    thread.start()
                    threads.append(thread)

                for thread in threads:
                    thread.join()

                wave_successes = [item["candidate"] for item in results if item["result"] == "success"]
                self._log_direct_speculative_results(wave_index, results, wave_successes)
                for candidate in wave_successes:
                    if self._candidate_key(candidate) not in {
                        self._candidate_key(item) for item in observed_successes
                    }:
                        observed_successes.append(candidate)
                successful_candidates = self.reservation_place_gate.successful_candidates() or observed_successes
                if self._apply_direct_successes(successful_candidates, center_hour):
                    final_status = "success"
                    break

                transient_candidates = []
                for item in results:
                    if item["result"] in ("retry_delay", "server_retry", "exception"):
                        key = self._candidate_key(item["candidate"])
                        if attempt_counts[key] < self.args.direct_max_attempts:
                            transient_candidates.append(item["candidate"])
                    if guide_state:
                        guide_state.record_attempt_result(item["candidate"], item["result"])
                pending.extend(transient_candidates)

                self.log_event(
                    "direct_wave_complete",
                    wave=wave_index,
                    success_count=len(successful_candidates),
                    transient_requeued=len(transient_candidates),
                    pending=len(pending),
                    results=Counter(item["result"] for item in results),
                )
                if pending and time.monotonic() < self.direct_deadline:
                    self._sleep_for(self.args.poll_interval, self.direct_deadline)

            successful_candidates = self.reservation_place_gate.successful_candidates() or observed_successes
            self._apply_direct_successes(successful_candidates, center_hour)
            reason = "target_complete" if final_status == "success" else "window_expired" if time.monotonic() >= self.direct_deadline else "candidate_exhausted"
            self.log_event(
                "direct_scheduler_complete",
                status=final_status,
                reason=reason,
                waves=wave_index,
                attempted_candidates=len(attempt_counts),
                total_attempts=sum(attempt_counts.values()),
                success_hours=sorted({item["hour"] for item in successful_candidates}),
            )
            if final_status == "success":
                self.logger.info("[完成] 直抢波次已达成目标")
            elif self.first_booking:
                self.logger.warning("[结束] 直抢波次已抢到一个小时，但未完成连续两小时")
            else:
                self.logger.warning(f"[结束] 直抢波次未抢到任何一个小时 | reason={reason}")
            return final_status
        finally:
            self.reservation_place_gate = previous_gate
            self.direct_deadline = previous_deadline

    def run_direct_mode(self, guide_state=None):
        if self.args.booking_mode in (BOOKING_MODE_DIRECT_FAST, BOOKING_MODE_GUIDED_FAST):
            return self.run_direct_speculative_mode(guide_state)

        first_status = self.run_direct_first_stage(guide_state)
        if first_status == "dry_run":
            return "dry_run"
        if first_status != "success":
            self.logger.warning("[结束] 直抢第一阶段未抢到任何一个小时")
            return "failed"
        if self.target_duration == 1:
            self.logger.info("[完成] 直抢单小时目标已达成")
            return "success"

        second_status = self.run_direct_second_stage(guide_state)
        if second_status != "success":
            self.logger.warning("[结束] 直抢第一阶段成功，但第二阶段未抢到相邻小时")
            return "failed"
        self.logger.info("[完成] 直抢两阶段均成功")
        return "success"

    def run_guided_mode(self):
        guide_state = GuidedBookingState(self.court_rank)
        guide_deadline = time.monotonic() + self.args.window_seconds * (2 if self.target_duration == 2 else 1)
        stop_event, collector_thread = self.start_guided_collector(guide_state, guide_deadline)
        try:
            return self.run_direct_mode(guide_state)
        finally:
            stop_event.set()
            collector_thread.join(timeout=1.0)

    def start_guided_collector(self, guide_state, deadline):
        stop_event = threading.Event()
        thread = threading.Thread(
            target=self._guided_collector_loop,
            args=(guide_state, deadline, stop_event),
            daemon=True,
        )
        thread.start()
        return stop_event, thread

    def _guided_collector_loop(self, guide_state, deadline, stop_event):
        inflight = set()
        probe_index = 0
        next_tick = time.monotonic()
        self.logger.info(
            f"[引导采集] 启动 | interval={self.args.guide_interval}s | "
            f"max_inflight={self.args.guide_max_inflight}"
        )
        while not stop_event.is_set() and time.monotonic() < deadline:
            inflight = {thread for thread in inflight if thread.is_alive()}
            if len(inflight) < self.args.guide_max_inflight:
                probe_index += 1
                thread = threading.Thread(
                    target=self._guided_probe_worker,
                    args=(guide_state, probe_index),
                    daemon=True,
                )
                thread.start()
                inflight.add(thread)
                self.logger.info(f"[引导采集] tick={probe_index} 已启动探测 | inflight={len(inflight)}")
            else:
                self.fail_stats["guided_probe_skipped_inflight"] += 1
                self.logger.warning(
                    f"[引导采集] inflight={len(inflight)} 达到上限，跳过本次探测"
                )
            next_tick += self.args.guide_interval
            self._sleep_until_guided_tick(next_tick, deadline, stop_event)
        alive = sum(1 for thread in inflight if thread.is_alive())
        self.logger.info(f"[引导采集] 停止 | alive_probes={alive}")

    def _guided_probe_worker(self, guide_state, probe_index):
        local_stats = Counter()
        client = KeepAliveClient(
            self.base_url,
            self._headers(),
            timeout=self.args.timeout,
            logger=self.logger,
            fail_stats=local_stats,
        )
        try:
            result = client.request(
                "GET",
                "/datediscount/getPlaceInfoByShortNameDiscount",
                params={
                    "shopNum": SHOP_NUM,
                    "dateymd": self.target_date,
                    "shortName": SHORT_NAME,
                    "token": self.args.token,
                },
                label=f"guided_get_places_{probe_index}",
            )
            places = self._places_from_guided_result(result, probe_index)
            if not places:
                return
            hour_table = self.build_hour_slot_table(places)
            guide_state.update_snapshot(hour_table, range(self.range_start_h, self.range_end_h), self.court_pool)
            self.logger.info(f"[引导采集] tick={probe_index} 快照已更新")
        finally:
            client.close()
            self.fail_stats.update(local_stats)

    def _places_from_guided_result(self, result, probe_index):
        if result.status >= 500 or result.status == 0:
            self.fail_stats["guided_get_places_server_error"] += 1
            self.logger.warning(f"[引导采集] tick={probe_index} 服务错误 status={result.status}")
            return None
        if result.json_error or not result.json_data:
            self.fail_stats["guided_get_places_json_error"] += 1
            self.logger.warning(f"[引导采集] tick={probe_index} 返回不是有效 JSON")
            return None
        if result.json_data.get("msg") != "success":
            message = safe_response_message(result.json_data)
            key = "busy" if BUSY_RETRY_TEXT in message else "business"
            self.fail_stats[f"guided_get_places_{key}"] += 1
            self.logger.warning(f"[引导采集] tick={probe_index} msg={result.json_data.get('msg')} data={message}")
            return None
        places = result.json_data.get("data", {}).get("placeArray", [])
        if not isinstance(places, list):
            self.fail_stats["guided_get_places_invalid_places"] += 1
            self.logger.warning(f"[引导采集] tick={probe_index} placeArray 不是列表")
            return None
        return places

    def _max_rounds(self, configured_rounds):
        window_rounds = math.ceil(self.args.window_seconds / self.args.poll_interval)
        return max(configured_rounds, window_rounds)

    def _sleep_after_get_places_failure(self, server_backoff, deadline):
        if self.last_get_places_error == "busy":
            self._sleep_for(min(self.args.poll_interval, 0.1), deadline)
            return self.args.error_backoff
        if self.last_get_places_error in ("server", "json"):
            self._sleep_for(server_backoff, deadline)
            return min(server_backoff * 2, 2.0)
        self._sleep_until_next_poll(deadline)
        return self.args.error_backoff

    def _sleep_until_next_poll(self, deadline):
        self._sleep_for(self.args.poll_interval, deadline)

    @staticmethod
    def _sleep_for(seconds, deadline):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return
        time.sleep(min(seconds, remaining))

    @staticmethod
    def _sleep_until_guided_tick(next_tick, deadline, stop_event):
        while not stop_event.is_set():
            remaining = min(next_tick, deadline) - time.monotonic()
            if remaining <= 0:
                return
            stop_event.wait(min(remaining, 0.05))

    def print_summary(self):
        self.logger.info("=" * 110)
        self.logger.info("[汇总] 本次预约任务结束")
        self.logger.info(f"[汇总] 日志文件：{self.log_path}")
        if self.args.dry_run:
            if self.dry_run_candidate:
                item = self.dry_run_candidate
                self.logger.info(
                    f"[汇总] dry-run 候选：{item['hour']}:00-{item['hour'] + 1}:00 "
                    f"{item['court_name']}({item['court_id']})"
                )
            else:
                self.logger.info("[汇总] dry-run 未发现候选")
            self.logger.info(f"[汇总] 失败统计：{dict(self.fail_stats)}")
            self._log_http_metrics_summary()
            self.logger.info("=" * 110)
            return

        if self.first_booking:
            self.logger.info(
                f"[汇总] 第一单成功：{self.first_booking['hour']}:00-{self.first_booking['hour'] + 1}:00 "
                f"{self.first_booking['court_name']}({self.first_booking['court_id']})"
            )
        else:
            self.logger.info("[汇总] 第一单未成功")

        if self.target_duration == 2:
            if self.second_booking:
                self.logger.info(
                    f"[汇总] 第二单成功：{self.second_booking['hour']}:00-{self.second_booking['hour'] + 1}:00 "
                    f"{self.second_booking['court_name']}({self.second_booking['court_id']})"
                )
            else:
                self.logger.info("[汇总] 第二单未成功")

            if self.first_booking and self.second_booking:
                hours = sorted([self.first_booking["hour"], self.second_booking["hour"]])
                if len(hours) == 2 and hours[1] - hours[0] == 1:
                    self.logger.info(f"[汇总] 最终实现连续两小时：{hours[0]}:00-{hours[1] + 1}:00")
                else:
                    self.logger.info("[汇总] 两单成功，但未构成连续两小时")
            else:
                self.logger.info("[汇总] 未完成连续两小时目标")
        elif self.first_booking:
            self.logger.info("[汇总] 已完成单小时预约目标")

        self.logger.info(f"[汇总] 失败统计：{dict(self.fail_stats)}")
        self._log_http_metrics_summary()
        self.logger.info("=" * 110)

    def _log_http_metrics_summary(self):
        metrics_summary = {}
        with self.metrics_lock:
            metrics_snapshot = {key: list(values) for key, values in self.http_metrics.items()}
            outcome_snapshot = dict(self.outcome_stats)
        for endpoint, values in sorted(metrics_snapshot.items()):
            ordered = sorted(values)
            if not ordered:
                continue
            p50 = ordered[min(math.ceil(len(ordered) * 0.50) - 1, len(ordered) - 1)]
            p90 = ordered[min(math.ceil(len(ordered) * 0.90) - 1, len(ordered) - 1)]
            summary = {
                "count": len(ordered),
                "p50_ms": round(p50 * 1000),
                "p90_ms": round(p90 * 1000),
                "max_ms": round(max(ordered) * 1000),
            }
            metrics_summary[endpoint] = summary
            self.logger.info(
                f"[汇总] HTTP {endpoint}: count={summary['count']} p50={summary['p50_ms']}ms "
                f"p90={summary['p90_ms']}ms max={summary['max_ms']}ms"
            )
        self.log_event(
            "run_summary",
            http_metrics=metrics_summary,
            outcomes=outcome_snapshot,
            failures=dict(self.fail_stats),
            completed_hours=int(bool(self.first_booking)) + int(bool(self.second_booking)),
        )

    def run(self):
        try:
            if self.args.check_session:
                self.prewarm()
                self.check_session()
                self.print_summary()
                return

            self.wait_for_start()
            self.logger.info(
                f"[开始] 启动预约流程：目标范围 {self.range_start_h}:00-{self.range_end_h}:00 | "
                f"目标时长={self.target_duration}小时 | mode={self.args.booking_mode}"
            )

            if self.args.booking_mode == BOOKING_MODE_DIRECT_FAST:
                self.run_direct_mode()
                self.print_summary()
                return
            if self.args.booking_mode == BOOKING_MODE_GUIDED_FAST:
                self.run_guided_mode()
                self.print_summary()
                return

            first_status = self.run_first_stage()
            if first_status == "dry_run":
                self.print_summary()
                return
            if first_status != "success":
                self.logger.warning("[结束] 第一阶段未抢到任何一个小时")
                self.print_summary()
                return
            if self.target_duration == 1:
                self.logger.info("[完成] 单小时目标已达成")
                self.print_summary()
                return

            second_status = self.run_second_stage()
            if second_status != "success":
                self.logger.warning("[结束] 第一阶段成功，但第二阶段未抢到相邻小时")
                self.print_summary()
                return

            self.logger.info("[完成] 两阶段均成功")
            self.print_summary()
        finally:
            self._close_direct_clients()
            self.client.close()


def build_parser():
    parser = argparse.ArgumentParser(
        description=f"羽毛球场地预约 - engine {BOOKING_ENGINE_VERSION}",
        epilog="""
Examples:
  python enhanced_book_smart_v2.py -t 17-21 --duration 2
  python enhanced_book_smart_v2.py --dry-run --force -d 2026-05-15 -t 22-23 --duration 1
        """,
    )
    parser.add_argument("-k", "--token", default=DEFAULT_TOKEN, help="token")
    parser.add_argument("-j", "--jsessionid", default=DEFAULT_JSESSIONID, help="JSESSIONID")
    parser.add_argument("-i", "--card-index", default=DEFAULT_CARD_INDEX, help="card index / offer id")

    date_group = parser.add_mutually_exclusive_group()
    date_group.add_argument("-d", "--date", help="指定日期 YYYY-MM-DD")
    date_group.add_argument("--in-days", type=int, help="N天后")

    parser.add_argument("-t", "--time", required=True, help="时间范围，如 17-18, 17-21, 22-23")
    parser.add_argument("--duration", type=int, default=2, help="目标预约时长，只能为 1 或 2，默认2")
    parser.add_argument("-p", "--priority", nargs="+", type=int, default=[7, 8, 9, 1, 6], help="priority 场地编号")
    parser.add_argument("--backup", nargs="+", type=int, default=[2, 3, 4, 5, 10, 11, 12], help="backup 场地编号")
    parser.add_argument(
        "--all-court",
        "--all_court",
        dest="all_court",
        action="store_true",
        help="包含默认排除的靠墙场地 4/5/12",
    )
    parser.add_argument("--force", action="store_true", help="立即执行，不等待12:00")
    parser.add_argument("--rounds", type=int, default=100, help="第一阶段轮数下限，默认100")
    parser.add_argument("--second-rounds", type=int, default=100, help="第二阶段轮数下限，默认100")
    parser.add_argument("--step-sleep", type=float, default=0.03, help="下单链路请求间隔，默认0.03秒")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL, help="easyserpClient base URL")
    parser.add_argument("--window-seconds", type=float, default=60.0, help="每个阶段的运行窗口，默认60秒")
    parser.add_argument("--poll-interval", type=float, default=0.08, help="普通轮询间隔，默认0.08秒")
    parser.add_argument(
        "--direct-spec-adjacent-delay",
        type=float,
        default=DEFAULT_DIRECT_SPEC_ADJACENT_DELAY,
        help="direct-fast 相邻小时候选启动延迟，默认0秒",
    )
    parser.add_argument(
        "--direct-max-inflight",
        type=int,
        default=DEFAULT_DIRECT_MAX_INFLIGHT,
        help="direct-fast 每波最大并发候选数，默认3",
    )
    parser.add_argument(
        "--direct-max-attempts",
        type=int,
        default=DEFAULT_DIRECT_MAX_ATTEMPTS,
        help="direct-fast 每个候选的最大尝试次数，默认2",
    )
    parser.add_argument(
        "--reservation-place-gap",
        type=float,
        default=DEFAULT_RESERVATION_PLACE_GAP,
        help="direct-fast reservationPlace 响应后的最小间隔，默认0.35秒",
    )
    parser.add_argument(
        "--reservation-place-fast-retry-gap",
        type=float,
        default=DEFAULT_RESERVATION_PLACE_FAST_RETRY_GAP,
        help="direct-fast 操作过快后的 reservationPlace 重试间隔，默认0.8秒",
    )
    parser.add_argument(
        "--booking-mode",
        choices=BOOKING_MODES,
        default=BOOKING_MODE_BALANCED,
        help="预约策略：balanced 查询后下单，direct-fast 跳过查询直抢，guided-fast 多线程引导直抢",
    )
    parser.add_argument("--guide-interval", type=float, default=0.5, help="guided-fast 探测调度间隔，默认0.5秒")
    parser.add_argument("--guide-max-inflight", type=int, default=4, help="guided-fast 最大未完成探测数，默认4")
    parser.add_argument("--error-backoff", type=float, default=0.25, help="服务错误初始退避，默认0.25秒")
    parser.add_argument("--dry-run", action="store_true", help="只查询和生成候选，不调用下单接口")
    parser.add_argument("--check-session", action="store_true", help="只检测 JSESSIONID 是否可用，不等待也不下单")
    parser.add_argument("--timeout", type=float, default=5.0, help="HTTP 超时时间，默认5秒")
    return parser


def main():
    parser = build_parser()
    args = parser.parse_args()
    try:
        bot = SmartBookingBotV2(args)
        bot.run()
    except ValueError as exc:
        print(f"[参数错误] {exc}")
        sys.exit(1)
    except KeyboardInterrupt:
        print("\n[中断] 用户手动终止")
        sys.exit(130)
    except Exception as exc:
        print(f"[严重错误] {exc}")
        print(traceback.format_exc())
        sys.exit(1)


if __name__ == "__main__":
    main()
