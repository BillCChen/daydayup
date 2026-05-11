#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
北大医学部羽毛球场地捡漏监控程序
功能：持续监测目标日期场地，发现连续2小时空闲立即预约，邮件通知
终止条件：目标日期前一日18:00（提前12小时退订截止）
"""

import requests
import json
import time
import argparse
import sys
import smtplib
from email.mime.text import MIMEText
from datetime import datetime, timedelta

BASE_URL = "http://wechat.sportplayer.cn/easyserpClient"

class CourtMonitorBot:
    def __init__(self, args):
        self.args = args
        self.session = requests.Session()
        self.start_time = datetime.now()
        self.check_count = 0
        self.last_report_hour = -1

        # 设置请求头
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

        # 解析目标日期
        if args.date:
            self.target_date = args.date
        elif args.in_days is not None:
            self.target_date = (datetime.now() + timedelta(days=args.in_days)).strftime("%Y-%m-%d")
        else:
            self.target_date = (datetime.now() + timedelta(days=2)).strftime("%Y-%m-%d")  # 默认后天

        self.target_dt = datetime.strptime(self.target_date, "%Y-%m-%d")
        self.weekday = self.target_dt.weekday()

        # 计算终止时间：目标日期前一日18:00
        self.end_time = self.target_dt - timedelta(days=1) + timedelta(hours=18)

        # 场地优先级
        self.priority_list = [f"ymq{i}" for i in (args.priority or [1, 6, 7, 8, 9])]
        self.all_courts = [f"ymq{i}" for i in range(1, 13)]

        # 邮件配置
        self.smtp_server = "smtp.qq.com"
        self.smtp_port = 465
        self.sender = "1425623506@qq.com"
        self.password = "dxrwvewalrzcfgce"
        self.receiver = "2010307209@stu.pku.edu.cn"

        print(f"[监控启动] 目标日期: {self.target_date} ({self._weekday_name()})")
        print(f"[监控启动] 终止时间: {self.end_time.strftime('%Y-%m-%d %H:%M')}")
        print(f"[监控启动] 检查间隔: 10秒")
        print(f"[监控启动] 寻找连续2小时空闲时段...")

        # 发送启动邮件
        self.send_email(
            f"启动监控 {self.target_date} 场地",
            f"开始监控 {self.target_date} 的羽毛球场地捡漏。\n"
            f"终止时间: {self.end_time.strftime('%Y-%m-%d %H:%M')}\n"
            f"优先场地: {self.priority_list}\n"
            f"当前时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        )

    def _weekday_name(self):
        names = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
        return names[self.weekday]

    def calculate_price(self, start_h, duration=2):
        """计算价格（与final_book_v2一致）"""
        old_total = 0
        actual_total = 0

        for h in range(start_h, start_h + duration):
            if self.weekday < 5:  # 工作日
                if 9 <= h < 15:
                    old_total += 80
                    actual_total += 20
                elif 15 <= h < 22:
                    old_total += 120
                    actual_total += 30
            else:  # 周末
                if 9 <= h < 21:
                    old_total += 120
                    actual_total += 30

        return old_total, actual_total

    def send_email(self, subject, body):
        """发送邮件"""
        try:
            msg = MIMEText(body, 'plain', 'utf-8')
            msg['Subject'] = subject
            msg['From'] = self.sender
            msg['To'] = self.receiver

            with smtplib.SMTP_SSL(self.smtp_server, self.smtp_port) as server:
                server.login(self.sender, self.password)
                server.sendmail(self.sender, [self.receiver], msg.as_string())

            print(f"[邮件] 发送成功: {subject}")
        except Exception as e:
            print(f"[邮件] 发送失败: {e}")

    def get_places(self):
        """获取场地信息"""
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

    def find_continuous_2h_slots(self, places):
        """
        查找所有场地的连续2小时空闲时段
        返回列表: [(court_id, court_name, start_h, slots_dict)]
        """
        results = []

        for place in places:
            proj = place.get("projectName", {})
            court_id = proj.get("shortname")
            court_name = proj.get("name")

            slots = place.get("projectInfo", [])
            # 构建小时映射
            slot_map = {}
            for slot in slots:
                h = int(slot["starttime"][:2])
                slot_map[h] = slot

            # 检查所有可能的连续2小时 (9-11, 10-12, ..., 20-22)
            # 工作日最晚20-22，周末最晚19-21（因为21点关门）
            max_start = 20 if self.weekday < 5 else 19

            for start_h in range(9, max_start + 1):
                if start_h in slot_map and (start_h + 1) in slot_map:
                    slot1 = slot_map[start_h]
                    slot2 = slot_map[start_h + 1]

                    # state != 4 表示可约
                    if slot1.get("state") != 4 and slot2.get("state") != 4:
                        old_price, new_price = self.calculate_price(start_h, 2)
                        results.append({
                            "court_id": court_id,
                            "court_name": court_name,
                            "start_h": start_h,
                            "end_h": start_h + 2,
                            "slots": [slot1, slot2],
                            "price_old": old_price,
                            "price_new": new_price
                        })

        return results

    def attempt_book(self, slot_info):
        """尝试预约特定的连续2小时场地"""
        court_id = slot_info["court_id"]
        court_name = slot_info["court_name"]
        slots = slot_info["slots"]

        print(f"\n[预约尝试] {court_name} {slot_info['start_h']}:00-{slot_info['end_h']}:00")

        # canBook 字段（简化版）
        canbook_fields = [{
            "day": self.target_date,
            "startTime": s["starttime"][:5],
            "endTime": s["endtime"][:5],
            "placeShortName": court_id
        } for s in slots]

        # 完整字段
        field_info_full = [{
            "day": self.target_date,
            "startTime": slots[0]["starttime"][:5],
            "endTime": slots[-1]["endtime"][:5],
            "placeShortName": court_id,
            "name": court_name,
            "stageTypeShortName": "ymq"
        }]

        old_total = slot_info["price_old"]
        actual_total = slot_info["price_new"]

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
                    "payMoney": str(old_total),
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

        # Step 3: reservationPlace
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
                return True
            else:
                print(f"  [×] 预约失败: {result.get('msg')}")
                return False
        except Exception as e:
            print(f"  [×] 异常: {e}")
            return False

    def send_hourly_report(self):
        """发送每小时状态汇报"""
        now = datetime.now()
        uptime = now - self.start_time

        # 查找当前所有可用时段（用于汇报）
        places = self.get_places()
        available_list = []
        if places:
            slots = self.find_continuous_2h_slots(places)
            for s in slots[:5]:  # 只显示前5个避免过长
                available_list.append(f"{s['court_name']} {s['start_h']}:00-{s['end_h']}:00")

        body = (f"监控运行中...\n\n"
                f"运行时间: {str(uptime).split('.')[0]}\n"
                f"检查次数: {self.check_count}\n"
                f"目标日期: {self.target_date}\n"
                f"当前可用连续2小时场地数: {len(slots) if places else '获取失败'}\n"
                f"可用时段示例: {', '.join(available_list) if available_list else '无'}\n\n"
                f"下次汇报: {(now + timedelta(hours=1)).strftime('%H:%M')}")

        self.send_email(f"每小时汇报 - {self.target_date} 场地监控", body)
        self.last_report_hour = now.hour

    def run(self):
        """主监控循环"""
        print(f"[运行] 监控中... 按 Ctrl+C 停止")

        while datetime.now() < self.end_time:
            now = datetime.now()
            self.check_count += 1

            # 每小时汇报（检查小时数是否变化）
            if now.hour != self.last_report_hour and self.check_count > 1:
                self.send_hourly_report()

            print(f"\n[第{self.check_count}次扫描] {now.strftime('%H:%M:%S')}")

            # 获取场地信息
            places = self.get_places()
            if not places:
                time.sleep(10)
                continue

            # 查找所有连续2小时空闲时段
            available_slots = self.find_continuous_2h_slots(places)

            if available_slots:
                print(f"[发现] 找到 {len(available_slots)} 个连续2小时空闲时段")

                # 按优先级排序（优先场地在前）
                def sort_key(x):
                    if x["court_id"] in self.priority_list:
                        return self.priority_list.index(x["court_id"])
                    return 100 + self.all_courts.index(x["court_id"])

                available_slots.sort(key=sort_key)

                # 尝试预约
                for slot_info in available_slots:
                    if self.attempt_book(slot_info):
                        # 预约成功，发送邮件并结束程序
                        subject = f"🎉 捡漏成功！{slot_info['court_name']} {slot_info['start_h']}:00-{slot_info['end_h']}:00"
                        body = (f"恭喜！成功预约到场地！\n\n"
                                f"场地: {slot_info['court_name']} ({slot_info['court_id']})\n"
                                f"日期: {self.target_date}\n"
                                f"时间: {slot_info['start_h']}:00 - {slot_info['end_h']}:00\n"
                                f"价格: ¥{slot_info['price_new']:.2f} (原价¥{slot_info['price_old']})\n"
                                f"预约时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"
                                f"共计扫描 {self.check_count} 次，运行时长 {str(datetime.now() - self.start_time).split('.')[0]}")

                        self.send_email(subject, body)
                        print(f"\n[成功] 预约完成！程序结束。")
                        return
            else:
                print("[状态] 暂无连续2小时空闲场地")

            # 等待10秒
            time.sleep(10)

        # 到达终止时间
        print(f"\n[终止] 已达到截止时间 {self.end_time.strftime('%Y-%m-%d %H:%M')}")
        self.send_email(
            f"监控结束 - {self.target_date} 未抢到",
            f"监控已运行至截止时间。\n"
            f"目标日期: {self.target_date}\n"
            f"终止时间: {self.end_time.strftime('%Y-%m-%d %H:%M')}\n"
            f"总检查次数: {self.check_count}\n"
            f"总运行时长: {str(datetime.now() - self.start_time).split('.')[0]}\n\n"
            f"未找到合适的连续2小时空闲场地。"
        )


def main():
    parser = argparse.ArgumentParser(
        description="羽毛球场地捡漏监控程序",
        epilog="""
