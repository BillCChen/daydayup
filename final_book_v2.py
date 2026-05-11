#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
北大医学部体育馆羽毛球场地自动预约脚本 - 价格修正版
正确实现：工作日分时段定价(9-15点20元/h，15-22点30元/h)，周末统一30元/h(21点关门)
"""

import requests
import json
import time
import argparse
import sys
from datetime import datetime, timedelta

BASE_URL = "http://wechat.sportplayer.cn/easyserpClient"

class PriceAwareBookingBot:
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

        # 解析日期
        if args.date:
            self.target_date = args.date
        elif args.in_days is not None:
            self.target_date = (datetime.now() + timedelta(days=args.in_days)).strftime("%Y-%m-%d")
        else:
            self.target_date = (datetime.now() + timedelta(days=4)).strftime("%Y-%m-%d")

        self.dt = datetime.strptime(self.target_date, "%Y-%m-%d")
        self.weekday = self.dt.weekday()  # 0-4周一到周五，5-6周末

        # 解析时间
        self.start_h = int(args.time.split('-')[0])
        self.end_h = int(args.time.split('-')[1])
        self.duration = self.end_h - self.start_h

        # 计算价格（根据具体规则）
        self.old_total, self.actual_total = self._calculate_price()

        # 场地队列
        self.priority_list = [f"ymq{i}" for i in (args.priority or [1, 6, 7, 8, 9])]
        self.backup_list = [f"ymq{i}" for i in (args.backup or [2, 3, 4, 5, 10, 11, 12])]

        print(f"[配置] 日期: {self.target_date} ({self._weekday_name()})")
        print(f"[配置] 时段: {self.start_h}:00-{self.end_h}:00 ({self.duration}小时)")
        print(f"[配置] 原价: ¥{self.old_total}, 实付: ¥{self.actual_total:.2f}")
        print(f"[配置] 优先: {self.priority_list}")
        print(f"[配置] 备选: {self.backup_list}")

    def _weekday_name(self):
        names = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
        return names[self.weekday]

    def _calculate_price(self):
        """
        价格规则：
        - 工作日(0-4): 9-15点(80->20), 15-22点(120->30)
        - 周末(5-6): 9-21点(120->30), 21点关门
        """
        old_total = 0
        actual_total = 0

        for h in range(self.start_h, self.end_h):
            if self.weekday < 5:  # 工作日
                if 9 <= h < 15:
                    old_total += 80
                    actual_total += 20
                elif 15 <= h < 22:
                    old_total += 120
                    actual_total += 30
                else:
                    raise ValueError(f"工作日开放时间为9:00-22:00，{h}:00不在范围内")
            else:  # 周末
                if 9 <= h < 21:
                    old_total += 120
                    actual_total += 30
                else:
                    raise ValueError(f"周末开放时间为9:00-21:00，{h}:00已关门")

        return old_total, actual_total

    def wait_until_noon(self):
        """等待到12:00:00.001"""
        if self.args.force:
            print("[模式] 强制模式，立即执行")
            return

        now = datetime.now()
        target = now.replace(hour=12, minute=0, second=0, microsecond=1000)

        if now > target:
            print("[时间] 已过12:00，立即执行")
            return

        print(f"[等待] 当前 {now.strftime('%H:%M:%S')}，等待到 12:00:00.001...")

        while datetime.now() < target:
            diff = (target - datetime.now()).total_seconds()
            if diff > 0.1:
                time.sleep(min(diff - 0.05, 0.5))
            else:
                time.sleep(0.001)
            # 整分钟的时候打印剩余时间
            if datetime.now().second == 0:
                remaining = (target - datetime.now()).total_seconds()
                print(f" {datetime.now().strftime('%H:%M:%S')}: [等待] 剩余 {remaining:.3f} 秒...")

        print(f"[触发] {datetime.now().strftime('%H:%M:%S.%f')[:-3]}")

    def get_places(self):
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

    def find_slots(self, places, court_id):
        for place in places:
            proj = place.get("projectName", {})
            if proj.get("shortname") != court_id:
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

            if len(available) == self.duration:
                return available, fullname

        return None, None

    def attempt_book(self, slots, fullname, court_id):
        print(f"\n[尝试] {fullname} {self.start_h}:00-{self.end_h}:00")

        canbook_fields = [{
            "day": self.target_date,
            "startTime": slot["starttime"][:5],
            "endTime": slot["endtime"][:5],
            "placeShortName": court_id
        } for slot in slots]

        field_info_full = [{
            "day": self.target_date,
            "startTime": slots[0]["starttime"][:5],
            "endTime": slots[-1]["endtime"][:5],
            "placeShortName": court_id,
            "name": fullname,
            "stageTypeShortName": "ymq"
        }]

        # Step 0: canBook
        try:
            r0 = self.session.post(
                f"{BASE_URL}/place/canBook",
                data={
                    "fieldinfo": json.dumps(canbook_fields, ensure_ascii=False),
                    "shopNum": "1001",
                    "token": self.args.token
                },
                timeout=3
            )
            if r0.json().get("msg") != "success":
                print(f"  [×] canBook 未通过")
                return False
        except Exception as e:
            print(f"  [×] canBook 异常: {e}")
            return False

        time.sleep(0.3)

        # Step 1: getOfferInfo
        try:
            r1 = self.session.post(
                f"{BASE_URL}/common/getOfferInfo",
                data={
                    "token": self.args.token,
                    "payMoney": str(self.old_total),
                    "shopNum": "1001",
                    "projectType": "3",
                    "projectInfo": json.dumps(field_info_full, ensure_ascii=False)
                },
                timeout=3
            )
            if r1.json().get("msg") != "success":
                return False
        except:
            return False

        time.sleep(0.3)

        # Step 2: getUseCardInfo
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
                return False
        except:
            return False

        time.sleep(0.3)

        # Step 3: reservationPlace（使用动态计算的价格）
        try:
            payload = {
                "token": self.args.token,
                "shopNum": "1001",
                "fieldinfo": json.dumps(field_info_full, ensure_ascii=False),
                "oldTotal": str(self.old_total),
                "cardPayType": "0",
                "type": "羽毛球",
                "offerId": self.args.card_index,
                "offerType": "3",
                "total": f"{self.actual_total:.2f}",  # 使用动态计算的价格
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
                print(f"[✓] 预约成功！{fullname} {self.target_date} {self.start_h}:00-{self.end_h}:00")
                return True
            else:
                print(f"  [×] {result.get('msg')}")
                return False
        except Exception as e:
            print(f"  [×] 异常: {e}")
            return False

    def run(self):
        try:
            self.wait_until_noon()
        except ValueError as e:
            print(f"[错误] {e}")
            return

        print(f"[开始] 启动预约（每1秒重试，最多{self.args.max_retry}次）...")

        for attempt in range(1, self.args.max_retry + 1):
            print(f"\n[第{attempt}次] {datetime.now().strftime('%H:%M:%S')}")

            places = self.get_places()
            if not places:
                time.sleep(1)
                continue

            # 先尝试优先场地
            success = False
            for court_id in self.priority_list:
                slots, fullname = self.find_slots(places, court_id)
                if slots and self.attempt_book(slots, fullname, court_id):
                    success = True
                    break

            # 再尝试备选
            if not success:
                for court_id in self.backup_list:
                    slots, fullname = self.find_slots(places, court_id)
                    if slots and self.attempt_book(slots, fullname, court_id):
                        success = True
                        break

            if success:
                print("\n[完成] 预约成功！")
                return

            if attempt < self.args.max_retry:
                time.sleep(1)

        print(f"\n[结束] 未达到目标")


def main():
    parser = argparse.ArgumentParser(
        description="羽毛球场地预约 - 价格修正版",
        epilog="""
