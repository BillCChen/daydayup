#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from datetime import datetime, timedelta
import math
from pathlib import Path
from typing import Any

import availability_analytics


def parse_int_list(value: str | None) -> list[int] | None:
    if not value:
        return None
    items: list[int] = []
    for item in value.replace(",", " ").split():
        try:
            items.append(int(item))
        except (TypeError, ValueError):
            continue
    return items


def parse_slots(value: str | None) -> list[str] | None:
    if not value:
        return None
    slots = [item.strip() for item in value.replace(",", " ").split() if item.strip()]
    return slots or None


def render_heat_colgroup(
    lead_columns: int,
    data_columns: int,
) -> str:
    cols = [
        f"<col style=\"width:var(--heat-label-size);min-width:var(--heat-label-size);max-width:var(--heat-label-size);\">"
    ]
    for _ in range(data_columns):
        cols.append(
            "<col style=\"width:var(--heat-cell-size);min-width:var(--heat-cell-size);max-width:var(--heat-cell-size);\">"
        )
    cols.append(
        "<col style=\"width:var(--heat-cell-size);min-width:var(--heat-cell-size);max-width:var(--heat-cell-size);\">"
    )
    if not lead_columns:
        cols[0] = "<col style=\"width:var(--heat-cell-size);min-width:var(--heat-cell-size);max-width:var(--heat-cell-size);\">"
    return "<colgroup>" + "".join(cols[:lead_columns + data_columns + 1]) + "</colgroup>"


