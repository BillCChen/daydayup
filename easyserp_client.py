#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Shared EasySERP API helpers for booking maintenance scripts.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

import requests

from network_alias import DEFAULT_HOST_ALIASES, install_host_aliases_with_defaults


install_host_aliases_with_defaults(default_aliases=DEFAULT_HOST_ALIASES)


DEFAULT_BASE_URL = "https://www.147soft.cn/easyserpClient"
DEFAULT_TOKEN = os.getenv("DAYDAYUP_TOKEN", "")
DEFAULT_JSESSIONID = os.getenv("DAYDAYUP_JSESSIONID", "")
DEFAULT_CARD_INDEX = os.getenv("DAYDAYUP_CARD_INDEX", "")
DEFAULT_SHOP_NUM = "1001"
DEFAULT_SHORT_NAME = "ymq"
USER_AGENT = (
    "Mozilla/5.0 (Linux; Android 16; V2366HA Build/BP2A.250605.031.A3; wv) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 Chrome/146.0.7680.177 "
    "Mobile Safari/537.36 XWEB/1460075 MMWEBSDK/20260202 MMWEBID/5120 "
    "REV/89918ef4d19865ac6236e9f77c99567b0ec6d85b "
    "MicroMessenger/8.0.70.3060(0x28004652) WeChat/arm64 Weixin "
    "NetType/WIFI Language/zh_CN ABI/arm64"
)


class EasySerpError(RuntimeError):
    pass


def redact_sensitive_text(text: Any) -> str:
    value = str(text)
    sensitive_keys = (
        r"token|jsessionid|cardindex|offerid|mastercardnum|"
        r"username|userName|password|passWord|admin_password"
    )
    value = re.sub(
        rf"(?i)({sensitive_keys})(=|%3[dD])([^&\s'\"<>]+)",
        lambda match: f"{match.group(1)}{match.group(2)}<redacted>",
        value,
    )
    value = re.sub(
        rf'(?i)([\"\'](?:{sensitive_keys})[\"\']\s*:\s*[\"\'])[^\"\']*',
        lambda match: f"{match.group(1)}<redacted>",
        value,
    )
    return value


@dataclass(frozen=True)
class OrderSummary:
    bill_num: str
    status: str
    date: str
    time_range: str
    court: str
    amount: str
    pay_type: str
    created_at: str
    short_name: str


