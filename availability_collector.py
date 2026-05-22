#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import sys
import time
from dataclasses import dataclass
from datetime import date, datetime, time as datetime_time, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from easyserp_client import (
    DEFAULT_BASE_URL,
    DEFAULT_JSESSIONID,
    DEFAULT_SHORT_NAME,
    DEFAULT_SHOP_NUM,
    DEFAULT_TOKEN,
    EasySerpClient,
    EasySerpError,
    redact_sensitive_text,
    require_success,
    trim_time,
)


ROOT = Path(__file__).resolve().parent
DEFAULT_DB_PATH = ROOT / "local" / "availability.sqlite3"
COLLECTOR_TZ = ZoneInfo("Asia/Shanghai")
SCHEMA_VERSION = "1"
QUERY_NODE_MINUTES = (15, 45)
DEFAULT_DAYS_AHEAD = 4
COURT_NUMBERS = tuple(range(1, 13))
FIRST_HOUR = 8
LAST_HOUR = 22
SQLITE_BUSY_TIMEOUT_MS = 5000


@dataclass(frozen=True)
class CollectorConfig:
    db_path: Path
    token: str
    jsessionid: str
    shop_num: str
    base_url: str
    timeout: float
    short_name: str = DEFAULT_SHORT_NAME


@dataclass(frozen=True)
class CollectionSummary:
    planned_node_at: str
    status: str
    success_count: int
    failure_count: int
    observation_count: int
    target_dates: list[str]
    error_summary: str


def now_in_zone() -> datetime:
    return datetime.now(COLLECTOR_TZ)


def format_timestamp(value: datetime) -> str:
    return value.astimezone(COLLECTOR_TZ).isoformat(timespec="seconds")


def next_query_node(now: datetime) -> datetime:
    current = now.astimezone(COLLECTOR_TZ).replace(microsecond=0)
    for day_offset in range(2):
        candidate_day = current.date() + timedelta(days=day_offset)
        for hour in range(24):
            for minute in QUERY_NODE_MINUTES:
                candidate = datetime.combine(candidate_day, datetime_time(hour, minute), COLLECTOR_TZ)
                if candidate >= current:
                    return candidate
    return datetime.combine(current.date() + timedelta(days=1), datetime_time(0, 15), COLLECTOR_TZ)


def current_query_node(now: datetime) -> datetime:
    current = now.astimezone(COLLECTOR_TZ).replace(microsecond=0)
    for day_offset in range(2):
        candidate_day = current.date() - timedelta(days=day_offset)
        for hour in reversed(range(24)):
            for minute in reversed(QUERY_NODE_MINUTES):
                candidate = datetime.combine(candidate_day, datetime_time(hour, minute), COLLECTOR_TZ)
                if candidate <= current:
                    return candidate
    return datetime.combine(current.date() - timedelta(days=1), datetime_time(23, 45), COLLECTOR_TZ)


def target_date_values(today: date, days_ahead: int = DEFAULT_DAYS_AHEAD) -> list[str]:
    return [(today + timedelta(days=offset)).isoformat() for offset in range(days_ahead + 1)]


def hour_ranges() -> list[tuple[str, str]]:
    return [(f"{hour:02d}:00", f"{hour + 1:02d}:00") for hour in range(FIRST_HOUR, LAST_HOUR + 1)]