def ensure_non_empty_rows(value: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return value if isinstance(value, list) and value else []


def format_date_chinese(date_text: str) -> str:
    try:
        date_value = datetime.fromisoformat(date_text).date()
        return f"{date_value.year}年{date_value.month:02d}月{date_value.day:02d}日"
    except ValueError:
        return date_text


def format_observed_time_chinese(observed_at: datetime) -> str:
    return f"{observed_at.year}年{observed_at.month:02d}月{observed_at.day:02d}日 {observed_at.hour:02d}:{observed_at.minute:02d}"


def format_slot_range(slot_start: str, slot_end: str | None = None) -> str:
    start_text = str(slot_start).strip()
    if ":" in start_text:
        hour_text, minute_text = start_text.split(":", 1)
    else:
        hour_text, minute_text = start_text, "00"

    try:
        start_hour = int(hour_text) % 24
        start_minute = int((minute_text or "0")) % 60
    except ValueError:
        return start_text

    end_hour = None
    end_minute = None
    if slot_end:
        end_text = str(slot_end).strip()
        if ":" in end_text:
            end_hour_text, end_minute_text = end_text.split(":", 1)
        else:
            end_hour_text, end_minute_text = end_text, start_minute
        try:
            end_hour = int(end_hour_text) % 24
            end_minute = int((end_minute_text or "0")) % 60
        except ValueError:
            pass

    if end_hour is None or end_minute is None:
        end_hour = (start_hour + 1) % 24
        end_minute = start_minute

    return f"{start_hour:02d}:{start_minute:02d}-{end_hour:02d}:{end_minute:02d}"


def collect_detail_indexes(
    observations: list[dict[str, Any]],
) -> tuple[
    dict[str, list[str]],
    dict[str, list[dict[str, Any]]],
    dict[str, list[int]],
    dict[str, list[dict[str, Any]]],
]:
    hour_court_detail: dict[str, set[str]] = defaultdict(set)
    court_day_detail: dict[str, defaultdict[str, int]] = defaultdict(lambda: defaultdict(int))
    timeseries_detail: dict[str, set[str]] = {}

    for item in observations:
        if int(item.get("is_bookable", 0)) != 1:
            continue

        court = int(item["court_number"])
        slot = str(item["start_time"])
        date_value = str(item["target_date"])
        observed_at_text = str(item.get("observed_at"))

        try:
            observed_at = datetime.fromisoformat(observed_at_text)
        except ValueError:
            continue
        if observed_at.tzinfo is None:
            observed_at = observed_at.replace(tzinfo=availability_analytics.LOCAL_TZ)
        else:
            observed_at = observed_at.astimezone(availability_analytics.LOCAL_TZ)

        bucket = observed_at.replace(minute=0 if observed_at.minute < 30 else 30, second=0, microsecond=0)
        observed_label = format_observed_time_chinese(observed_at)

        hour_court_detail[f"{slot}|{court}"].add(
            f"{format_date_chinese(date_value)} · 记录于 {observed_label}"
        )
        court_day_detail[f"{court}|{date_value}"][format_slot_range(slot)] += 1
        timeseries_key = f"{court}|{date_value}|{format_slot_range(slot)}"
        bucket_key = bucket.isoformat()
        timeseries_detail.setdefault(bucket_key, set()).add(timeseries_key)

    hour_court_map = {key: sorted(values) for key, values in hour_court_detail.items()}
    court_day_map = {
        key: [
            {"time": slot, "count": count}
            for slot, count in sorted(
                ((slot, count) for slot, count in values.items()),
                key=lambda item: item[0],
            )
        ]
        for key, values in court_day_detail.items()
    }
    timeseries_map: dict[str, list[int]] = {}
    timeseries_detail_map: dict[str, list[dict[str, Any]]]
    timeseries_detail_map = {}
    for bucket, totals in timeseries_detail.items():
        courts: set[int] = set()
        details: list[dict[str, Any]] = []
        for composite_key in sorted(totals):
            court_text, date_text, time_text = composite_key.split("|", 2)
            court_value = int(court_text)
            courts.add(court_value)
            details.append(
                {
                    "court": court_value,
                    "date": date_text,
                    "time": time_text,
                    "count": 1,
                }
            )
        timeseries_map[bucket] = sorted(courts)
        details.sort(key=lambda item: (item["court"], item["date"], item["time"]))
        timeseries_detail_map[bucket] = details
    return hour_court_map, court_day_map, timeseries_map, timeseries_detail_map


def build_court_day_payload(
    observations: list[dict[str, Any]],
    courts: tuple[int, ...],
    *,
    now: datetime,
    past_days: int,
    future_days: int,
) -> list[dict[str, Any]]:
    base_time = now.astimezone(availability_analytics.LOCAL_TZ)
    base_date = base_time.date()

    past_days = max(0, int(past_days))
    future_days = max(0, int(future_days))
    target_dates = [
        (base_date - timedelta(days=day)).isoformat()
        for day in range(past_days, -1, -1)
    ] + [
        (base_date + timedelta(days=day)).isoformat()
        for day in range(1, future_days + 1)
    ]

    day_totals = {
        court: {date_value: 0.0 for date_value in target_dates}
        for court in courts
    }

    target_set = set(target_dates)
    for item in observations:
        if int(item.get("is_bookable", 0)) != 1:
            continue

        court = int(item.get("court_number"))
        if court not in day_totals:
            continue

        date_value = str(item.get("target_date"))
        if date_value not in target_set:
            continue

        day_totals[court][date_value] = day_totals[court].get(date_value, 0.0) + 0.5

    rows: list[dict[str, Any]] = []
    for court in courts:
        days: list[dict[str, Any]] = []
        for date_value in target_dates:
            date_obj = datetime.strptime(date_value, "%Y-%m-%d").date()
            total_hours = float(day_totals[court].get(date_value, 0.0))
            days.append(
                {
                    "date": date_value,
                    "hours": total_hours,
                    "is_weekend": date_obj.weekday() >= 5,
                }
            )

        rows.append({
            "court": court,
            "days": days,
            "total_hours": sum(item["hours"] for item in days),
        })

    return rows


def render_hour_court_html(
    rows: list[dict[str, Any]],
    start_hour: int,
    end_hour: int,
    detail_map: dict[str, list[str]],
) -> str:
    if not rows:
        return "<p class=\"hint\">暂无可约数据。</p>"

    hourly_values = [[float(court.get("hours", 0.0)) for court in row.get("courts", [])] for row in rows]
    max_hours = max((x for row in hourly_values for x in row), default=0.0)

    def color_for(value: float) -> str:
        if value <= 0 or max_hours <= 0:
            return "#f8fafc"
        ratio = value / max_hours
        if ratio < 0.34:
            return "#fde2f3"
        if ratio < 0.67:
            return "#fbcfe8"
        if ratio < 0.9:
            return "#fed7aa"
        return "#fdba74"

    courts = [str(court.get("court")) for court in rows[0].get("courts", [])]
    colgroup = render_heat_colgroup(1, len(courts))
    header = (
        "<tr><th>时间段</th>"
        + "".join(f"<th>场地 {court}</th>" for court in courts)
        + "<th class=\"total-head\">总计</th></tr>"
    )

    body_rows: list[str] = []
    for row in rows:
        slot = str(row.get("slot", ""))
        slot_start = str(row.get("start_time", slot))
        slot_end = str(row.get("end_time", "")).strip() or None
        slot_label = format_slot_range(slot_start, slot_end)
        cells: list[str] = [f'<td class="label-cell"><strong>{slot_label}</strong></td>']
        for court in row.get("courts", []):
            court_number = court.get("court")
            hours = float(court.get("hours", 0.0))
            key = f"{slot}|{court_number}"
            cells.append(
                f'<td class="heat-cell clickable" style="background:{color_for(hours)}" '
                f'data-type="hour-court" data-key="{key}" data-title="{slot_label} · 场地 {court_number}" '
                '>'
                f"{hours:.1f}</td>"
            )
        total = float(row.get("total_hours", 0.0))
        cells.append(f'<td class="strong total-cell">{total:.1f}</td>')
        body_rows.append("<tr>" + "".join(cells) + "</tr>")

    return f"""
    <table class="heat-table">
      {colgroup}
      <thead>{header}</thead>
      <tbody>{"".join(body_rows)}</tbody>
    </table>
    <p class="meta">时间范围：{start_hour:02d}:00 - {end_hour:02d}:00，单位：0.5 小时一条记录</p>
    """


def render_court_day_html(rows: list[dict[str, Any]], detail_map: dict[str, list[str]]) -> str:
    if not rows:
        return "<p class=\"hint\">暂无可约数据。</p>"

    date_headers = [day.get("date") for day in rows[0].get("days", [])]
    colgroup = render_heat_colgroup(1, len(date_headers))
    header = (
        "<tr><th>场地</th>"
        + "".join(
            f'<th class="court-day-date" data-court-day-date="{date}">{datetime.strptime(date, "%Y-%m-%d").strftime("%m-%d")}</th>'
            for date in date_headers
        )
        + "<th class=\"total-head\">总计</th></tr>"
    )
    body_rows: list[str] = []

    for row in rows:
        court_number = str(row.get("court"))
        cells: list[str] = [f"<td class=\"label-cell\"><strong>{court_number}</strong></td>"]
        for day in row.get("days", []):
            value = float(day.get("hours", 0.0))
            date_value = str(day.get("date"))
            key = f"{court_number}|{date_value}"
            if day.get("is_weekend"):
                heat_class = "weekend"
            elif value > 1:
                heat_class = "hot"
            elif value > 0:
                heat_class = "warm"
            else:
                heat_class = "cold"
            cells.append(
                f'<td class="heat-cell clickable {heat_class}" data-type="court-day" data-key="{key}" '
                f'data-title="{date_value} · 场地 {court_number}" data-court-day-date="{date_value}">{value:.1f}</td>'
            )
        total = float(row.get("total_hours", 0.0))
        cells.append(f'<td class="strong total-cell">{total:.1f}</td>')
        body_rows.append("<tr>" + "".join(cells) + "</tr>")

    return f"""
    <table class="heat-table">
      {colgroup}
      <thead>{header}</thead>
      <tbody>{"".join(body_rows)}</tbody>
    </table>
    <p class="meta">日期为按天聚合，周末格子同样支持点击查看明细。</p>
    """


def render_timeseries_svg(points: list[dict[str, Any]]) -> str:
    if not points:
        return "<p class=\"hint\">暂无变化点。</p>"

    parsed_points = []
    for point in points:
        raw_timestamp = point.get("timestamp")
        if not isinstance(raw_timestamp, str):
            continue

        try:
            point_hours = float(point.get("hours", 0.0))
        except (TypeError, ValueError):
            continue

        try:
            parsed_dt = datetime.fromisoformat(raw_timestamp)
        except ValueError:
            continue

        if parsed_dt.tzinfo is None:
            parsed_dt = parsed_dt.replace(tzinfo=availability_analytics.LOCAL_TZ)
        else:
            parsed_dt = parsed_dt.astimezone(availability_analytics.LOCAL_TZ)

        parsed_points.append((parsed_dt, raw_timestamp, point_hours))

    if not parsed_points:
        return "<p class=\"hint\">暂无可用数据。</p>"

    values = [item[2] for item in parsed_points]
    max_y = max(values) if values else 0.0
    min_y = min(values) if values else 0.0
    if max_y == min_y:
        max_y = min_y + 0.5

    tick_min = 0.0
    tick_max = math.ceil(max_y * 2) / 2
    if tick_min == tick_max:
        tick_max = tick_min + 0.5

    width = 1100
    height = 300
    chart_left = 72
    chart_right = 30
    chart_top = 20
    chart_bottom = 85
    inner_w = width - chart_left - chart_right
    inner_h = height - chart_top - chart_bottom

    sorted_points = sorted(parsed_points, key=lambda item: item[0])
    points_for_chart: list[tuple[int, str, float]] = [
        (int(point_dt.timestamp() * 1000), raw_timestamp, float(point_hours))
        for point_dt, raw_timestamp, point_hours in sorted_points
    ]
    time_values = [item[0] for item in points_for_chart]
    min_t = min(time_values)
    max_t = max(time_values)
    time_span_ms = max_t - min_t
    if time_span_ms == 0:
        time_span_ms = 30 * 60 * 1000

    def x_at(ts_ms: int) -> float:
        ratio = (ts_ms - min_t) / time_span_ms
        return chart_left + ratio * inner_w

    def y_at(v: float) -> float:
        return chart_top + inner_h * (1 - (v - tick_min) / (tick_max - tick_min))

    point_changes: dict[int, tuple[str, float]] = {}
    for ts_ms, display_time, value in points_for_chart:
        point_changes[ts_ms] = (display_time, float(value))
    change_ts_set = set(point_changes.keys())
    sorted_change_times = sorted(change_ts_set)

    values_by_tick: list[tuple[int, float, bool, int]] = []
    current_value = float(points_for_chart[0][2])
    current_change_idx = 0
    for current_tick in range(min_t, max_t + 1, 30 * 60 * 1000):
        while (
            current_change_idx + 1 < len(sorted_change_times)
            and sorted_change_times[current_change_idx + 1] <= current_tick
        ):
            current_change_idx += 1
            current_value = point_changes[sorted_change_times[current_change_idx]][1]
        is_change = current_tick in change_ts_set
        active_change_ts = sorted_change_times[current_change_idx]
        values_by_tick.append((current_tick, current_value, is_change, active_change_ts))

    points_path = " ".join(f"{x_at(t):.2f},{y_at(v):.2f}" for t, v, _, _ in values_by_tick)
    circles: list[str] = []
    ticks: list[str] = []

    for ts_ms, value, is_change, active_change_ts in values_by_tick:
        point_dt = datetime.fromtimestamp(ts_ms / 1000, tz=availability_analytics.LOCAL_TZ)
        x = x_at(ts_ms)
        y = y_at(value)
        label = point_dt.strftime("%m-%d %H:%M")
        display_time = point_changes.get(active_change_ts, (point_dt.isoformat(), value))[0]
        point_class = "point clickable"
        circle_radius = "4.5" if is_change else "3.2"
        circles.append(
            f'<circle class="{point_class}" cx="{x:.2f}" cy="{y:.2f}" r="{circle_radius}" data-cx="{x:.2f}" data-cy="{y:.2f}" '
            f'data-timestamp="{display_time}" data-title="{label}" data-is-change="{str(is_change).lower()}"/>'
        )
        tick_label = f"{label}" if point_dt.minute == 0 and point_dt.hour % 4 == 0 else ""
        ticks.append(
            f"""
          <g class="axis-tick">
            <line x1="{x:.2f}" y1="{chart_top + inner_h}" x2="{x:.2f}" y2="{chart_top + inner_h + 7}" />
            {f'<text x="{x:.2f}" y="{chart_top + inner_h + 44}" text-anchor="end" transform="rotate(45 {x:.2f} {chart_top + inner_h + 44:.2f})">{tick_label}</text>' if tick_label else ''}
          </g>
        """
        )

    y_ticks: list[str] = []
    tick_count = int((tick_max - tick_min) / 0.5) + 1
    for i in range(tick_count):
        value = tick_min + 0.5 * i
        y = y_at(value)
        y_ticks.append(
            f"""
          <g>
            <line x1="{chart_left}" x2="{width - chart_right}" y1="{y:.2f}" y2="{y:.2f}" class="grid" />
          <text x="{chart_left - 8}" y="{y:.2f}" text-anchor="end" dominant-baseline="middle" class="axis-title">{value:.1f}</text>
          </g>
        """
        )

    return f"""
    <svg viewBox="0 0 {width} {height}" class="chart" role="img" aria-label="timeseries">
      <defs>
        <linearGradient id="lineGradient" x1="0" y1="0" x2="1" y2="0">
          <stop offset="0%" stop-color="#2563eb" />
          <stop offset="100%" stop-color="#06b6d4" />
        </linearGradient>
      </defs>
      {"".join(y_ticks)}
      <polyline points="{points_path}" fill="none" stroke="url(#lineGradient)" stroke-width="2.5" stroke-linejoin="round" stroke-linecap="round" />
      <line x1="{chart_left}" y1="{chart_top + inner_h}" x2="{width - chart_right}" y2="{chart_top + inner_h}" class="axis" />
      <line x1="{chart_left}" y1="{chart_top}" x2="{chart_left}" y2="{chart_top + inner_h}" class="axis" />
      {"".join(ticks)}
      {"".join(circles)}
    </svg>
    <p class="meta">每半小时采样点在折线上连续展示；X 轴刻度文本已隐藏以避免拥挤。</p>
    """


def load_all_data(args: argparse.Namespace) -> tuple[
    dict[str, Any],
    dict[str, Any],
    dict[str, Any],
    dict[str, list[str]],
    dict[str, list[dict[str, Any]]],
    dict[str, list[int]],
    dict[str, list[dict[str, Any]]],
]:
    now = datetime.now(tz=availability_analytics.LOCAL_TZ)
    common = {
        "availability_db_path": Path(args.availability_db),
        "cache_db_path": Path(args.cache_db),
        "window_days": args.window_days,
        "start_hour": args.start_hour,
        "end_hour": args.end_hour,
        "courts": args.courts,
        "slots": args.slots,
        "cache_ttl_seconds": args.cache_ttl_seconds,
    }

    hour_court = availability_analytics.get_hour_court(now=now, **common)
    court_day = availability_analytics.get_court_day(now=now, **common)
    timeseries = availability_analytics.get_timeseries(
        availability_db_path=Path(args.availability_db),
        cache_db_path=Path(args.cache_db),
        window_days=args.window_days,
        courts=args.courts,
        slots=args.slots,
        cache_ttl_seconds=args.cache_ttl_seconds,
        now=now,
    )

    observed_after = availability_analytics.extract.observed_after_text(now=now, window_days=args.window_days)
    observations = availability_analytics.extract.load_observations(
        Path(args.availability_db),
        observed_after=observed_after,
        court_numbers=tuple(hour_court["request"]["courts"]),
        slot_starts=tuple(hour_court["request"]["slots"]),
    )

    court_rows = build_court_day_payload(
        observations,
        tuple(hour_court["request"]["courts"]),
        now=now,
        past_days=max(0, args.court_day_past_days),
        future_days=max(0, args.court_day_future_days),
    )
    court_day["rows"] = court_rows

    hour_court_detail, court_day_detail, timeseries_court_map, timeseries_detail_map = collect_detail_indexes(
        observations
    )
    return (
        hour_court,
        court_day,
        timeseries,
        hour_court_detail,
        court_day_detail,
        timeseries_court_map,
        timeseries_detail_map,
    )


def build_data_payload(args: argparse.Namespace) -> dict[str, Any]:
    hour_payload, court_payload, timeseries_payload, hour_detail, court_detail, _timeseries_court_map, timeseries_detail = load_all_data(args)
    hour_rows = ensure_non_empty_rows(hour_payload.get("rows", []))
    court_rows = ensure_non_empty_rows(court_payload.get("rows", []))
    timeseries_points = [item for item in timeseries_payload.get("points", []) if isinstance(item, dict)]

    point_courts = {
        str(point.get("timestamp")): timeseries_detail.get(str(point.get("timestamp")), [])
        for point in timeseries_points
    }
    request_payload = dict(hour_payload.get("request", {})) if isinstance(hour_payload.get("request"), dict) else {}
    court_payload_request = court_payload.get("request")
    if isinstance(court_payload_request, dict):
        request_payload.update(
            {
                key: court_payload_request.get(key)
                for key in ("court_day_past_days", "court_day_future_days")
            }
        )

    courts = request_payload.get("courts")
    if isinstance(courts, tuple):
        request_payload["courts"] = list(courts)
    slots = request_payload.get("slots")
    if isinstance(slots, tuple):
        request_payload["slots"] = list(slots)

    request_payload.setdefault("court_day_past_days", max(0, int(args.court_day_past_days)))
    request_payload.setdefault("court_day_future_days", max(0, int(args.court_day_future_days)))

    return {
        "generated_at": datetime.now(tz=availability_analytics.LOCAL_TZ).isoformat(),
        "request": request_payload,
        "cache": {
            "hour": hour_payload.get("cache", {}),
            "court_day": court_payload.get("cache", {}),
            "timeseries": timeseries_payload.get("cache", {}),
        },
        "source_signature": hour_payload.get("source_signature", "-"),
        "hour_court_table_html": render_hour_court_html(
            hour_rows,
            request_payload.get("start_hour", args.start_hour),
            request_payload.get("end_hour", args.end_hour),
            hour_detail,
        ),
        "court_day_table_html": render_court_day_html(court_rows, court_detail),
        "timeseries_svg_html": render_timeseries_svg(timeseries_points),
        "hour_court_details": hour_detail,
        "court_day_details": court_detail,
        "timeseries_court_details": point_courts,
    }


def build_html(args: argparse.Namespace) -> str:
    return f"""<!doctype html>
<html lang=\"zh-CN\">
<head>
  <meta charset=\"UTF-8\" />
  <meta name=\"viewport\" content=\"width=device-width, initial-scale=1.0\" />
  <title>Availability Analytics</title>
  <style>
    :root {{
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "Microsoft YaHei", sans-serif;
      color: #1f2937;
      background: #f8fafc;
      line-height: 1.4;
      --heat-cell-size: 56px;
      --heat-label-size: 64px;
      --heat-radius: 10px;
    }}
    body {{
      margin: 0;
      padding: 20px;
    }}
    .container {{
      max-width: 1360px;
      margin: 0 auto;
      display: grid;
      gap: 16px;
    }}
    h1 {{
      margin: 0;
      font-size: 1.5rem;
    }}
    .card {{
      border: 1px solid #d1d5db;
      border-radius: 10px;
      background: #fff;
      padding: 14px 16px;
      box-shadow: 0 2px 8px rgba(0, 0, 0, 0.04);
    }}
    .report-header {{
      text-align: center;
      padding: 20px 16px 18px;
    }}
    .report-title {{
      margin: 0 0 8px;
      font-size: 1.8rem;
      font-weight: 800;
      color: #0f172a;
      letter-spacing: 0.01em;
      line-height: 1.25;
    }}
    .report-meta {{
      margin: 0;
      color: #334155;
      font-size: 1rem;
      font-weight: 500;
      line-height: 1.5;
    }}
    .meta {{
      margin: 8px 0 0;
      color: #6b7280;
      font-size: 0.9rem;
    }}
    .date-filter {{
      margin-top: 10px;
      display: flex;
      flex-wrap: wrap;
      align-items: center;
      gap: 10px;
    }}
    .filter-field {{
      display: inline-flex;
      align-items: center;
      gap: 6px;
      color: #334155;
      font-size: 0.89rem;
      white-space: nowrap;
    }}
    .filter-field input[type="date"],
    .filter-field input[type="number"] {{
      border: 1px solid #cbd5e1;
      border-radius: 8px;
      padding: 6px 8px;
      color: #0f172a;
      font-size: 0.9rem;
      background: #fff;
    }}
    .filter-field input[type="number"] {{
      width: 86px;
    }}
    .filter-btn {{
      appearance: none;
      border: 1px solid #64748b;
      border-radius: 8px;
      background: #fff;
      color: #0f172a;
      font-size: 0.89rem;
      padding: 6px 11px;
      cursor: pointer;
      transition: all 0.2s ease;
    }}
    .filter-btn:hover {{
      border-color: #0f172a;
      background: #f1f5f9;
    }}
    .filter-btn:active {{
      transform: translateY(1px);
    }}
    .heat-table {{
      width: max-content;
      border-collapse: separate;
      border-spacing: 6px;
      margin-top: 8px;
      table-layout: fixed;
      font-variant-numeric: tabular-nums;
      box-sizing: border-box;
    }}
    th, td {{
      border: 0;
      padding: 0;
      box-sizing: border-box;
      text-align: center;
      white-space: nowrap;
      vertical-align: middle;
    }}
    th {{
      background: #f1f5f9;
      font-weight: 600;
      padding: 6px 0;
    }}
    .label-cell {{
      width: var(--heat-label-size);
      height: var(--heat-cell-size);
      border-radius: var(--heat-radius);
      color: #334155;
      line-height: var(--heat-cell-size);
      overflow: hidden;
      text-overflow: ellipsis;
    }}
    .strong {{
      font-weight: 700;
      color: #0f172a;
      width: var(--heat-cell-size);
      height: var(--heat-cell-size);
      border-radius: var(--heat-radius);
      line-height: var(--heat-cell-size);
      overflow: hidden;
      text-overflow: ellipsis;
    }}
    .total-cell {{
      background: #f8fafc;
      border: 1px dashed #0f172a;
    }}
    .total-head {{
      background: #fde68a;
      color: #0f172a;
      border: 1px dashed #0f172a;
    }}
    .heat-cell {{
      width: var(--heat-cell-size);
      height: var(--heat-cell-size);
      border-radius: var(--heat-radius);
      font-weight: 600;
      color: #0f172a;
      border: none;
      cursor: default;
      transition: box-shadow 0.2s ease;
      line-height: var(--heat-cell-size);
      overflow: hidden;
      text-overflow: ellipsis;
    }}
    .heat-cell.clickable {{
      cursor: pointer;
    }}
    .heat-cell.clickable:hover {{
      box-shadow: 0 0 0 2px rgba(14, 165, 233, 0.35);
    }}
    .hot {{ background: #fef3c7; }}
    .warm {{ background: #fee2e2; }}
    .cold {{ background: #f8fafc; }}
    .weekend {{ background: #fbcfe8; }}
    .heat-table th:first-child {{
      width: var(--heat-label-size);
      min-width: var(--heat-label-size);
    }}
    .heat-table th:not(:first-child),
    .heat-table td:not(:first-child) {{
      width: var(--heat-cell-size);
      min-width: var(--heat-cell-size);
    }}
    .chart {{
      width: 100%;
      height: auto;
      display: block;
      margin-top: 8px;
    }}
    .axis {{ stroke: #94a3b8; stroke-width: 1; }}
    .grid {{ stroke: #e5e7eb; stroke-width: 1; }}
    .point {{ fill: #2563eb; cursor: pointer; transition: fill 0.2s ease; }}
    .point:hover {{
      fill: #4338ca;
      stroke: #0f172a;
      stroke-width: 2;
    }}
    .axis-title {{ fill: #64748b; font-size: 11px; }}
    .axis-tick text {{ fill: #64748b; font-size: 11px; }}
    .axis-tick line {{ stroke: #94a3b8; }}
    .hint {{ color: #6b7280; margin: 10px 0 0; }}
    .grid-wrap {{
      overflow-x: auto;
      width: 100%;
    }}
    .detail-popover {{
      position: fixed;
      left: 0;
      top: 0;
      transform: translate3d(0, 0, 0);
      min-width: 220px;
      max-width: 320px;
      background: #ffffff;
      border: 1px solid #d9e2ec;
      border-radius: 10px;
      box-shadow: 0 12px 24px rgba(15, 23, 42, 0.2);
      padding: 10px 12px;
      color: #0f172a;
      z-index: 20;
      display: none;
    }}
    .detail-popover.visible {{
      display: block;
    }}
    .detail-popover h3 {{
      margin: 0;
      font-size: 0.93rem;
      font-weight: 600;
    }}
    .detail-popover ul {{
      margin: 8px 0 0;
      padding-left: 18px;
      max-height: 240px;
      overflow-y: auto;
      font-size: 0.89rem;
      color: #334155;
    }}
    .detail-popover li {{
      margin: 2px 0;
    }}
  </style>
</head>
<body>
  <div class="container">
    <div class="card report-header">
      <h1 class="report-title">Availability Analytics 报表</h1>
      <p class="report-meta">默认加载：<span id="report-config">正在读取数据源…</span>；场地×日期可见范围：<span id="court-day-visible-range">正在计算…</span></p>
    </div>
    <div class="date-filter" role="region" aria-label="日期范围筛选">
      <label class="filter-field">
        <span>目标日期</span>
        <input id="filter-target-date" type="date" />
      </label>
      <label class="filter-field">
        <span>过去（天）</span>
        <input id="filter-past-days" type="number" min="0" step="1" value="0" />
      </label>
      <label class="filter-field">
        <span>未来（天）</span>
        <input id="filter-future-days" type="number" min="0" step="1" value="6" />
      </label>
      <button type="button" class="filter-btn" id="filter-apply">应用筛选</button>
      <button type="button" class="filter-btn" id="filter-reset">重置（今天，-0/+6）</button>
    </div>

    <section class="card">
      <h2>1）时间槽 × 场地 热力图（每小时可约时长）</h2>
      <div class="grid-wrap" id="hour-court-heatmap"><p class="hint">正在加载表格…</p></div>
    </section>

    <section class="card">
      <h2>2）场地 × 日期 热力图（按天汇总）</h2>
      <div class="grid-wrap" id="court-day-heatmap"><p class="hint">正在加载表格…</p></div>
    </section>

    <section class="card">
      <h2>3）变化点折线图（精确半小时）</h2>
      <div id="timeseries-chart"><p class="hint">正在加载图表…</p></div>
    </section>

    <section class="card">
      <h2>缓存与口径</h2>
      <p class="meta">数据源签名：<code id="cache-source-signature">读取中…</code></p>
      <p class="meta">小时图 cache hit: <span id="cache-hour-hit">-</span></p>
      <p class="meta">court_day cache hit: <span id="cache-court-day-hit">-</span></p>
      <p class="meta">timeseries cache hit: <span id="cache-timeseries-hit">-</span></p>
      <p class="meta" id="data-load-error" style="display:none;color:#b91c1c;"></p>
    </section>
  </div>
  <div id="detail-popover" class="detail-popover" role="status" aria-live="polite"></div>

  <script src="availability_analytics_data_index.js"></script>
  <script src="availability_analytics_report_loader.js"></script>
</body>
</html>
"""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Render availability analytics to a local html file.")
    parser.add_argument("--availability-db", default="local/availability.sqlite3")
    parser.add_argument("--cache-db", default="local/availability_analytics_cache.sqlite3")
    parser.add_argument("--window-days", type=int, default=11)
    parser.add_argument("--court-day-past-days", type=int, default=10)
    parser.add_argument("--court-day-future-days", type=int, default=10)
    parser.add_argument("--start-hour", type=int, default=8)
    parser.add_argument("--end-hour", type=int, default=22)
    parser.add_argument("--courts", type=str, default="")
    parser.add_argument("--slots", type=str, default="")
    parser.add_argument("--cache-ttl-seconds", type=int, default=availability_analytics.DEFAULT_CACHE_TTL_SECONDS)
    parser.add_argument("--output", default="availability_analytics_report_bundle/availability_analytics_report.html")
    parser.add_argument("--data-dir", default="availability_analytics_report_bundle/availability_analytics_data")
    parser.add_argument("--data-index", default="availability_analytics_report_bundle/availability_analytics_data_index.json")
    args = parser.parse_args()
    args.courts = parse_int_list(args.courts)
    args.slots = parse_slots(args.slots)
    return args


def main() -> None:
    args = parse_args()
    data_payload = build_data_payload(args)
    html_content = build_html(args)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(html_content, encoding="utf-8")

    data_dir = Path(args.data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)
    generated_at = datetime.now(tz=availability_analytics.LOCAL_TZ)
    snapshot_name = f"availability_analytics_data_{generated_at.strftime('%Y%m%dT%H%M%S')}.json"
    snapshot_path = data_dir / snapshot_name
    data_payload["updated_at"] = generated_at.isoformat()
    snapshot_path.write_text(json.dumps(data_payload, ensure_ascii=False, indent=2), encoding="utf-8")

    index_path = Path(args.data_index)
    index_dir = index_path.parent
    snapshot_js_path = snapshot_path.with_suffix(".js")

    try:
        latest_data_path = str(snapshot_path.relative_to(index_dir))
    except ValueError:
        latest_data_path = str(snapshot_path)

    try:
        latest_data_js_path = str(snapshot_js_path.relative_to(index_dir))
    except ValueError:
        latest_data_js_path = str(snapshot_js_path)

    index_payload = {
        "schema_version": 1,
        "generated_at": generated_at.isoformat(),
        "latest_data_path": latest_data_path,
        "latest_data_js_path": latest_data_js_path,
        "source_signature": data_payload.get("source_signature"),
        "request": data_payload.get("request", {}),
        "cache": data_payload.get("cache", {}),
    }
    snapshot_js_content = "window.__AVAILABILITY_ANALYTICS_DATA__ = " + json.dumps(
        data_payload,
        ensure_ascii=False,
        indent=2,
    ) + ";\n"
    snapshot_js_path.write_text(snapshot_js_content, encoding="utf-8")

    index_path.write_text(json.dumps(index_payload, ensure_ascii=False, indent=2), encoding="utf-8")
    index_js_path = index_path.with_suffix(".js")
    index_js_content = "window.__AVAILABILITY_ANALYTICS_INDEX__ = " + json.dumps(
        index_payload,
        ensure_ascii=False,
        indent=2,
    ) + ";\n"
    index_js_path.write_text(index_js_content, encoding="utf-8")

    print(str(output))
    print(str(snapshot_path))
    print(str(snapshot_js_path))
    print(str(index_js_path))
    print(str(index_path))


if __name__ == "__main__":
    main()
