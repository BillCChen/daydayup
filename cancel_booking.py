#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Cancel a venue booking by bill number.
"""

from __future__ import annotations

import argparse
import sys
import time
from typing import Any

from easyserp_client import (
    DEFAULT_SHORT_NAME,
    EasySerpError,
    add_common_args,
    build_client,
    fetch_orders,
    find_order,
    format_amount,
    is_cancelled,
    print_error,
    require_success,
    summarize_order,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Cancel a venue booking")
    add_common_args(parser)
    parser.add_argument("--bill-num", required=True, help="booking bill number from list_bookings.py")
    parser.add_argument("--reason", default="天气原因", help="cancel reason")
    parser.add_argument("--affiliate-card", default="", help="affiliate card value, normally empty")
    parser.add_argument("--short-name", default=DEFAULT_SHORT_NAME, help="fallback venue short name")
    parser.add_argument("--page-size", type=int, default=20, help="page size for verification")
    parser.add_argument("--max-pages", type=int, default=5, help="maximum pages for verification")
    parser.add_argument("--verify-delay", type=float, default=1.0, help="seconds before post-cancel verification")
    parser.add_argument("--yes", action="store_true", help="skip interactive confirmation")
    parser.add_argument("--dry-run", action="store_true", help="show refund preview without cancelling")
    parser.add_argument("--force", action="store_true", help="allow cancelling a bill not found in recent orders")
    return parser


def preview_refund(client, args, order: dict[str, Any] | None) -> None:
    short_name = args.short_name
    if order:
        summary = summarize_order(order)
        short_name = summary.short_name or short_name

    if short_name:
        refund_rule = client.get(
            "common/getRefundTime",
            params={
                "shortName": short_name,
                "shopNum": args.shop_num,
                "token": args.token,
                "type": "place",
            },
        )
        if refund_rule.get("msg") == "success" and refund_rule.get("data"):
            first_rule = refund_rule["data"][0]
            percentage = first_rule.get("refundPercentage")
            cancel_time = first_rule.get("canceltime")
            print(f"退款规则: refundPercentage={percentage}% cancelTime={cancel_time}")

    refund_money = require_success(
        client.get(
            "place/getCanclePlaceMoney",
            params={"billNum": args.bill_num, "token": args.token},
        ),
        "getCanclePlaceMoney",
    )
    if isinstance(refund_money, dict):
        print(
            "退款预览: "
            f"pay={format_amount(refund_money.get('payMoney'))} "
            f"place={format_amount(refund_money.get('placeMoney'))} "
            f"refund={format_amount(refund_money.get('reFundMoney'))} "
            f"extra={format_amount(refund_money.get('zengzhiMoney'))}"
        )


def print_order(order: dict[str, Any]) -> None:
    summary = summarize_order(order)
    print(
        "目标预约: "
        f"bill={summary.bill_num} status={summary.status} date={summary.date} "
        f"time={summary.time_range} court={summary.court} amount={summary.amount}"
    )


def confirm(args) -> bool:
    if args.yes:
        return True
    expected = "CANCEL"
    value = input(f"输入 {expected} 确认取消 bill={args.bill_num}: ").strip()
    return value == expected


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
        )
        order = find_order(orders, args.bill_num)
        if order:
            print_order(order)
            if is_cancelled(order):
                print("该预约已是取消状态")
                return 0
        elif not args.force:
            raise EasySerpError("bill number was not found in recent orders; pass --force to continue")
        else:
            print("未在近期订单中找到该 bill，按 --force 继续")

        preview_refund(client, args, order)
        if args.dry_run:
            print("dry-run: 未调用取消接口")
            return 0
        if not confirm(args):
            print("已放弃取消")
            return 1

        response = client.post(
            "place/canclePlaceAppointment",
            data={
                "outtradeno": args.bill_num,
                "token": args.token,
                "reason": args.reason,
                "affiliateCard": args.affiliate_card,
            },
        )
        print(f"取消接口返回: msg={response.get('msg')} data={response.get('data')}")

        time.sleep(max(args.verify_delay, 0))
        verified_orders = fetch_orders(
            client,
            token=args.token,
            shop_num=args.shop_num,
            page_size=args.page_size,
            max_pages=args.max_pages,
        )
        verified_order = find_order(verified_orders, args.bill_num)
        if verified_order and is_cancelled(verified_order):
            print("取消已确认: 订单状态为取消")
            return 0
        if verified_order is None:
            print("取消可能已生效: 复查列表未找到该订单")
            return 0
        print(f"取消未确认: 当前状态={summarize_order(verified_order).status}")
        return 2
    except EasySerpError as exc:
        return print_error(exc)


if __name__ == "__main__":
    sys.exit(main())
