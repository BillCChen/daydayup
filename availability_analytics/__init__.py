from __future__ import annotations

import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from . import cache, extract, metrics


DEFAULT_AVAILABILITY_DB_PATH = Path(__file__).resolve().parents[1] / "local" / "availability.sqlite3"
DEFAULT_ANALYTICS_CACHE_DB_PATH = Path(__file__).resolve().parents[1] / "local" / "availability_analytics_cache.sqlite3"
DEFAULT_CACHE_TTL_SECONDS = int(os.getenv("AVAILABILITY_ANALYTICS_CACHE_TTL_SECONDS", "120"))
DEFAULT_WINDOW_DAYS = 7
DEFAULT_START_HOUR = 8
DEFAULT_END_HOUR = 22
LOCAL_TZ = ZoneInfo("Asia/Shanghai")


def _normalize_int(value: Any, default: int, minimum: int, maximum: int) -> int:
    try:
        return max(minimum, min(int(value), maximum))
    except (TypeError, ValueError):
        return default


def _normalize_window_days(value: Any) -> int:
    return max(1, min(_normalize_int(value, DEFAULT_WINDOW_DAYS, 1, 90), 90))


def _normalize_hours(start_hour: Any, end_hour: Any) -> tuple[int, int]:
    start = _normalize_int(start_hour, DEFAULT_START_HOUR, 0, 23)
    end = _normalize_int(end_hour, DEFAULT_END_HOUR, 0, 23)
    if end < start:
        return end, start
    return start, end


def _normalize_courts(courts: Any) -> tuple[int, ...]:
    if not courts:
        return tuple(range(1, 13))
    values: list[int] = []
    for item in courts:
        try:
            value = int(item)
        except (TypeError, ValueError):
            continue
        if 1 <= value <= 12:
            values.append(value)
    values = sorted(set(values))
    return tuple(values) if values else tuple(range(1, 13))


def _normalize_slots(raw_slots: Any, start_hour: int, end_hour: int) -> tuple[str, ...]:
    if raw_slots:
        normalized = []
        for item in raw_slots:
            try:
                normalized.append(extract.normalize_slot_start(item))
            except ValueError:
                continue
        normalized = sorted(set(normalized))
        if normalized:
            return tuple(normalized)
    return tuple(extract.default_hour_slots(start_hour, end_hour))


def _normalize_now(value: datetime | None) -> datetime:
    if value is None:
        return datetime.now(LOCAL_TZ)
    if value.tzinfo is None:
        return value.astimezone(LOCAL_TZ)
    return value


def _payload_metadata(
    metric: str,
    request: dict[str, Any],
    availability_db_path: Path,
    now: datetime,
 ) -> dict[str, Any]:
    observed_after = now - timedelta(days=request["window_days"])
    observed_after_text = observed_after.replace(microsecond=0).isoformat()

    if metric == "hour_court":
        payload = metrics.compute_hour_court(
            availability_db_path,
            observed_after=observed_after_text,
            court_numbers=request["courts"],
            slot_starts=request["slots"],
        )
        return payload

    if metric == "court_day":
        payload = metrics.compute_court_day(
            availability_db_path,
            observed_after=observed_after_text,
            court_numbers=request["courts"],
            slot_starts=request["slots"],
            window_days=request["window_days"],
            now=now,
        )
        return payload

    if metric == "timeseries":
        payload = metrics.compute_timeseries(
            availability_db_path,
            observed_after=observed_after_text,
            court_numbers=request["courts"],
            slot_starts=request["slots"],
        )
        return payload
    raise ValueError("invalid metric")


def _build_request(
    *,
    metric: str,
    window_days: Any,
    start_hour: Any,
    end_hour: Any,
    courts: Any,
    slots: Any,
) -> dict[str, Any]:
    metric_name = str(metric or "").strip().lower()
    if metric_name not in {"hour_court", "court_day", "timeseries"}:
        raise ValueError("invalid metric")
    start_hour, end_hour = _normalize_hours(start_hour, end_hour)
    return {
        "metric": metric_name,
        "window_days": _normalize_window_days(window_days),
        "start_hour": start_hour,
        "end_hour": end_hour,
        "courts": _normalize_courts(courts),
        "slots": _normalize_slots(slots, start_hour, end_hour),
    }


def get_analytics(
    metric: str,
    *,
    availability_db_path: Path = DEFAULT_AVAILABILITY_DB_PATH,
    cache_db_path: Path = DEFAULT_ANALYTICS_CACHE_DB_PATH,
    window_days: Any = DEFAULT_WINDOW_DAYS,
    start_hour: Any = DEFAULT_START_HOUR,
    end_hour: Any = DEFAULT_END_HOUR,
    courts: Any = None,
    slots: Any = None,
    cache_ttl_seconds: int = DEFAULT_CACHE_TTL_SECONDS,
    now: datetime | None = None,
) -> dict[str, Any]:
    request = _build_request(
        metric=metric,
        window_days=window_days,
        start_hour=start_hour,
        end_hour=end_hour,
        courts=courts,
        slots=slots,
    )
    observed_at = _normalize_now(now)
    observed_after_text = (observed_at - timedelta(days=request["window_days"])).replace(microsecond=0).isoformat()
    source_signature = extract.source_signature(
        availability_db_path,
        observed_after=observed_after_text,
        court_numbers=request["courts"],
        slot_starts=request["slots"],
    )
    cache_key = cache.make_cache_key(
        request["metric"],
        {
            "window_days": request["window_days"],
            "start_hour": request["start_hour"],
            "end_hour": request["end_hour"],
            "courts": request["courts"],
            "slots": request["slots"],
        },
    )
    payload = cache.get_cached_payload(
        cache_db_path=cache_db_path,
        cache_key=cache_key,
        metric=request["metric"],
        params={
            "window_days": request["window_days"],
            "start_hour": request["start_hour"],
            "end_hour": request["end_hour"],
            "courts": request["courts"],
            "slots": request["slots"],
        },
        source_signature=source_signature,
        ttl_seconds=cache_ttl_seconds,
        now=observed_at.timestamp(),
        compute_payload=lambda: _payload_metadata(
            request["metric"],
            request,
            availability_db_path,
            observed_at,
        ),
    )
    payload["request"] = {
        "metric": request["metric"],
        "window_days": request["window_days"],
        "start_hour": request["start_hour"],
        "end_hour": request["end_hour"],
        "courts": request["courts"],
        "slots": request["slots"],
    }
    payload["source_signature"] = source_signature
    return payload


def get_hour_court(**kwargs: Any) -> dict[str, Any]:
    return get_analytics("hour_court", **kwargs)


def get_court_day(**kwargs: Any) -> dict[str, Any]:
    return get_analytics("court_day", **kwargs)


def get_timeseries(**kwargs: Any) -> dict[str, Any]:
    return get_analytics("timeseries", **kwargs)
