from __future__ import annotations

import sqlite3
from datetime import timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

LOCAL_TZ = ZoneInfo("Asia/Shanghai")


def default_hour_slots(start_hour: int, end_hour: int) -> list[str]:
    start = max(0, min(int(start_hour), 23))
    end = max(0, min(int(end_hour), 23))
    if end < start:
        start, end = end, start
    return [f"{hour:02d}:00" for hour in range(start, end + 1)]


def normalize_slot_start(value: str) -> str:
    text = str(value).strip()
    if not text:
        raise ValueError("empty slot")
    if ":" in text:
        hour_text, minute_text = text.split(":", 1)
    else:
        hour_text, minute_text = text, "00"
    hour = int(hour_text)
    minute = int(minute_text or 0)
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise ValueError("invalid slot")
    return f"{hour:02d}:{minute:02d}"


def _build_connection(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(str(db_path))
    connection.row_factory = sqlite3.Row
    return connection


def _build_in_clause(items: tuple[Any, ...]) -> str:
    return ",".join(["?"] * len(items))


def source_signature(
    db_path: Path,
    *,
    observed_after: str,
    court_numbers: tuple[int, ...],
    slot_starts: tuple[str, ...],
) -> str:
    conditions = ["observed_at >= ?"]
    params: list[Any] = [observed_after]
    if court_numbers:
        conditions.append(f"court_number IN ({_build_in_clause(court_numbers)})")
        params.extend(court_numbers)
    if slot_starts:
        conditions.append(f"start_time IN ({_build_in_clause(slot_starts)})")
        params.extend(slot_starts)
    where = " AND ".join(conditions)
    with _build_connection(db_path) as connection:
        row = connection.execute(
            f"SELECT COALESCE(MAX(observed_at), '') AS max_observed_at, COUNT(*) AS row_count FROM availability_observations WHERE {where}",
            tuple(params),
        ).fetchone()
    return f"{row['max_observed_at']}|{row['row_count']}"


def load_observations(
    db_path: Path,
    *,
    observed_after: str,
    court_numbers: tuple[int, ...],
    slot_starts: tuple[str, ...],
) -> list[dict[str, Any]]:
    conditions = ["observed_at >= ?"]
    params: list[Any] = [observed_after]
    if court_numbers:
        conditions.append(f"court_number IN ({_build_in_clause(court_numbers)})")
        params.extend(court_numbers)
    if slot_starts:
        conditions.append(f"start_time IN ({_build_in_clause(slot_starts)})")
        params.extend(slot_starts)
    where = " AND ".join(conditions)
    query = """
        SELECT observed_at, target_date, court_number, start_time, is_bookable
        FROM availability_observations
        WHERE %s
        ORDER BY observed_at ASC, court_number ASC, start_time ASC
    """ % where
    with _build_connection(db_path) as connection:
        rows = connection.execute(query, tuple(params)).fetchall()
    return [
        {
            "observed_at": row["observed_at"],
            "target_date": row["target_date"],
            "court_number": int(row["court_number"]),
            "start_time": row["start_time"],
            "is_bookable": int(row["is_bookable"]),
        }
        for row in rows
    ]


def observed_after_text(now: Any | None = None, window_days: int = 7) -> str:
    now_time = now
    if now_time is None:
        from datetime import datetime

        now_time = datetime.now(tz=LOCAL_TZ)
    return (now_time - timedelta(days=window_days)).replace(microsecond=0).isoformat()