使用示例:
  # 监控后天（默认）的场地，发现连续2小时空闲立即预约
  python court_monitor.py -k TOKEN -j JSESSION -i CARD

  # 监控指定日期（如2月5日）的场地
  python court_monitor.py -k TOKEN -j JSESSION -i CARD -d 2026-02-05

  # 监控3天后的场地，优先1号场
  python court_monitor.py -k TOKEN -j JSESSION -i CARD --in-days 3 -p 1 6 7
        """
    )
    parser.add_argument("-k", "--token", required=True, help="微信Token")
    parser.add_argument("-j", "--jsessionid", required=True, help="JSESSIONID")
    parser.add_argument("-i", "--card-index", required=True, help="会员卡号")

    date_group = parser.add_mutually_exclusive_group()
    date_group.add_argument("-d", "--date", help="指定日期 YYYY-MM-DD")
    date_group.add_argument("--in-days", type=int, help="N天后 (默认2)")

    parser.add_argument("-p", "--priority", nargs="+", type=int, default=[1, 6, 7, 8, 9],
                       help="优先场地 (默认: 1 6 7 8 9)")

    args = parser.parse_args()

    try:
        monitor = CourtMonitorBot(args)
        monitor.run()
    except KeyboardInterrupt:
        print("\n\n[停止] 用户中断")
        monitor.send_email("监控已停止", "用户手动中断监控程序。")
    except Exception as e:
        print(f"[严重错误] {e}")
        try:
            monitor.send_email("监控异常终止", f"发生错误: {e}")
        except:
            pass
        sys.exit(1)


if __name__ == "__main__":
    main()
# 指定监控日期
# python court_monitor.py -k "$DAYDAYUP_TOKEN" -j "$DAYDAYUP_JSESSIONID" -i "$DAYDAYUP_CARD_INDEX" -d 2026-02-05

# 指定场地
# python court_monitor.py -k "$DAYDAYUP_TOKEN" -j "$DAYDAYUP_JSESSIONID" -i "$DAYDAYUP_CARD_INDEX" -d 2026-02-05 -p 1 6 7

# nohup python court_monitor.py -k "$DAYDAYUP_TOKEN" -j "$DAYDAYUP_JSESSIONID" -i "$DAYDAYUP_CARD_INDEX" -d 2026-02-05 -p 1 6 7 > court_monitor.log 2>&1 &
