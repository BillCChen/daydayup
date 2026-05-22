#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
北大医学部体育馆羽毛球场地自动预约脚本 - 灵活日期版
支持：自动计算/绝对日期/相对天数 三种日期指定方式
"""

import requests
import json
import time
import threading
import argparse
import sys
import re
from datetime import datetime, timedelta
from urllib.parse import urlencode

DEFAULTS = {
    "shop_num": "1001",
    "release_time": "11:58:00",
    "priority_duration": 5,
    "max_retry": 30,
    "base_url": "http://wechat.sportplayer.cn/easyserpClient",
    "default_advance_days": 4  # 默认提前4天（即第5天）
}


class BadmintonBookingBot:
    def __init__(self, args):
        self.args = args
        self.session = requests.Session()

        if not args.token or "xxx" in args.token:
            print("[错误] 请提供有效的 Token (--token)")
            sys.exit(1)
        if not args.jsessionid or "xxx" in args.jsessionid:
            print("[错误] 请提供有效的 JSESSIONID (--jsessionid)")
            sys.exit(1)

        self.session.headers.update({
            "Host": "wechat.sportplayer.cn",
            "Connection": "keep-alive",
            "User-Agent": "Mozilla/5.0 (Linux; Android 16; V2366HA Build/BP2A.250605.031.A3; wv) AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 Chrome/142.0.7444.173 Mobile Safari/537.36 XWEB/1420193 MMWEBSDK/20251202 MMWEBID/5120 MicroMessenger/8.0.68.3020(0x280044AC) WeChat/arm64 Weixin NetType/WIFI Language/zh_CN ABI/arm64",
            "Accept": "application/json, text/plain, */*",
            "X-Requested-With": "com.tencent.mm",
            "Referer": f"http://wechat.sportplayer.cn/easyserp/index.html?token={args.token}",
            "Accept-Encoding": "gzip, deflate",
            "Accept-Language": "zh-CN,zh;q=0.9,en-US;q=0.8,en;q=0.7",
            "Cookie": f"JSESSIONID={args.jsessionid}"
        })

        # 解析目标日期（三种方式优先级：--date > --in-days > 默认4天后）
        self.target_date = self._resolve_target_date()

        self.target_time_ranges = self._parse_time_slots(args.time)
        if not self.target_time_ranges:
            print("[错误] 请至少提供一个有效的时间段（如 18-20）")
            sys.exit(1)

        self.priority_places = [f"ymq{c}" for c in (args.priority or [1, 6, 7, 8, 9])]
        self.backup_places = [f"ymq{c}" for c in (args.backup or [2, 3, 4, 5, 10, 11, 12])]

        self.success = False
        self.lock = threading.Lock()

    def _resolve_target_date(self):
        """解析目标日期，支持多种格式"""
        args = self.args

        # 优先级1：明确指定具体日期
        if args.date:
            # 验证日期格式
            try:
                dt = datetime.strptime(args.date, "%Y-%m-%d")
                print(f"[日期] 使用指定日期: {args.date} ({self._weekday_name(dt)})")
                return args.date
            except ValueError:
                print(f"[错误] 日期格式无效: {args.date}，请使用 YYYY-MM-DD 格式（如 2026-02-05）")
                sys.exit(1)

        # 优先级2：指定N天后
        if args.in_days is not None:
            target = datetime.now() + timedelta(days=args.in_days)
            date_str = target.strftime("%Y-%m-%d")
            print(f"[日期] 使用相对日期: {args.in_days}天后 = {date_str} ({self._weekday_name(target)})")
            return date_str

        # 优先级3：默认4天后（第5天）
        target = datetime.now() + timedelta(days=DEFAULTS['default_advance_days'])
        date_str = target.strftime("%Y-%m-%d")
        print(f"[日期] 使用默认: {DEFAULTS['default_advance_days']}天后 = {date_str} ({self._weekday_name(target)})")
        return date_str

    def _weekday_name(self, dt):
        """返回星期几的中文名称"""
        weekdays = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
        return weekdays[dt.weekday()]

    def _parse_time_slots(self, slots):
        """解析时间简写"""
        parsed = []
        for slot in slots:
            try:
                # 支持多种分隔符：- ~ 到
                slot_clean = re.sub(r'[~到]', '-', slot)
                start, end = slot_clean.split('-')
                start_str = f"{int(start):02d}:00"
                end_str = f"{int(end):02d}:00"

                # 验证时间合理性
                if int(start) < 0 or int(end) > 24 or int(start) >= int(end):
                    print(f"[警告] 时间范围无效 '{slot}'，已跳过")
                    continue

                parsed.append({
                    "display": f"{start_str}-{end_str}",
                    "start": start_str,
                    "end": end_str,
                    "hours": int(end) - int(start)
                })
            except:
                print(f"[警告] 时间格式 '{slot}' 无效，已跳过")
        return parsed

    def check_and_wait(self):
        """智能时间控制"""
        now = datetime.now()

        if self.args.force:
            print("[模式] 强制运行模式 (--force)")
            return

        if now.hour == 11 and 0 <= now.minute < 60:
            target = now.replace(hour=11, minute=58, second=0, microsecond=0)
            if now >= target:
                print("[时间] 已过11:58，立即执行")
                return

            print(f"[等待] 当前 {now.strftime('%H:%M:%S')}，等待至 11:58:00...")
            while datetime.now() < target:
                remaining = (target - datetime.now()).total_seconds()
                if remaining > 10:
                    print(f"\r[倒计时] {int(remaining)} 秒...", end="", flush=True)
                    time.sleep(1)
                else:
                    time.sleep(0.1)
            print("\n[开始] 时间到！")
        else:
            print(f"[时间] 当前 {now.strftime('%H:%M')} 不在11:00-12:00区间，立即执行")

    def get_place_info(self):
        """获取场地信息"""
        url = f"{DEFAULTS['base_url']}/datediscount/getPlaceInfoByShortNameDiscount"
        params = {
            "shopNum": DEFAULTS['shop_num'],
            "dateymd": self.target_date,
            "shortName": "ymq",
            "token": self.args.token
        }
        try:
            resp = self.session.get(url, params=params, timeout=3)
            data = resp.json()
            if data.get("msg") == "success":
                return data.get("data", {}).get("placeArray", [])
        except:
            pass
        return []

    def is_target_slot(self, start, end):
        for t in self.target_time_ranges:
            if start == t["start"] and end == t["end"]:
                return True
        return False

    def book_court(self, place_short, place_full, slot, hours):
        """三步预约流程"""
        money = slot["money"]
        old_money = slot["oldMoney"]
        total = money * hours
        old_total = old_money * hours

        field_info = [{
            "day": self.target_date,
            "startTime": slot["starttime"],
            "endTime": slot["endtime"],
            "placeShortName": place_short,
            "name": place_full,
            "stageTypeShortName": "ymq"
        }]

        # Step 1: getOfferInfo
        try:
            r1 = self.session.post(
                f"{DEFAULTS['base_url']}/common/getOfferInfo",
                data={
                    "token": self.args.token,
                    "payMoney": str(old_total),
                    "shopNum": DEFAULTS['shop_num'],
                    "projectType": "3",
                    "projectInfo": json.dumps(field_info)
                },
                timeout=2
            )
            if r1.json().get("msg") != "success":
                return False
        except:
            return False

        # Step 2: getUseCardInfo
        try:
            r2 = self.session.post(
                f"{DEFAULTS['base_url']}/common/getUseCardInfo",
                data={
                    "token": self.args.token,
                    "shopNum": DEFAULTS['shop_num'],
                    "projectType": "3",
                    "projectInfo": json.dumps(field_info)
                },
                timeout=2
            )
            if r2.json().get("msg") != "success":
                return False
        except:
            return False

        # Step 3: reservationPlace
        try:
            r3 = self.session.post(
                f"{DEFAULTS['base_url']}/place/reservationPlace",
                data={
                    "token": self.args.token,
                    "shopNum": DEFAULTS['shop_num'],
                    "fieldinfo": json.dumps(field_info),
                    "oldTotal": str(old_total),
                    "cardPayType": "0",
                    "type": "羽毛球",
                    "offerId": self.args.card_index,
                    "offerType": "3",
                    "total": str(total),
                    "premerother": "",
                    "cardIndex": self.args.card_index,
                    "masterCardNum": "",
                    "zengzhiMoney": "0"
                },
                timeout=3
            )
            result = r3.json()

            if result.get("msg") == "success":
                with self.lock:
                    if not self.success:
                        self.success = True
                        print(f"\n{'='*60}")
                        print(f"[✓ 预约成功] {place_full}")
                        print(f"[✓] 日期: {self.target_date}")
                        print(f"[✓] 时间: {slot['starttime']}-{slot['endtime']}")
                        print(f"[✓] 费用: ¥{total}")
                        print(f"[✓] 支付卡: {self.args.card_index}")
                        print(f"{'='*60}")
                        return True
        except:
            pass
        return False

    def worker(self, place_short, is_priority=True):
        """工作线程"""
        retry = 0
        max_retry = self.args.priority_duration * 3 if is_priority else 20

        while not self.success and retry < max_retry:
            places = self.get_place_info()
            if not places:
                retry += 1
                time.sleep(0.1)
                continue

            for place in places:
                proj = place.get("projectName", {})
                if proj.get("shortname") != place_short:
                    continue

                for slot in place.get("projectInfo", []):
                    if slot.get("state") != 4:
                        continue

                    if self.is_target_slot(slot["starttime"], slot["endtime"]):
                        hours = int(slot["endtime"].split(':')[0]) - int(slot["starttime"].split(':')[0])
                        tag = "[优先]" if is_priority else "[备选]"
                        print(f"{tag} {place_short} {slot['starttime']}-{slot['endtime']} 尝试预约...")

                        if self.book_court(place_short, proj.get("name"), slot, hours):
                            return

            retry += 1
            time.sleep(0.15)

    def run(self):
        """主流程"""
        print("="*60)
        print("北大医学部体育馆羽毛球场地自动预约")
        print("="*60)
        print(f"目标时段: {[t['display'] for t in self.target_time_ranges]}")
        print(f"优先场地: {self.priority_places}")
        print(f"备选场地: {self.backup_places}")
        print(f"会员卡号: {self.args.card_index}")
        print(f"强制运行: {self.args.force}")
        print("="*60)

        # 时间控制
        self.check_and_wait()
        if  not self.args.force:
            # 等待到开抢时间
            release = datetime.strptime(
                f"{datetime.now().date()} {self.args.wait_time}",
                "%Y-%m-%d %H:%M:%S"
            )
            print(f"[系统] 等待开抢: {self.args.wait_time}...")
            while datetime.now() < release:
                diff = (release - datetime.now()).total_seconds()
                if diff > 5:
                    print(f"\r[倒计时] {int(diff)} 秒...", end="", flush=True)
                    time.sleep(1)
                else:
                    time.sleep(0.001)
        print("\n[开始] 开抢！")

        # 阶段1：优先场地
        print(f"[阶段1] 启动优先场地: {self.priority_places}")
        threads = []
        for place in self.priority_places:
            t = threading.Thread(target=self.worker, args=(place, True))
            t.daemon = True
            t.start()
            threads.append(t)

        start = time.time()
        while time.time() - start < self.args.priority_duration and not self.success:
            time.sleep(0.1)

        # 阶段2：备选场地
        if not self.success:
            print(f"[阶段2] 启动备选场地: {self.backup_places}")
            for place in self.backup_places:
                t = threading.Thread(target=self.worker, args=(place, False))
                t.daemon = True
                t.start()
                threads.append(t)

            for t in threads:
                t.join(timeout=15)
        else:
            for t in threads:
                t.join(timeout=2)

        if self.success:
            print("\n[SUCCESS] 预约完成！请检查微信订单。")
        else:
            print("\n[FAILED] 未能抢到场地。")


def main():
    parser = argparse.ArgumentParser(
        description="北大医学部体育馆羽毛球场地自动预约脚本 - 灵活日期版",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
日期指定方式（三选一，优先级: --date > --in-days > 默认4天后）:
  1. 默认: 自动计算4天后（当前开放规则：每天12:00开放第5天场地）
  2. --date: 指定具体日期（如 2026-02-05）
  3. --in-days: 指定N天后（如 --in-days 3 表示3天后）

使用示例:
  # 1. 默认4天后（周五抢下周一）
  python book_court.py -k TOKEN -j JSESSION -i CARD -t 18-20

  # 2. 指定具体日期（如下周日 2026-02-08）
  python book_court.py -k TOKEN -j JSESSION -i CARD --date 2026-02-08 -t 14-16

  # 3. 指定3天后（如果开放规则改为提前3天预约）
  python book_court.py -k TOKEN -j JSESSION -i CARD --in-days 3 -t 18-20

  # 4. 测试明天是否有票（指定1天后）
  python book_court.py -k TOKEN -j JSESSION -i CARD --in-days 1 -t 9-11 --force

  # 5. 只抢2月14日情人节晚上的1号场
  python book_court.py -k TOKEN -j JSESSION -i CARD -d 2026-02-14 -t 19-21 -p 1 --backup
        """
    )

    # 必要参数
    parser.add_argument("--token", "-k", required=True, help="微信Token（从抓包URL参数获取）")
    parser.add_argument("--jsessionid", "-j", required=True, help="JSESSIONID（从抓包Cookie获取）")
    parser.add_argument("--card-index", "-i", required=True, help="会员卡号（offerId/cardIndex）")

    # 日期指定（三种方式，互斥）
    date_group = parser.add_mutually_exclusive_group()
    date_group.add_argument("--date", "-d", default=None,
                           help="指定具体日期（格式：YYYY-MM-DD，如 2026-02-05）")
    date_group.add_argument("--in-days", "-id", type=int, default=None, metavar="N",
                           help="指定N天后（如 --in-days 3 表示3天后）")

    # 时间与时段
    parser.add_argument("--time", "-t", nargs="+", default=["18-20", "19-21"],
                       help="预约时段（24小时制，如 18-20），可指定多个")

    # 场地优先级
    parser.add_argument("--priority", "-p", nargs="+", type=int, default=[1, 6, 7, 8, 9],
                       help="优先场地编号列表（默认：1 6 7 8 9）")
    parser.add_argument("--backup", "-b", nargs="+", type=int, default=[2, 3, 4, 5, 10, 11, 12],
                       help="备选场地编号列表（默认：2 3 4 5 10 11 12）")

    # 控制选项
    parser.add_argument("--force", "-f", action="store_true",
                       help="强制运行模式（无视11:00-12:00等待逻辑）")
    parser.add_argument("--wait-time", "-w", default="11:58:00",
                       help="开抢等待时间（默认：11:58:00）")
    parser.add_argument("--priority-duration", "-pd", type=int, default=5,
                       help="优先场地独占秒数（默认：5秒）")

    args = parser.parse_args()

    # 处理 backup 为空的情况
    if hasattr(args, 'backup') and args.backup is None:
        args.backup = []

    bot = BadmintonBookingBot(args)
    bot.run()


if __name__ == "__main__":
    main()


# Required credentials can be passed through DAYDAYUP_TOKEN, DAYDAYUP_JSESSIONID, and DAYDAYUP_CARD_INDEX.



# 方式 1：默认（自动计算4天后）
# 适用场景：当前规则（每天12:00开放第5天场地）
# 如果今天是周一，自动抢周五（4天后）的场地
# python daydayup.py -k xxx -j yyy -i zzz -t 18-20


# 指定具体日期
# 抢 2026年2月14日（情人节）晚上的场地
# python daydayup.py -k "$DAYDAYUP_TOKEN" -j "$DAYDAYUP_JSESSIONID" -i "$DAYDAYUP_CARD_INDEX" --date 2026-02-02 -t 16-18 --force

# 抢下周日（具体日期）下午
# python book_court.py -k xxx -j yyy -i zzz -d 2026-02-08 -t 14-16