价格规则示例:
  周一到周五: 9-15点(20元/h), 15-22点(30元/h)
  周六到周日: 9-21点(30元/h), 21点关门

示例:
  # 工作日16-18点(15点后): 60元(30×2)
  python final_book.py -k xxx -j yyy -i zzz -t 16-18

  # 工作日14-16点(跨时段): 50元(20+30)
  python final_book.py -k xxx -j yyy -i zzz -t 14-16

  # 周末任意时段: 30元/h
  python final_book.py -k xxx -j yyy -i zzz -d 2026-02-01 -t 14-16
        """
    )
    parser.add_argument("-k", "--token", required=True)
    parser.add_argument("-j", "--jsessionid", required=True)
    parser.add_argument("-i", "--card-index", required=True)

    date_group = parser.add_mutually_exclusive_group()
    date_group.add_argument("-d", "--date", help="指定日期 YYYY-MM-DD")
    date_group.add_argument("--in-days", type=int, help="N天后")

    parser.add_argument("-t", "--time", required=True, help="时段 如9-11,14-16,16-18")
    parser.add_argument("-p", "--priority", nargs="+", type=int, default=[1, 6, 7, 8, 9])
    parser.add_argument("--backup", nargs="+", type=int, default=[2, 3, 4, 5, 10, 11, 12])
    parser.add_argument("--force", action="store_true", help="立即执行")
    parser.add_argument("--max-retry", type=int, default=60)

    args = parser.parse_args()

    try:
        bot = PriceAwareBookingBot(args)
        bot.run()
    except ValueError as e:
        print(f"[参数错误] {e}")
        sys.exit(1)
    except Exception as e:
        print(f"[严重错误] {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
# # 场景1:默认最新
# 周四抢下周一(默认4天后)晚上18-20点,自动等到12:00
# python final_book_v2.py -k "$DAYDAYUP_TOKEN" -j "$DAYDAYUP_JSESSIONID" -i "$DAYDAYUP_CARD_INDEX" -t 18-20

# # 场景2:定时定点
# 抢明天(1天后)下午14-16点(原价80,实付40)
# python final_book_v2.py -k "$DAYDAYUP_TOKEN" -j "$DAYDAYUP_JSESSIONID" -i "$DAYDAYUP_CARD_INDEX" --in-days 1 -t 14-16

# # 场景3:特殊日期
# 指定2月14日情人节晚上,只抢1号场
# python final_book_v2.py -k "$DAYDAYUP_TOKEN" -j "$DAYDAYUP_JSESSIONID" -i "$DAYDAYUP_CARD_INDEX" -d 2026-02-14 -t 19-21 -p 1 --backup

# # 场景4:测试脚本(立即执行,不等待)
# python final_book_v2.py -k "$DAYDAYUP_TOKEN" -j "$DAYDAYUP_JSESSIONID" -i "$DAYDAYUP_CARD_INDEX" -d 2026-02-05 -t 19-21 --force
# python final_book_v2.py -k "$DAYDAYUP_TOKEN" -j "$DAYDAYUP_JSESSIONID" -i "$DAYDAYUP_CARD_INDEX" -d 2026-02-01 -t 17-19 --force
