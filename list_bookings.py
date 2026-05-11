#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
List current venue bookings.
"""

from __future__ import annotations

import argparse
import json
import sys

from easyserp_client import (
    EasySerpError,
    add_common_args,
    build_client,
    fetch_orders,
    is_cancelled,
    print_error,
    summarize_order,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="List current venue bookings")
    add_common_args(parser)
    parser.add_argument("--page-size", type=int, default=20, help="page size")
    parser.add_argument("--max-pages", type=int, default=5, help="maximum pages to fetch")
    parser.add_argument("--start-time", default="", help="optional order start date filter")
    parser.add_argument("--end-time", default="", help="optional order end date filter")
    parser.add_argument("--all", action="store_true", help="include cancelled bookings")
    parser.add_argument("--json", action="store_true", help="print selected fields as JSON")
    return parser


def selected_fields(order: dict) -> dict:
    summary = summarize_order(order)
    return {
        "bill_num": summary.bill_num,
        "status": summary.status,
        "date": summary.date,
        "time": summary.time_range,
        "court": summary.court,
        "amount": summary.amount,
        "pay_type": summary.pay_type,
        "created_at": summary.created_at,
        "short_name": summary.short_name,
    }


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    try:
        client = build_client(args)
        orders = fetch_orders(
            client,
            token=args.token,
            shop_num=args.shop_num,
            page_size=args.page_size,
            max_pages=args.max_pages,
            start_time=args.start_time,
            end_time=args.end_time,
        )
        if not args.all:
            orders = [order for order in orders if not is_cancelled(order)]

        rows = [selected_fields(order) for order in orders]
        if args.json:
            print(json.dumps(rows, ensure_ascii=False, indent=2))
            return 0

        if not rows:
            print("未查询到当前预约")
            return 0

        print(f"当前预约数: {len(rows)}")
        for index, row in enumerate(rows, start=1):
            print(
                f"[{index}] bill={row['bill_num']} status={row['status']} "
                f"date={row['date']} time={row['time']} court={row['court']} "
                f"amount={row['amount']} pay={row['pay_type']} created={row['created_at']}"
            )
        return 0
    except EasySerpError as exc:
        return print_error(exc)


if __name__ == "__main__":
    sys.exit(main())
