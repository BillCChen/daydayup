#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
诊断版：打印原始 state 值，确认 state 含义
"""

import requests
import json
import os
from datetime import datetime

BASE_URL = "http://wechat.sportplayer.cn/easyserpClient"

def diagnose(token, jsessionid, date, court):
    session = requests.Session()
    session.headers.update({
        "Host": "wechat.sportplayer.cn",
        "Connection": "keep-alive",
        "User-Agent": "Mozilla/5.0 (Linux; Android 16; V2366HA Build/BP2A.250605.031.A3; wv) AppleWebKit/537.36",
        "Accept": "application/json, text/plain, */*",
        "X-Requested-With": "com.tencent.mm",
        "Referer": f"http://wechat.sportplayer.cn/easyserp/index.html?token={token}",
        "Accept-Encoding": "gzip, deflate",
        "Accept-Language": "zh-CN,zh;q=0.9",
        "Cookie": f"JSESSIONID={jsessionid}"
    })

    url = f"{BASE_URL}/datediscount/getPlaceInfoByShortNameDiscount"
    params = {
        "shopNum": "1001",
        "dateymd": date,
        "shortName": "ymq",
        "token": token
    }

    resp = session.get(url, params=params, timeout=5)
    data = resp.json()

    if data.get("msg") != "success":
        print(f"API错误: {data.get('msg')}")
        return

    places = data.get("data", {}).get("placeArray", [])

    for place in places:
        proj = place.get("projectName", {})
        if proj.get("shortname") == court:
            print(f"\n场地: {court} ({proj.get('name')})")
            print("原始 state 值对照表:")
            print("-" * 40)

            for slot in place.get("projectInfo", []):
                start = slot.get("starttime", "")[:5]
                end = slot.get("endtime", "")[:5]
                state = slot.get("state")
                money = slot.get("money")

                # 重点：打印原始数值
                print(f"{start}-{end} | state={state} | ¥{money}")

            print("-" * 40)
            print("请观察:")
            print("  - 已被你预约的时段 state 是多少？")
            print("  - 确定空闲的时段 state 是多少？")
            print("  - 被别人预约的时段 state 是多少？")

if __name__ == "__main__":
    TOKEN = os.getenv("DAYDAYUP_TOKEN", "")
    JSESSIONID = os.getenv("DAYDAYUP_JSESSIONID", "")
    DATE = "2026-02-02"
    COURT = "ymq8"

    diagnose(TOKEN, JSESSIONID, DATE, COURT)
