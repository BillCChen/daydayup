#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
北大医学部体育馆羽毛球场地自动预约脚本 - 完整流程版
包含 canBook 预检步骤
"""

import requests
import json
import time
import threading
import argparse
from datetime import datetime, timedelta

BASE_URL = "http://wechat.sportplayer.cn/easyserpClient"

class CompleteBookingBot:
    def __init__(self, args):
        self.args = args
        self.session = requests.Session()

        self.session.headers.update({
            "Host": "wechat.sportplayer.cn",
            "Connection": "keep-alive",
            "User-Agent": "Mozilla/5.0 (Linux; Android 16; V2366HA Build/BP2A.250605.031.A3; wv) AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 Chrome/142.0.7444.173 Mobile Safari/537.36 XWEB/1420193 MMWEBSDK/20251202 MMWEBID/5120 MicroMessenger/8.0.68.3020(0x280044AC) WeChat/arm64 Weixin NetType/WIFI Language/zh_CN ABI/arm64",
            "Accept": "application/json, text/plain, */*",
            "Content-Type": "application/x-www-form-urlencoded",
            "X-Requested-With": "com.tencent.mm",
            "Referer": f"http://wechat.sportplayer.cn/easyserp/index.html?token={args.token}",
            "Accept-Encoding": "gzip, deflate",
            "Accept-Language": "zh-CN,zh;q=0.9,en-US;q=0.8,en;q=0.7",
            "Cookie": f"JSESSIONID={args.jsessionid}"
        })

        self.target_date = args.date
        self.start_h = int(args.time.split('-')[0])
        self.end_h = int(args.time.split('-')[1])
        self.hours = self.end_h - self.start_h

    def get_places(self):
        """获取场地列表"""
        url = f"{BASE_URL}/datediscount/getPlaceInfoByShortNameDiscount"
        params = {
            "shopNum": "1001",
            "dateymd": self.target_date,
            "shortName": "ymq",
            "token": self.args.token
        }
        try:
            resp = self.session.get(url, params=params, timeout=5)
            data = resp.json()
            if data.get("msg") == "success":
                return data.get("data", {}).get("placeArray", [])
        except Exception as e:
            print(f"[错误] 获取场地失败: {e}")
        return None

    def find_slots(self, places, court):
        """查找连续可用时段（state != 4）"""
        for place in places:
            proj = place.get("projectName", {})
            if proj.get("shortname") != court:
                continue

            slots = place.get("projectInfo", [])
            fullname = proj.get("name")

            available = []
            for slot in slots:
                start = slot.get("starttime", "")[:5]
                state = slot.get("state")
                hour = int(start.split(':')[0])

                if self.start_h <= hour < self.end_h and state != 4:
                    available.append(slot)

            if len(available) == self.hours:
                return available, fullname
            else:
                print(f"[提示] 仅找到 {len(available)}/{self.hours} 小时")
                return None, None
        return None, None

    def check_can_book(self, slots):
        """Step 0: canBook 预检（关键步骤！）"""
        print("[Step 0] 预检可约状态(canBook)...")

        # canBook 只需要简化的 fieldinfo（不包含 name 和 stageTypeShortName）
        # 注意：如果是连续时段，可能需要分别检查每个小时段，或只检查第一个
        canbook_fields = []
        for slot in slots:
            canbook_fields.append({
                "day": self.target_date,
                "startTime": slot["starttime"][:5],  # 大写驼峰
                "endTime": slot["endtime"][:5],
                "placeShortName": self.args.court
                # 注意：没有 name 和 stageTypeShortName！
            })

        try:
            resp = self.session.post(
                f"{BASE_URL}/place/canBook",
                data={
                    "fieldinfo": json.dumps(canbook_fields, ensure_ascii=False),
                    "shopNum": "1001",
                    "token": self.args.token
                },
                timeout=3
            )
            result = resp.json()
            print(f"  响应: {result}")

            if result.get("msg") == "success":
                return True
            else:
                print(f"  [失败] canBook 预检未通过: {result.get('msg')}")
                return False
        except Exception as e:
            print(f"  [错误] canBook 请求异常: {e}")
            return False

    def book(self, slots, fullname):
        """执行预约流程（1-3步）"""
        old_total = sum(s["oldMoney"] for s in slots)
        actual_total = self.hours * 20.0

        # Step 0: canBook 预检（必须！）
        if not self.check_can_book(slots):
            return False, "canBook 预检失败"

        # Step 1: getOfferInfo
        print("[Step 1] 获取优惠...")

        # reservationPlace 需要完整的 fieldinfo（包含 name 和 stageTypeShortName）
        field_info_full = [{
            "day": self.target_date,
            "startTime": slots[0]["starttime"][:5],
            "endTime": slots[-1]["endtime"][:5],
            "placeShortName": self.args.court,
            "name": fullname,  # 注意：reservationPlace 需要 name
            "stageTypeShortName": "ymq"  # 注意：reservationPlace 需要这个
        }]

        try:
            r1 = self.session.post(
                f"{BASE_URL}/common/getOfferInfo",
                data={
                    "token": self.args.token,
                    "payMoney": str(old_total),
                    "shopNum": "1001",
                    "projectType": "3",
                    "projectInfo": json.dumps(field_info_full, ensure_ascii=False)
                },
                timeout=3
            )
            if r1.json().get("msg") != "success":
                return False, f"Step1失败: {r1.json().get('msg')}"
        except Exception as e:
            return False, f"Step1异常: {e}"

        # Step 2: getUseCardInfo
        print("[Step 2] 验证学生卡...")
        try:
            r2 = self.session.post(
                f"{BASE_URL}/common/getUseCardInfo",
                data={
                    "token": self.args.token,
                    "shopNum": "1001",
                    "projectType": "3",
                    "projectInfo": json.dumps(field_info_full, ensure_ascii=False)
                },
                timeout=3
            )
            if r2.json().get("msg") != "success":
                return False, f"Step2失败: {r2.json().get('msg')}"
        except Exception as e:
            return False, f"Step2异常: {e}"

        # Step 3: reservationPlace
        print("[Step 3] 提交订单...")
        try:
            payload = {
                "token": self.args.token,
                "shopNum": "1001",
                "fieldinfo": json.dumps(field_info_full, ensure_ascii=False),
                "oldTotal": str(old_total),
                "cardPayType": "0",
                "type": "羽毛球",
                "offerId": self.args.card_index,
                "offerType": "3",
                "total": f"{actual_total:.2f}",
                "premerother": "",
                "cardIndex": self.args.card_index,
                "masterCardNum": "",
                "zengzhiMoney": "0"
            }

            r3 = self.session.post(
                f"{BASE_URL}/place/reservationPlace",
                data=payload,
                timeout=5
            )
            result = r3.json()

            if result.get("msg") == "success":
                return True, f"预约成功！{fullname} {self.target_date} {slots[0]['starttime'][:5]}-{slots[-1]['endtime'][:5]}"
            else:
                return False, f"预约失败: {result.get('msg')}"

        except Exception as e:
            return False, f"Step3异常: {e}"

    def run(self):
        print("="*60)
        print(f"北大医学部羽毛球场地预约")
        print(f"日期: {self.target_date}")
        print(f"时段: {self.start_h}:00-{self.end_h}:00")
        print(f"场地: {self.args.court}")
        print(f"卡号: {self.args.card_index}")
        print("="*60)

        places = self.get_places()
        if not places:
            print("[错误] 无法获取场地信息")
            return

        slots, fullname = self.find_slots(places, self.args.court)

        if slots:
            success, msg = self.book(slots, fullname)
            print(f"\n[结果] {msg}")
        else:
            print("\n[结果] 未找到可用的连续时段")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="羽毛球场地预约（完整流程）")
    parser.add_argument("-k", "--token", required=True, help="微信Token")
    parser.add_argument("-j", "--jsessionid", required=True, help="JSESSIONID")
    parser.add_argument("-i", "--card-index", required=True, help="会员卡号")
    parser.add_argument("-d", "--date", required=True, help="日期 YYYY-MM-DD")
    parser.add_argument("-t", "--time", required=True, help="时段 如16-18")
    parser.add_argument("-c", "--court", default="ymq8", help="场地 如ymq8")

    args = parser.parse_args()
    bot = CompleteBookingBot(args)
    bot.run()
# python final_book.py -k "$DAYDAYUP_TOKEN" -j "$DAYDAYUP_JSESSIONID" -i "$DAYDAYUP_CARD_INDEX" -d 2026-02-02 -t 16-18 -c ymq8
