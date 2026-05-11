#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Exchange a WeChat OAuth code for an EasySERP token and verify account login.
"""

from __future__ import annotations

import argparse
import getpass
import sys
from urllib.parse import parse_qs, quote, urlsplit

import requests

from easyserp_client import DEFAULT_BASE_URL, USER_AGENT, EasySerpError, print_error, require_success


DEFAULT_CLUB_MEMBER_CODE = "bdyxbtyg7"
DEFAULT_SHOP_NUM = "1001"
DEFAULT_NAME = "wx"
DEFAULT_REDIRECT_URL = "https://www.147soft.cn/easyserp/index.html"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Exchange a WeChat OAuth code for an EasySERP token"
    )
    parser.add_argument("-u", "--username", default="", help="account username")
    parser.add_argument("-p", "--password", default="", help="account password")
    parser.add_argument("--code", default="", help="code from the WeChat OAuth redirect URL")
    parser.add_argument("--redirect-url", default="", help="full redirect URL containing code")
    parser.add_argument("--print-auth-url", action="store_true", help="print the WeChat OAuth URL")
    parser.add_argument("--club-member-code", default=DEFAULT_CLUB_MEMBER_CODE, help="club member code")
    parser.add_argument("--shop-num", default=DEFAULT_SHOP_NUM, help="shop number")
    parser.add_argument("--name", default=DEFAULT_NAME, help="WeChat config name")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL, help="EasySERP API base URL")
    parser.add_argument("--timeout", type=float, default=10.0, help="request timeout in seconds")
    return parser


class TokenClient:
    def __init__(self, base_url: str, timeout: float):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": USER_AGENT,
                "Accept": "application/json, text/plain, */*",
                "Content-Type": "application/x-www-form-urlencoded",
                "X-Requested-With": "com.tencent.mm",
                "Referer": DEFAULT_REDIRECT_URL,
                "Accept-Language": "zh-CN,zh;q=0.9,en-US;q=0.8,en;q=0.7",
            }
        )

    def get(self, endpoint: str, params: dict[str, str]) -> dict:
        url = f"{self.base_url}/{endpoint.lstrip('/')}"
        try:
            response = self.session.get(url, params=params, timeout=self.timeout)
        except requests.RequestException as exc:
            raise EasySerpError(f"request failed: {exc}") from exc
        if response.status_code >= 400:
            raise EasySerpError(f"request error: HTTP {response.status_code}")
        try:
            payload = response.json()
        except ValueError as exc:
            raise EasySerpError("non-json response") from exc
        if not isinstance(payload, dict):
            raise EasySerpError("unexpected response shape")
        return payload


def extract_code(args: argparse.Namespace) -> str:
    if args.code:
        return args.code.strip()
    if args.redirect_url:
        query = parse_qs(urlsplit(args.redirect_url).query)
        code = query.get("code", [""])[0].strip()
        if code:
            return code
    return input("WeChat redirect code: ").strip()


def read_username(args: argparse.Namespace) -> str:
    username = args.username.strip() or input("Username: ").strip()
    if not username:
        raise EasySerpError("missing username")
    return username


def read_password(args: argparse.Namespace) -> str:
    password = args.password or getpass.getpass("Password: ")
    if not password:
        raise EasySerpError("missing password")
    return password


def fetch_appid(client: TokenClient, club_member_code: str) -> str:
    payload = client.get(
        "wechar/getWXConfigInfo",
        params={"clubMemberCode": club_member_code},
    )
    data = require_success(payload, "getWXConfigInfo")
    if not isinstance(data, dict) or not data.get("appid"):
        raise EasySerpError("missing appid")
    return str(data["appid"])


def build_auth_url(appid: str, redirect_url: str) -> str:
    encoded = quote(redirect_url, safe="")
    return (
        "https://open.weixin.qq.com/connect/oauth2/authorize"
        f"?appid={appid}&redirect_uri={encoded}"
        "&response_type=code&scope=snsapi_userinfo&state=123#wechat_redirect"
    )


def exchange_token(client: TokenClient, code: str, club_member_code: str, name: str) -> str:
    payload = client.get(
        "wechar/member",
        params={"code": code, "clubMemberCode": club_member_code, "name": name},
    )
    token = require_success(payload, "wechar/member")
    if not isinstance(token, str) or not token:
        raise EasySerpError("missing token")
    return token


def save_club_info(client: TokenClient, token: str, club_member_code: str, shop_num: str) -> None:
    payload = client.get(
        "wechar/saveClubInfoByToken",
        params={"token": token, "clubMemberCode": club_member_code, "shopNum": shop_num},
    )
    require_success(payload, "saveClubInfoByToken")


def verify_login(client: TokenClient, username: str, password: str, token: str) -> None:
    payload = client.get(
        "memberLogin/logined",
        params={"userName": username, "passWord": password, "token": token},
    )
    require_success(payload, "memberLogin/logined")


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    try:
        client = TokenClient(args.base_url, args.timeout)
        if args.print_auth_url:
            appid = fetch_appid(client, args.club_member_code)
            print(build_auth_url(appid, DEFAULT_REDIRECT_URL), file=sys.stderr)

        username = read_username(args)
        password = read_password(args)
        code = extract_code(args)
        if not code:
            raise EasySerpError("missing WeChat OAuth code")

        token = exchange_token(client, code, args.club_member_code, args.name)
        save_club_info(client, token, args.club_member_code, args.shop_num)
        verify_login(client, username, password, token)
        print(token)
        return 0
    except EasySerpError as exc:
        return print_error(exc)


if __name__ == "__main__":
    sys.exit(main())