class EasySerpClient:
    def __init__(self, base_url: str, token: str, jsessionid: str = "", timeout: float = 10.0):
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.jsessionid = jsessionid
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update(self._headers())

    def get(self, endpoint: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        return self._request("GET", endpoint, params=params)

    def post(self, endpoint: str, data: dict[str, Any] | None = None) -> dict[str, Any]:
        return self._request("POST", endpoint, data=data)

    def _request(
        self,
        method: str,
        endpoint: str,
        *,
        params: dict[str, Any] | None = None,
        data: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        url = f"{self.base_url}/{endpoint.lstrip('/')}"
        try:
            response = self.session.request(
                method,
                url,
                params=params,
                data=data,
                timeout=self.timeout,
            )
        except requests.RequestException as exc:
            raise EasySerpError(f"request failed: {redact_sensitive_text(exc)}") from exc

        if response.status_code >= 500:
            raise EasySerpError(f"server error: HTTP {response.status_code}")
        if response.status_code >= 400:
            raise EasySerpError(f"request error: HTTP {response.status_code}")

        try:
            payload = response.json()
        except ValueError as exc:
            raise EasySerpError(f"non-json response: {redact_sensitive_text(response.text[:120])}") from exc

        if not isinstance(payload, dict):
            raise EasySerpError("unexpected response shape")
        return payload

    def _headers(self) -> dict[str, str]:
        parsed = urlsplit(self.base_url)
        origin = f"{parsed.scheme}://{parsed.netloc}"
        headers = {
            "User-Agent": USER_AGENT,
            "Accept": "application/json, text/plain, */*",
            "Content-Type": "application/x-www-form-urlencoded",
            "Origin": origin,
            "X-Requested-With": "com.tencent.mm",
            "Referer": f"{origin}/easyserp/index.html?token={self.token}",
            "Accept-Language": "zh-CN,zh;q=0.9,en-US;q=0.8,en;q=0.7",
        }
        if self.jsessionid:
            headers["Cookie"] = f"JSESSIONID={self.jsessionid}"
        return headers


def add_common_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("-k", "--token", default=DEFAULT_TOKEN, help="wechat token")
    parser.add_argument("-j", "--jsessionid", default=DEFAULT_JSESSIONID, help="JSESSIONID")
    parser.add_argument("--shop-num", default=DEFAULT_SHOP_NUM, help="shop number")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL, help="EasySERP API base URL")
    parser.add_argument("--timeout", type=float, default=10.0, help="request timeout in seconds")


def build_client(args: argparse.Namespace) -> EasySerpClient:
    if not args.token:
        raise EasySerpError("missing token; pass -k or set DAYDAYUP_TOKEN")
    return EasySerpClient(args.base_url, args.token, args.jsessionid, args.timeout)


def require_success(payload: dict[str, Any], action: str) -> Any:
    if payload.get("msg") != "success":
        raise EasySerpError(f"{action} failed: {payload.get('data') or payload.get('msg')}")
    return payload.get("data")


def fetch_orders(
    client: EasySerpClient,
    *,
    token: str,
    shop_num: str,
    page_size: int,
    max_pages: int,
    start_time: str = "",
    end_time: str = "",
) -> list[dict[str, Any]]:
    orders: list[dict[str, Any]] = []
    for page_no in range(max_pages):
        params: dict[str, Any] = {
            "pageNo": page_no,
            "pageSize": page_size,
            "shopNum": shop_num,
            "token": token,
        }
        if start_time:
            params["startTime"] = start_time
        if end_time:
            params["endTime"] = end_time

        data = require_success(client.get("place/getPlaceOrder", params=params), "getPlaceOrder")
        if not isinstance(data, list):
            raise EasySerpError("getPlaceOrder returned a non-list data field")
        orders.extend(data)
        if len(data) < page_size:
            break
        time.sleep(0.08)
    return orders


def summarize_order(order: dict[str, Any]) -> OrderSummary:
    slots = order.get("jsonArray") or []
    slot = slots[0] if slots and isinstance(slots[0], dict) else {}
    start = trim_time(order.get("readystarttime") or slot.get("start") or "")
    end = trim_time(order.get("readyendtime") or slot.get("end") or "")
    time_range = f"{start}-{end}" if start or end else ""
    court = (
        order.get("stagenum")
        or slot.get("siteName")
        or order.get("itemorgoodname")
        or order.get("itemorgoodshortname")
        or ""
    )
    return OrderSummary(
        bill_num=str(order.get("billNum") or ""),
        status=str(order.get("prestatus") or ""),
        date=str(order.get("readydate") or slot.get("reversionDate") or ""),
        time_range=time_range,
        court=str(court),
        amount=format_amount(order.get("readycashnum")),
        pay_type=str(order.get("payType") or ""),
        created_at=str(order.get("preTime") or ""),
        short_name=str(order.get("itemorgoodshortname") or order.get("shortName") or ""),
    )


def is_cancelled(order: dict[str, Any]) -> bool:
    return "取消" in str(order.get("prestatus") or "")


def find_order(orders: list[dict[str, Any]], bill_num: str) -> dict[str, Any] | None:
    for order in orders:
        if str(order.get("billNum") or "") == bill_num:
            return order
    return None


def mask_card(value: Any) -> str:
    text = str(value or "")
    if len(text) <= 6:
        return text
    return f"{text[:3]}...{text[-3:]}"


def trim_time(value: Any) -> str:
    text = str(value or "")
    if len(text) >= 5 and text[2] == ":":
        return text[:5]
    return text


def format_amount(value: Any) -> str:
    if value in (None, ""):
        return ""
    try:
        return f"{float(value):.2f}"
    except (TypeError, ValueError):
        return str(value)


def print_error(exc: Exception) -> int:
    print(f"[错误] {exc}", file=sys.stderr)
    return 1
