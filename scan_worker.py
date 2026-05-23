#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from easyserp_client import DEFAULT_BASE_URL, DEFAULT_JSESSIONID, DEFAULT_SHOP_NUM, DEFAULT_TOKEN
from web_console import ServerConfig, UserStore, USERS_PATH, WebConsole


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the Daydayup scan worker.")
    parser.add_argument("-k", "--token", default=DEFAULT_TOKEN, help="wechat token")
    parser.add_argument("-j", "--jsessionid", default=DEFAULT_JSESSIONID, help="JSESSIONID")
    parser.add_argument("--shop-num", default=DEFAULT_SHOP_NUM, help="shop number")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL, help="EasySERP API base URL")
    parser.add_argument("--timeout", type=float, default=10.0, help="request timeout in seconds")
    parser.add_argument("--users-csv", default=str(USERS_PATH), help="local users CSV path")
    parser.add_argument("--interval-seconds", type=float, default=30.0, help="seconds between scan cycles")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    config = ServerConfig(
        shop_num=args.shop_num,
        base_url=args.base_url,
        timeout=args.timeout,
    )
    users = UserStore(Path(args.users_csv), default_token=args.token, default_jsessionid=args.jsessionid)
    app = WebConsole(config, users, start_scan_worker=False)
    print(f"Daydayup scan worker running with interval={args.interval_seconds:.1f}s", flush=True)
    print(f"Users CSV={users.path}", flush=True)
    print(f"Enabled users={len(users.enabled_users())}", flush=True)
    try:
        app.scans.run_forever(args.interval_seconds)
    except KeyboardInterrupt:
        print("Stopped", flush=True)
        return 130
    finally:
        app.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
