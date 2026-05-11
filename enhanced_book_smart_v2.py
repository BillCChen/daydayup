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
import ssl
import sys
import time
import traceback
import urllib.parse
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta
from logging.handlers import RotatingFileHandler


DEFAULT_BASE_URL = "https://www.147soft.cn/easyserpClient"
DEFAULT_TOKEN = os.getenv("DAYDAYUP_TOKEN", "")
DEFAULT_JSESSIONID = os.getenv("DAYDAYUP_JSESSIONID", "")
DEFAULT_CARD_INDEX = os.getenv("DAYDAYUP_CARD_INDEX", "")
SHOP_NUM = "1001"
SHORT_NAME = "ymq"
PROJECT_TYPE = "3"
PREWARM_SECONDS = 3.0
BUSY_RETRY_TEXT = "当前排队人数较多"
FAST_RETRY_TEXT = "操作过快"
TAKEN_RETRY_TEXT = "下手太晚"
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


class KeepAliveClient:
    def __init__(self, base_url, headers, timeout, logger, fail_stats):
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
                    f"[HTTP] label={label} method={method} exception={repr(exc)} elapsed={elapsed:.3f}s"
                )
                self.logger.error(traceback.format_exc())
                return HttpResult(status=0, text="", elapsed=elapsed)
        except Exception as exc:
            elapsed = time.perf_counter() - start
            self.fail_stats[f"{label}_exception"] += 1
            self.logger.error(
                f"[HTTP] label={label} method={method} exception={repr(exc)} elapsed={elapsed:.3f}s"
            )
            self.logger.error(traceback.format_exc())
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
        preview = redact_text(text[:800].replace("\n", "\\n").replace("\r", ""))

        self.logger.info(
            f"[HTTP] label={label} method={method} status={resp.status} elapsed={elapsed:.3f}s"
        )
        self.logger.info(f"[HTTP] label={label} response_preview={preview}")

        try:
            json_data = json.loads(text)
            return HttpResult(resp.status, text, elapsed, json_data=json_data)
        except Exception:
            self.fail_stats[f"{label}_json_decode_error"] += 1
            self.logger.error(f"[HTTP] label={label} JSON解析失败")
            return HttpResult(resp.status, text, elapsed, json_error=True)

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
    replacements = [
        ("token=", "&"),
        ("cardIndex=", "&"),
        ("offerId=", "&"),
        ("masterCardNum=", "&"),
    ]
    redacted = text
    for prefix, terminator in replacements:
        start = 0
        while True:
            idx = redacted.find(prefix, start)
            if idx < 0:
                break
            value_start = idx + len(prefix)
            value_end = redacted.find(terminator, value_start)
            if value_end < 0:
                value_end = len(redacted)
            redacted = redacted[:value_start] + "<redacted>" + redacted[value_end:]
            start = value_start + len("<redacted>")

    for key in ("cardindex", "cardIndex", "masterCardNum"):
        redacted = redact_json_string_key(redacted, key)
    return redacted


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
        self.base_url = args.base_url.rstrip("/")
        self.origin = self._origin_from_base_url(self.base_url)
        self.fail_stats = Counter()
        self.first_booking = None
        self.second_booking = None
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

        excluded_courts = set() if args.all_court else {"ymq1", "ymq5", "ymq12"}
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
        )

        self._log_config()
        self._warn_about_credentials()

    def _setup_logger(self):
        os.makedirs("logs", exist_ok=True)
        log_name = (
            f"booking_smart_v2_{self.target_date}_{self.range_start_h}-{self.range_end_h}"
            f"_d{self.target_duration}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
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

        self.logger.handlers.clear()
        self.logger.addHandler(console_handler)
        self.logger.addHandler(file_handler)

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
        self.logger.info("羽毛球场地预约脚本启动（v2 均衡模式）")
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
        self.logger.info(f"[配置] 场地池={self.court_pool}")
        if self.excluded_courts:
            self.logger.info(f"[配置] 默认排除靠墙场地={self.excluded_courts}；使用 --all-court 可包含")
        self.logger.info(
            f"[凭证] token={fingerprint(self.args.token)} | JSESSIONID={fingerprint(self.args.jsessionid)} | "
            f"card_index={fingerprint(self.args.card_index)}"
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
        data = str(result.json_data.get("data", ""))
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
            message = str(result.json_data.get("data", ""))
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
                        }
                    )

        candidates.sort(
            key=lambda item: (
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

    def attempt_single_hour_booking(self, candidate, label, round_index, candidate_index, candidate_total):
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

        r0 = self.client.request(
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
            return self._classify_booking_failure(r0, f"{label}_canBook")

        self._sleep_between_booking_calls()
        r1 = self.client.request(
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
            return self._classify_booking_failure(r1, f"{label}_getOfferInfo")

        self._sleep_between_booking_calls()
        r2 = self.client.request(
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
            return self._classify_booking_failure(r2, f"{label}_getUseCardInfo")

        self._sleep_between_booking_calls()
        r3 = self.client.request(
            "POST",
            "/place/reservationPlace",
            data={
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
            },
            label=f"{label}_reservationPlace",
        )
        if self._response_success(r3):
            self.logger.info(
                f"[成功] label={label} 预约成功 | 日期={self.target_date} | "
                f"时段={start_time}-{end_time} | 场地={court_name}({court_id})"
            )
            return "success"
        return self._classify_booking_failure(r3, f"{label}_reservationPlace")

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

    def _classify_booking_failure(self, result, label):
        self.fail_stats[f"{label}_not_success"] += 1
        data = ""
        if result and result.json_data:
            data = str(result.json_data.get("data", ""))
        self.logger.warning(f"[失败] label={label} data={data or 'empty'}")
        if TAKEN_RETRY_TEXT in data:
            return "candidate_taken"
        if FAST_RETRY_TEXT in data:
            time.sleep(0.8)
            return "retry_delay"
        if result and result.status >= 500:
            return "server_retry"
        return "business_fail"

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
                if result == "candidate_taken":
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
                if result == "candidate_taken":
                    continue
                if result in ("retry_delay", "server_retry"):
                    break

            self._sleep_until_next_poll(deadline)

        return "failed"

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
        self.logger.info("=" * 110)

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
                f"目标时长={self.target_duration}小时"
            )

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
            self.client.close()


def build_parser():
    parser = argparse.ArgumentParser(
        description="羽毛球场地预约 - v2 均衡模式",
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
        help="包含默认排除的靠墙场地 1/5/12",
    )
    parser.add_argument("--force", action="store_true", help="立即执行，不等待12:00")
    parser.add_argument("--rounds", type=int, default=100, help="第一阶段轮数下限，默认100")
    parser.add_argument("--second-rounds", type=int, default=100, help="第二阶段轮数下限，默认100")
    parser.add_argument("--step-sleep", type=float, default=0.03, help="下单链路请求间隔，默认0.03秒")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL, help="easyserpClient base URL")
    parser.add_argument("--window-seconds", type=float, default=60.0, help="每个阶段的运行窗口，默认60秒")
    parser.add_argument("--poll-interval", type=float, default=0.08, help="普通轮询间隔，默认0.08秒")
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