def connect_database(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA busy_timeout = 5000")
    connection.execute("PRAGMA journal_mode = WAL")
    connection.execute("PRAGMA foreign_keys = ON")
    initialize_schema(connection)
    return connection


def initialize_schema(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS schema_meta (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS collector_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            planned_node_at TEXT NOT NULL UNIQUE,
            started_at TEXT NOT NULL,
            finished_at TEXT,
            status TEXT NOT NULL,
            target_start_date TEXT NOT NULL,
            target_end_date TEXT NOT NULL,
            success_count INTEGER NOT NULL DEFAULT 0,
            failure_count INTEGER NOT NULL DEFAULT 0,
            observation_count INTEGER NOT NULL DEFAULT 0,
            error_summary TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS availability_raw_responses (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            planned_node_at TEXT NOT NULL,
            target_date TEXT NOT NULL,
            fetched_at TEXT NOT NULL,
            status TEXT NOT NULL,
            response_json TEXT NOT NULL DEFAULT '',
            error_text TEXT NOT NULL DEFAULT '',
            UNIQUE(planned_node_at, target_date)
        );

        CREATE TABLE IF NOT EXISTS availability_observations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            planned_node_at TEXT NOT NULL,
            observed_at TEXT NOT NULL,
            target_date TEXT NOT NULL,
            court_number INTEGER NOT NULL,
            court_id TEXT NOT NULL,
            court_name TEXT NOT NULL,
            start_time TEXT NOT NULL,
            end_time TEXT NOT NULL,
            is_bookable INTEGER NOT NULL,
            price_value REAL,
            pay_value REAL,
            raw_state TEXT NOT NULL DEFAULT '',
            source_present INTEGER NOT NULL DEFAULT 0,
            UNIQUE(planned_node_at, target_date, court_number, start_time)
        );

        CREATE INDEX IF NOT EXISTS idx_availability_observations_target
            ON availability_observations(target_date, court_number, start_time);
        CREATE INDEX IF NOT EXISTS idx_availability_observations_node
            ON availability_observations(planned_node_at);
        """
    )
    now_text = format_timestamp(now_in_zone())
    connection.execute(
        """
        INSERT INTO schema_meta(key, value, updated_at)
        VALUES('schema_version', ?, ?)
        ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at
        """,
        (SCHEMA_VERSION, now_text),
    )
    connection.commit()


def fetch_availability_payload(
    client: EasySerpClient,
    *,
    shop_num: str,
    short_name: str,
    token: str,
    target_date: str,
) -> dict[str, Any]:
    return client.get(
        "datediscount/getPlaceInfoByShortNameDiscount",
        params={
            "shopNum": shop_num,
            "dateymd": target_date,
            "shortName": short_name,
            "token": token,
        },
    )


def places_from_payload(payload: dict[str, Any]) -> list[dict[str, Any]]:
    data = require_success(payload, "getPlaceInfoByShortNameDiscount")
    places = data.get("placeArray", []) if isinstance(data, dict) else []
    if not isinstance(places, list):
        raise EasySerpError("placeArray response is not a list")
    return [place for place in places if isinstance(place, dict)]


def collect_once(
    config: CollectorConfig,
    *,
    planned_node_at: datetime | None = None,
    today: date | None = None,
    client: EasySerpClient | None = None,
) -> CollectionSummary:
    started_at = now_in_zone()
    planned_at = planned_node_at or current_query_node(started_at)
    planned_text = format_timestamp(planned_at)
    date_values = target_date_values(today or started_at.date())
    active_client = client or EasySerpClient(config.base_url, config.token, config.jsessionid, config.timeout)
    observation_count = 0
    success_count = 0
    failures: list[str] = []

    with connect_database(config.db_path) as connection:
        upsert_run_start(connection, planned_text, format_timestamp(started_at), date_values)
        for target_date in date_values:
            fetched_at = format_timestamp(now_in_zone())
            try:
                payload = fetch_availability_payload(
                    active_client,
                    shop_num=config.shop_num,
                    short_name=config.short_name,
                    token=config.token,
                    target_date=target_date,
                )
                places = places_from_payload(payload)
                observations = build_observations(
                    planned_node_at=planned_text,
                    observed_at=fetched_at,
                    target_date=target_date,
                    places=places,
                )
                upsert_raw_response(connection, planned_text, target_date, fetched_at, "success", payload, "")
                upsert_observations(connection, observations)
                observation_count += len(observations)
                success_count += 1
            except Exception as exc:
                error_text = redact_sensitive_text(exc)
                failures.append(f"{target_date}: {error_text}")
                upsert_raw_response(connection, planned_text, target_date, fetched_at, "failed", {}, error_text)
        status = run_status(success_count, len(failures))
        error_summary = "; ".join(failures[:6])
        finish_run(
            connection,
            planned_text,
            format_timestamp(now_in_zone()),
            status,
            success_count,
            len(failures),
            observation_count,
            error_summary,
        )

    return CollectionSummary(
        planned_node_at=planned_text,
        status=status,
        success_count=success_count,
        failure_count=len(failures),
        observation_count=observation_count,
        target_dates=date_values,
        error_summary=error_summary,
    )


def upsert_run_start(connection: sqlite3.Connection, planned_node_at: str, started_at: str, date_values: list[str]) -> None:
    connection.execute(
        """
        INSERT INTO collector_runs(
            planned_node_at, started_at, finished_at, status, target_start_date, target_end_date,
            success_count, failure_count, observation_count, error_summary, created_at, updated_at
        )
        VALUES(?, ?, NULL, 'running', ?, ?, 0, 0, 0, '', ?, ?)
        ON CONFLICT(planned_node_at) DO UPDATE SET
            started_at = excluded.started_at,
            finished_at = NULL,
            status = 'running',
            target_start_date = excluded.target_start_date,
            target_end_date = excluded.target_end_date,
            success_count = 0,
            failure_count = 0,
            observation_count = 0,
            error_summary = '',
            updated_at = excluded.updated_at
        """,
        (planned_node_at, started_at, date_values[0], date_values[-1], started_at, started_at),
    )


def finish_run(
    connection: sqlite3.Connection,
    planned_node_at: str,
    finished_at: str,
    status: str,
    success_count: int,
    failure_count: int,
    observation_count: int,
    error_summary: str,
) -> None:
    connection.execute(
        """
        UPDATE collector_runs
        SET finished_at = ?, status = ?, success_count = ?, failure_count = ?,
            observation_count = ?, error_summary = ?, updated_at = ?
        WHERE planned_node_at = ?
        """,
        (finished_at, status, success_count, failure_count, observation_count, error_summary, finished_at, planned_node_at),
    )


def upsert_raw_response(
    connection: sqlite3.Connection,
    planned_node_at: str,
    target_date: str,
    fetched_at: str,
    status: str,
    payload: dict[str, Any],
    error_text: str,
) -> None:
    connection.execute(
        """
        INSERT INTO availability_raw_responses(
            planned_node_at, target_date, fetched_at, status, response_json, error_text
        )
        VALUES(?, ?, ?, ?, ?, ?)
        ON CONFLICT(planned_node_at, target_date) DO UPDATE SET
            fetched_at = excluded.fetched_at,
            status = excluded.status,
            response_json = excluded.response_json,
            error_text = excluded.error_text
        """,
        (planned_node_at, target_date, fetched_at, status, json.dumps(payload, ensure_ascii=False), error_text),
    )


def upsert_observations(connection: sqlite3.Connection, observations: list[dict[str, Any]]) -> None:
    rows = [
        (
            item["planned_node_at"],
            item["observed_at"],
            item["target_date"],
            item["court_number"],
            item["court_id"],
            item["court_name"],
            item["start_time"],
            item["end_time"],
            item["is_bookable"],
            item["price_value"],
            item["pay_value"],
            item["raw_state"],
            item["source_present"],
        )
        for item in observations
    ]
    connection.executemany(
        """
        INSERT INTO availability_observations(
            planned_node_at, observed_at, target_date, court_number, court_id, court_name,
            start_time, end_time, is_bookable, price_value, pay_value, raw_state, source_present
        )
        VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(planned_node_at, target_date, court_number, start_time) DO UPDATE SET
            observed_at = excluded.observed_at,
            court_id = excluded.court_id,
            court_name = excluded.court_name,
            end_time = excluded.end_time,
            is_bookable = excluded.is_bookable,
            price_value = excluded.price_value,
            pay_value = excluded.pay_value,
            raw_state = excluded.raw_state,
            source_present = excluded.source_present
        """,
        rows,
    )


def build_observations(
    *,
    planned_node_at: str,
    observed_at: str,
    target_date: str,
    places: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    available = available_slot_map(target_date, places)
    observations: list[dict[str, Any]] = []
    for court_number in COURT_NUMBERS:
        for start_time, end_time in hour_ranges():
            slot = available.get((court_number, start_time))
            observations.append(
                {
                    "planned_node_at": planned_node_at,
                    "observed_at": observed_at,
                    "target_date": target_date,
                    "court_number": court_number,
                    "court_id": clean_string(slot.get("court_id")) if slot else f"ymq{court_number}",
                    "court_name": clean_string(slot.get("court_name")) if slot else f"Court {court_number}",
                    "start_time": start_time,
                    "end_time": end_time,
                    "is_bookable": 1 if slot else 0,
                    "price_value": float_or_none(slot.get("price_value")) if slot else None,
                    "pay_value": float_or_none(slot.get("pay_value")) if slot else slot_pay_value(target_date, start_time),
                    "raw_state": clean_string(slot.get("raw_state")) if slot else "",
                    "source_present": 1 if slot else 0,
                }
            )
    return observations


def available_slot_map(target_date: str, places: list[dict[str, Any]]) -> dict[tuple[int, str], dict[str, Any]]:
    result: dict[tuple[int, str], dict[str, Any]] = {}
    for place in places:
        project = place.get("projectName", {}) if isinstance(place, dict) else {}
        court_id = clean_string(project.get("shortname"))
        court_name = clean_string(project.get("name")) or court_id
        court_number = court_number_from_text(court_id) or court_number_from_text(court_name)
        if court_number not in COURT_NUMBERS:
            continue
        for slot in place.get("projectInfo", []):
            if not isinstance(slot, dict) or clean_string(slot.get("state")) != "1":
                continue
            start_time, end_time = slot_times(slot)
            if (start_time, end_time) not in hour_ranges():
                continue
            price_value = slot_price_value(slot, target_date, start_time)
            result[(court_number, start_time)] = {
                "court_id": court_id or f"ymq{court_number}",
                "court_name": court_name or f"Court {court_number}",
                "price_value": price_value,
                "pay_value": slot_pay_value(target_date, start_time),
                "raw_state": clean_string(slot.get("state")),
            }
    return result


def slot_times(slot: dict[str, Any]) -> tuple[str, str]:
    start = normalize_time(slot.get("starttime") or slot.get("startTime") or slot.get("start") or "")
    end = normalize_time(slot.get("endtime") or slot.get("endTime") or slot.get("end") or "")
    return start, end


def normalize_time(value: Any) -> str:
    text = trim_time(value)
    match = re.fullmatch(r"(\d{1,2}):(\d{2})", text)
    if not match:
        return ""
    return f"{int(match.group(1)):02d}:{match.group(2)}"


def slot_price_value(slot: dict[str, Any], target_date: str, start_time: str) -> float:
    for key in ("oldMoney", "money"):
        value = float_or_none(slot.get(key))
        if value is not None:
            return value
    return slot_pay_value(target_date, start_time)


def slot_pay_value(target_date: str, start_time: str) -> float:
    target_day = date.fromisoformat(target_date)
    hour = int(start_time.split(":", 1)[0])
    if target_day.weekday() < 5 and hour < 16:
        return 20.0
    return 30.0


def run_status(success_count: int, failure_count: int) -> str:
    if failure_count == 0:
        return "success"
    if success_count > 0:
        return "partial"
    return "failed"


def court_number_from_text(value: Any) -> int | None:
    match = re.search(r"(\d+)", clean_string(value))
    if not match:
        return None
    try:
        return int(match.group(1))
    except ValueError:
        return None


def float_or_none(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def clean_string(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Collect court availability observations into SQLite.")
    parser.add_argument("--once", action="store_true", help="collect the current node and exit")
    parser.add_argument("--db-path", default=os.getenv("DAYDAYUP_AVAILABILITY_DB", str(DEFAULT_DB_PATH)), help="SQLite database path")
    parser.add_argument("-k", "--token", default=DEFAULT_TOKEN, help="wechat token")
    parser.add_argument("-j", "--jsessionid", default=DEFAULT_JSESSIONID, help="JSESSIONID")
    parser.add_argument("--shop-num", default=os.getenv("DAYDAYUP_SHOP_NUM", DEFAULT_SHOP_NUM), help="shop number")
    parser.add_argument("--base-url", default=os.getenv("DAYDAYUP_BASE_URL", DEFAULT_BASE_URL), help="EasySERP API base URL")
    parser.add_argument("--timeout", type=float, default=float(os.getenv("DAYDAYUP_TIMEOUT", "10")), help="request timeout in seconds")
    return parser.parse_args(argv)


def config_from_args(args: argparse.Namespace) -> CollectorConfig:
    token = clean_string(args.token)
    if not token:
        raise EasySerpError("missing token; pass -k or set DAYDAYUP_TOKEN")
    return CollectorConfig(
        db_path=Path(args.db_path),
        token=token,
        jsessionid=clean_string(args.jsessionid),
        shop_num=clean_string(args.shop_num) or DEFAULT_SHOP_NUM,
        base_url=clean_string(args.base_url) or DEFAULT_BASE_URL,
        timeout=float(args.timeout),
    )


def print_summary(summary: CollectionSummary) -> None:
    date_span = f"{summary.target_dates[0]}..{summary.target_dates[-1]}" if summary.target_dates else "-"
    print(
        "node={node} status={status} dates={dates} success={success} failed={failed} observations={observations}".format(
            node=summary.planned_node_at,
            status=summary.status,
            dates=date_span,
            success=summary.success_count,
            failed=summary.failure_count,
            observations=summary.observation_count,
        ),
        flush=True,
    )
    if summary.error_summary:
        print(f"errors={redact_sensitive_text(summary.error_summary)}", flush=True)


def run_forever(config: CollectorConfig) -> None:
    while True:
        next_node = next_query_node(now_in_zone())
        sleep_seconds = max(0.0, (next_node - now_in_zone()).total_seconds())
        print(f"next_node={format_timestamp(next_node)} sleep_seconds={sleep_seconds:.1f}", flush=True)
        time.sleep(sleep_seconds)
        summary = collect_once(config, planned_node_at=next_node)
        print_summary(summary)


def main(argv: list[str] | None = None) -> int:
    try:
        args = parse_args(argv)
        config = config_from_args(args)
        if args.once:
            summary = collect_once(config)
            print_summary(summary)
            return 0 if summary.status in {"success", "partial"} else 1
        run_forever(config)
    except KeyboardInterrupt:
        print("stopped", flush=True)
        return 130
    except Exception as exc:
        print(f"error={redact_sensitive_text(exc)}", file=sys.stderr, flush=True)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
