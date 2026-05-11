#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
List card balances.
"""

from __future__ import annotations

import argparse
import json
import sys

from easyserp_client import (
    DEFAULT_CARD_INDEX,
    EasySerpError,
    add_common_args,
    build_client,
    format_amount,
    mask_card,
    print_error,
    require_success,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="List card balances")
    add_common_args(parser)
    parser.add_argument("-i", "--card-index", default="", help="optional card index filter")
    parser.add_argument("--default-card", action="store_true", help="filter by DAYDAYUP_CARD_INDEX")
    parser.add_argument("--show-full-card", action="store_true", help="show full card index")
    parser.add_argument("--json", action="store_true", help="print selected fields as JSON")
    return parser


def selected_fields(card: dict, show_full_card: bool) -> dict:
    card_index = str(card.get("cardindex") or "")
    return {
        "card_index": card_index if show_full_card else mask_card(card_index),
        "card_name": card.get("cardname") or card.get("shortcardname") or "",
        "status": card.get("cardstatus") or "",
        "cash_balance": format_amount(card.get("cardcash")),
        "money": format_amount(card.get("money")),
        "present_money": format_amount(card.get("presentMoney")),
        "end_date": card.get("enddate") or "",
        "times_balance": card.get("cardtime") or card.get("shouChuCiShu") or "",
        "paid_amount": format_amount(card.get("shouChuJinE")),
    }


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    try:
        if args.default_card and not args.card_index:
            args.card_index = DEFAULT_CARD_INDEX
        client = build_client(args)
        cards = require_success(
            client.get(
                "card/getCardByUser",
                params={"shopNum": args.shop_num, "token": args.token},
            ),
            "getCardByUser",
        )
        if not isinstance(cards, list):
            raise EasySerpError("getCardByUser returned a non-list data field")

        if args.card_index:
            cards = [card for card in cards if str(card.get("cardindex") or "") == args.card_index]

        rows = [selected_fields(card, args.show_full_card) for card in cards]
        if args.json:
            print(json.dumps(rows, ensure_ascii=False, indent=2))
            return 0

        if not rows:
            print("未查询到会员卡")
            return 0

        print(f"会员卡数量: {len(rows)}")
        for index, row in enumerate(rows, start=1):
            print(
                f"[{index}] card={row['card_index']} name={row['card_name']} "
                f"status={row['status']} balance={row['cash_balance']} "
                f"end={row['end_date']} times={row['times_balance']} paid={row['paid_amount']}"
            )
        return 0
    except EasySerpError as exc:
        return print_error(exc)


if __name__ == "__main__":
    sys.exit(main())
