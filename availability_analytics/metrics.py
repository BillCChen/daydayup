from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta
from typing import Any

from zoneinfo import ZoneInfo

from . import extract


TZ = ZoneInfo("Asia/Shanghai")


def compute_hour_court(
    db_path,
    *,
    observed_after: str,
    court_numbers: tuple[int, ...],
    slot_starts: tuple[str, ...],
) -> dict[str, Any]:
    observations = extract.load_observations(
        db_path,
        observed_after=observed_after,
        court_numbers=court_numbers,
        slot_starts=slot_starts,
    )
    totals = {
        slot: {court: 0.0 for court in court_numbers}
        for slot in slot_starts
    }
    for item in observations:
        if item["is_bookable"] != 1:
            continue
        totals.setdefault(item["start_time"], {})
        row = totals[item["start_time"]]
        if item["court_number"] not in row:
            row[item["court_number"]] = 0.0
        row[item["court_number"]] += 0.5
    slots_payload = []
    for slot in slot_starts:
        by_court = []
        court_map = totals.get(slot, {})
        for court in court_numbers:
            end_hour = (int(slot.split(":", 1)[0]) + 1) % 24
            by_court.append(
                {
                    "court": court,
                    "hours": float(court_map.get(court, 0.0)),
                }
            )
        total_hours = sum(item["hours"] for item in by_court)
        slots_payload.append(
            {
                "slot": slot,
                "start_time": slot,
                "end_time": f"{end_hour:02d}:00",
                "courts": by_court,
                "total_hours": total_hours,
            }
        )
    return {
        "metric": "hour_court",
        "window": {
            "start_slot": slot_starts[0] if slot_starts else None,
            "end_slot": slot_starts[-1] if slot_starts else None,
            "courts": len(court_numbers),
        },
        "rows": slots_payload,
        "observation_count": len(observations),
    }


def compute_court_day(
    db_path,
    *,
    observed_after: str,
    court_numbers: tuple[int, ...],
    slot_starts: tuple[str, ...],
    window_days: int,
    now: datetime | None = None,
) -> dict[str, Any]:
    observations = extract.load_observations(
        db_path,
        observed_after=observed_after,
        court_numbers=court_numbers,
        slot_starts=slot_starts,
    )
    end_time = now or datetime.now(tz=TZ)
    dates = [
        (end_time.date() - timedelta(days=offset)).isoformat()
        for offset in range(max(1, int(window_days)) - 1, -1, -1)
    ]
    day_totals = {court: {date_value: 0.0 for date_value in dates} for court in court_numbers}
    for item in observations:
        if item["is_bookable"] != 1:
            continue
        if item["target_date"] not in day_totals[item["court_number"]]:
            continue
        day_totals[item["court_number"]][item["target_date"]] += 0.5
    rows = []
    for court in court_numbers:
        day_values = []
        for date_value in dates:
            date_obj = datetime.fromisoformat(date_value).date()
            day_values.append(
                {
                    "date": date_value,
                    "hours": day_totals[court].get(date_value, 0.0),
                    "is_weekend": date_obj.weekday() >= 5,
                }
            )
        rows.append({"court": court, "days": day_values, "total_hours": sum(item["hours"] for item in day_values)})
    return {
        "metric": "court_day",
        "rows": rows,
        "observation_count": len(observations),
    }


def compute_timeseries(
    db_path,
    *,
    observed_after: str,
    court_numbers: tuple[int, ...],
    slot_starts: tuple[str, ...],
) -> dict[str, Any]:
    observations = extract.load_observations(
        db_path,
        observed_after=observed_after,
        court_numbers=court_numbers,
        slot_starts=slot_starts,
    )
    bucket_slots: defaultdict[str, set[str]] = defaultdict(set)
    for item in observations:
        if int(item["is_bookable"]) != 1:
            continue
        observed_at = datetime.fromisoformat(item["observed_at"])
        if observed_at.tzinfo is None:
            observed_at = observed_at.replace(tzinfo=TZ)
        local_time = observed_at.astimezone(TZ)
        bucket = local_time.replace(
            minute=0 if local_time.minute < 30 else 30,
            second=0,
            microsecond=0,
        )
        bucket_key = f"{int(item['court_number'])}|{item['target_date']}|{item['start_time']}"
        bucket_slots[bucket.isoformat()].add(bucket_key)
    points = []
    last_value: float | None = None
    for bucket in sorted(bucket_slots):
        value = 0.5 * len(bucket_slots[bucket])
        if last_value is None or value != last_value:
            points.append({"timestamp": bucket, "hours": value})
            last_value = value
    return {"metric": "timeseries", "points": points, "observation_count": len(observations)}
