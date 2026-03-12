#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
持续扫描连续两小时空闲羽毛球场地，并在成功预约后发送邮件通知。

功能：
1. 在 [range_begin, range_end) 范围内扫描“连续两小时”可预约场地
2. 扫描频率可配置，默认较低
3. 不预约距离当前时间小于 min_gap 小时的场地，避免无法退订
4. 成功后发送邮件通知
5. 专业日志：控制台 + 文件
"""

import argparse
import json
import logging
import os
import smtplib
import sys
import time
import traceback
from collections import Counter
from datetime import datetime, timedelta
from email.header import Header
from email.mime.text import MIMEText
from email.utils import formataddr
from logging.handlers import RotatingFileHandler

import requests
from requests.adapters import HTTPAdapter

BASE_URL = "http://wechat.sportplayer.cn/easyserpClient"


class MonitorBookingBot:
    def __init__(self, args):
        self.args = args
        self.session = requests.Session()

        adapter = HTTPAdapter(pool_connections=10, pool_maxsize=10, max_retries=0)
        self.session.mount("http://", adapter)
        self.session.mount("https://", adapter)

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

        self.priority_list = [f"ymq{i}" for i in (args.priority or [7, 8, 9, 1, 6])]
        self.backup_list = [f"ymq{i}" for i in (args.backup or [2, 3, 4, 5, 10, 11, 12])]
        self.court_order = self.priority_list + self.backup_list

        self.range_begin = datetime.strptime(args.range_begin, "%Y-%m-%d-%H")
        self.range_end = datetime.strptime(args.range_end, "%Y-%m-%d-%H")
        if self.range_end <= self.range_begin:
            raise ValueError("range_end 必须晚于 range_begin")
        if (self.range_end - self.range_begin) < timedelta(hours=2):
            raise ValueError("扫描区间长度至少需要 2 小时")

        self.fail_stats = Counter()
        self._setup_logger()

        self.logger.info("=" * 96)
        self.logger.info("持续扫描预约脚本启动")
        self.logger.info(f"[配置] 扫描区间 = [{self.range_begin:%Y-%m-%d %H:00}, {self.range_end:%Y-%m-%d %H:00})")
        self.logger.info(f"[配置] 最小时间间隔 min_gap = {self.args.min_gap} 小时")
        self.logger.info(f"[配置] 扫描周期 scan_interval = {self.args.scan_interval} 秒")
        self.logger.info(f"[配置] 优先场地 = {self.priority_list}")
        self.logger.info(f"[配置] 备选场地 = {self.backup_list}")
        self.logger.info(f"[配置] 邮件收件人 = {self.args.notify_to}")
        self.logger.info("=" * 96)

    def _setup_logger(self):
        os.makedirs("logs", exist_ok=True)
        log_name = f"monitor_booking_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
        log_path = os.path.join("logs", log_name)

        self.logger = logging.getLogger(f"monitor_booking_{id(self)}")
        self.logger.setLevel(logging.INFO)
        self.logger.propagate = False

        formatter = logging.Formatter(
            "%(asctime)s.%(msecs)03d | %(levelname)-7s | %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S"
        )

        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setFormatter(formatter)

        file_handler = RotatingFileHandler(
            log_path,
            maxBytes=5 * 1024 * 1024,
            backupCount=3,
            encoding="utf-8"
        )
        file_handler.setFormatter(formatter)

        self.logger.handlers.clear()
        self.logger.addHandler(console_handler)
        self.logger.addHandler(file_handler)

        self.log_path = log_path

    def _request(self, method, url, *, params=None, data=None, step="", timeout=(2.0, 3.0)):
        start = time.perf_counter()
        try:
            resp = self.session.request(
                method=method,
                url=url,
                params=params,
                data=data,
                timeout=timeout
            )
            elapsed = time.perf_counter() - start
            preview = resp.text[:800].replace("\n", "\\n").replace("\r", "")
            self.logger.info(f"[HTTP] step={step} method={method} status={resp.status_code} elapsed={elapsed:.3f}s")
            self.logger.info(f"[HTTP] step={step} response_preview={preview}")

            try:
                data_json = resp.json()
            except Exception:
                self.fail_stats[f"{step}_json_decode_error"] += 1
                self.logger.error(f"[HTTP] step={step} JSON 解析失败")
                return None, resp

            return data_json, resp

        except Exception as e:
            self.fail_stats[f"{step}_exception"] += 1
            self.logger.error(f"[HTTP] step={step} exception={repr(e)}")
            self.logger.error(traceback.format_exc())
            return None, None

    def _weekday_name(self, dt_obj):
        names = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
        return names[dt_obj.weekday()]

    def _calculate_price(self, date_str, start_h, end_h):
        dt_obj = datetime.strptime(date_str, "%Y-%m-%d")
        weekday = dt_obj.weekday()

        old_total = 0
        actual_total = 0

        for h in range(start_h, end_h):
            if weekday < 5:
                if 9 <= h < 15:
                    old_total += 80
                    actual_total += 20
                elif 15 <= h < 22:
                    old_total += 120
                    actual_total += 30
                else:
                    raise ValueError(f"工作日开放时间为 9:00-22:00，{h}:00 不在范围内")
            else:
                if 9 <= h < 21:
                    old_total += 120
                    actual_total += 30
                else:
                    raise ValueError(f"周末开放时间为 9:00-21:00，{h}:00 已关门")

        return old_total, actual_total

    def get_places(self, date_str):
        url = f"{BASE_URL}/datediscount/getPlaceInfoByShortNameDiscount"
        params = {
            "shopNum": "1001",
            "dateymd": date_str,
            "shortName": "ymq",
            "token": self.args.token
        }

        data, _ = self._request("GET", url, params=params, step=f"get_places[{date_str}]")
        if not data:
            self.fail_stats["get_places_fail"] += 1
            self.logger.warning(f"[get_places] {date_str} 请求失败或返回空")
            return None

        if data.get("msg") != "success":
            self.fail_stats["get_places_not_success"] += 1
            self.logger.warning(f"[get_places] {date_str} msg={data.get('msg')} full={data}")
            return None

        places = data.get("data", {}).get("placeArray", [])
        self.logger.info(f"[get_places] {date_str} 成功，场地数={len(places)}")
        return places

    def inspect_two_hour_slot(self, places, court_id, date_str, start_h):
        end_h = start_h + 2

        for place in places:
            proj = place.get("projectName", {})
            if proj.get("shortname") != court_id:
                continue

            fullname = proj.get("name", court_id)
            slots = place.get("projectInfo", [])

            target = {}
            state_summary = []

            for slot in slots:
                start_raw = slot.get("starttime", "")
                end_raw = slot.get("endtime", "")
                state = slot.get("state")

                if not start_raw or not end_raw:
                    continue

                start_txt = start_raw[:5]
                end_txt = end_raw[:5]
                try:
                    hour = int(start_txt.split(":")[0])
                except Exception:
                    continue

                if start_h <= hour < end_h:
                    target[hour] = slot
                    state_summary.append(f"{start_txt}-{end_txt}:state={state}")

            ordered_slots = []
            ok = True
            for h in range(start_h, end_h):
                slot = target.get(h)
                if not slot:
                    ok = False
                    break
                if slot.get("state") != 1:
                    ok = False
                    break
                ordered_slots.append(slot)

            return {
                "court_id": court_id,
                "fullname": fullname,
                "date": date_str,
                "start_h": start_h,
                "end_h": end_h,
                "state_summary": state_summary if state_summary else ["slot_missing"],
                "bookable_slots": ordered_slots if ok else []
            }

        return {
            "court_id": court_id,
            "fullname": court_id,
            "date": date_str,
            "start_h": start_h,
            "end_h": end_h,
            "state_summary": ["court_not_found"],
            "bookable_slots": []
        }

    def attempt_book(self, court_info):
        date_str = court_info["date"]
        start_h = court_info["start_h"]
        end_h = court_info["end_h"]
        court_id = court_info["court_id"]
        fullname = court_info["fullname"]
        slots = court_info["bookable_slots"]

        self.logger.info(
            f"[尝试] 日期={date_str} | 时段={start_h}:00-{end_h}:00 | "
            f"场地={fullname}({court_id}) | 状态={court_info['state_summary']}"
        )

        if len(slots) != 2:
            self.fail_stats["two_hour_slot_not_bookable"] += 1
            self.logger.info(f"[跳过] {fullname} 不是完整连续两小时可约")
            return False

        old_total, actual_total = self._calculate_price(date_str, start_h, end_h)

        canbook_fields = [{
            "day": date_str,
            "startTime": slot["starttime"][:5],
            "endTime": slot["endtime"][:5],
            "placeShortName": court_id
        } for slot in slots]

        field_info_full = [{
            "day": date_str,
            "startTime": slots[0]["starttime"][:5],
            "endTime": slots[-1]["endtime"][:5],
            "placeShortName": court_id,
            "name": fullname,
            "stageTypeShortName": "ymq"
        }]

        self.logger.info(
            f"[下单参数] date={date_str} court={court_id} time={start_h}:00-{end_h}:00 "
            f"oldTotal={old_total} total={actual_total:.2f}"
        )

        r0, _ = self._request(
            "POST",
            f"{BASE_URL}/place/canBook",
            data={
                "fieldinfo": json.dumps(canbook_fields, ensure_ascii=False),
                "shopNum": "1001",
                "token": self.args.token
            },
            step="canBook"
        )
        if not r0 or r0.get("msg") != "success":
            self.fail_stats["canBook_fail"] += 1
            self.logger.warning(f"[失败] canBook 未通过：{r0}")
            return False

        r1, _ = self._request(
            "POST",
            f"{BASE_URL}/common/getOfferInfo",
            data={
                "token": self.args.token,
                "payMoney": str(old_total),
                "shopNum": "1001",
                "projectType": "3",
                "projectInfo": json.dumps(field_info_full, ensure_ascii=False)
            },
            step="getOfferInfo"
        )
        if not r1 or r1.get("msg") != "success":
            self.fail_stats["getOfferInfo_fail"] += 1
            self.logger.warning(f"[失败] getOfferInfo 未通过：{r1}")
            return False

        r2, _ = self._request(
            "POST",
            f"{BASE_URL}/common/getUseCardInfo",
            data={
                "token": self.args.token,
                "shopNum": "1001",
                "projectType": "3",
                "projectInfo": json.dumps(field_info_full, ensure_ascii=False)
            },
            step="getUseCardInfo"
        )
        if not r2 or r2.get("msg") != "success":
            self.fail_stats["getUseCardInfo_fail"] += 1
            self.logger.warning(f"[失败] getUseCardInfo 未通过：{r2}")
            return False

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

        r3, _ = self._request(
            "POST",
            f"{BASE_URL}/place/reservationPlace",
            data=payload,
            step="reservationPlace",
            timeout=(2.0, 5.0)
        )
        if not r3:
            self.fail_stats["reservationPlace_fail"] += 1
            self.logger.warning("[失败] reservationPlace 请求失败")
            return False

        if r3.get("msg") == "success":
            self.logger.info(f"[成功] 预约成功：{date_str} {start_h}:00-{end_h}:00 {fullname}({court_id})")
            self.send_success_email(date_str, start_h, end_h, fullname, court_id, actual_total)
            return True

        self.fail_stats["reservationPlace_not_success"] += 1
        self.logger.warning(f"[失败] reservationPlace 未通过：{r3}")
        return False

    def send_success_email(self, date_str, start_h, end_h, fullname, court_id, actual_total):
        try:
            subject = f"羽毛球场地预约成功 | {date_str} {start_h}:00-{end_h}:00"
            body = (
                f"预约成功。\n\n"
                f"日期：{date_str}（{self._weekday_name(datetime.strptime(date_str, '%Y-%m-%d'))}）\n"
                f"时段：{start_h}:00-{end_h}:00\n"
                f"场地：{fullname}（{court_id}）\n"
                f"金额：¥{actual_total:.2f}\n"
                f"扫描区间：[{self.range_begin:%Y-%m-%d %H:00}, {self.range_end:%Y-%m-%d %H:00})\n"
                f"最小预约间隔：{self.args.min_gap} 小时\n"
                f"日志文件：{self.log_path}\n"
            )

            msg = MIMEText(body, "plain", "utf-8")
            msg["Subject"] = Header(subject, "utf-8")
            msg["From"] = formataddr(("羽毛球预约脚本", self.args.smtp_sender))
            msg["To"] = self.args.notify_to

            server = smtplib.SMTP_SSL(self.args.smtp_host, self.args.smtp_port, timeout=10)
            server.login(self.args.smtp_sender, self.args.smtp_password)
            server.sendmail(self.args.smtp_sender, [self.args.notify_to], msg.as_string())
            server.quit()

            self.logger.info(f"[邮件] 成功发送通知到 {self.args.notify_to}")

        except Exception as e:
            self.fail_stats["send_mail_fail"] += 1
            self.logger.error(f"[邮件] 发送失败：{repr(e)}")
            self.logger.error(traceback.format_exc())

    def build_candidates(self):
        """
        生成所有可能的两小时候选起点。
        规则：
        - 候选区间为 [range_begin, range_end)
        - 候选开始时间必须满足 start + 2h <= range_end
        - start 必须晚于 now + min_gap
        """
        now = datetime.now()
        min_start = now + timedelta(hours=self.args.min_gap)

        candidates = []
        cursor = self.range_begin

        while cursor + timedelta(hours=2) <= self.range_end:
            if cursor >= min_start:
                candidates.append(cursor)
            cursor += timedelta(hours=1)

        return candidates

    def run_once(self):
        candidates = self.build_candidates()
        if not candidates:
            self.logger.info("[扫描] 当前没有满足 min_gap 的候选时段")
            return False, True  # success, finished

        self.logger.info(f"[扫描] 当前有效候选起点数 = {len(candidates)}")

        # 按日期分组，减少 get_places 次数
        by_date = {}
        for dt_obj in candidates:
            date_str = dt_obj.strftime("%Y-%m-%d")
            by_date.setdefault(date_str, []).append(dt_obj)

        for date_str in sorted(by_date.keys()):
            places = self.get_places(date_str)
            if not places:
                continue

            for dt_obj in by_date[date_str]:
                start_h = dt_obj.hour
                end_h = start_h + 2
                self.logger.info(f"[候选] {date_str} {start_h}:00-{end_h}:00")

                for court_id in self.court_order:
                    court_info = self.inspect_two_hour_slot(places, court_id, date_str, start_h)
                    if len(court_info["bookable_slots"]) != 2:
                        self.logger.info(
                            f"[跳过] {court_info['fullname']}({court_id}) "
                            f"{date_str} {start_h}:00-{end_h}:00 | {court_info['state_summary']}"
                        )
                        continue

                    success = self.attempt_book(court_info)
                    if success:
                        return True, True

        # 如果扫描窗口已经整体过期，则结束
        latest_possible_start = self.range_end - timedelta(hours=2)
        finished = datetime.now() > latest_possible_start + timedelta(hours=1)
        return False, finished

    def print_summary(self):
        self.logger.info("=" * 96)
        self.logger.info("[汇总] 任务结束")
        self.logger.info(f"[汇总] 日志文件：{self.log_path}")
        self.logger.info(f"[汇总] 失败统计：{dict(self.fail_stats)}")
        self.logger.info("=" * 96)

    def run(self):
        try:
            while True:
                self.logger.info("-" * 96)
                self.logger.info("[扫描] 开始新一轮扫描")
                success, finished = self.run_once()

                if success:
                    self.logger.info("[结束] 已成功预约，任务结束")
                    break

                if finished:
                    self.logger.info("[结束] 扫描区间已无可用候选时段，任务结束")
                    break

                self.logger.info(f"[等待] {self.args.scan_interval} 秒后进入下一轮扫描")
                time.sleep(self.args.scan_interval)

        finally:
            self.print_summary()


def main():
    parser = argparse.ArgumentParser(
        description="持续扫描连续两小时可预约羽毛球场地，并在成功后发送邮件通知",
        epilog="""
示例：
  python monitor_book.py -k TOKEN -j JSESSIONID -i CARDINDEX \\
    --range-begin 2026-03-12-18 --range-end 2026-03-14-22

  python monitor_book.py -k TOKEN -j JSESSIONID -i CARDINDEX \\
    --range-begin 2026-03-12-18 --range-end 2026-03-14-22 \\
    --min-gap 15 --scan-interval 300
        """
    )

    parser.add_argument("-k", "--token", default="oRjsg6asr0-oCgFLVvrunP9NmGOM", help="token")
    parser.add_argument("-j", "--jsessionid", default="63C410E47DB5E8401C58FEEBAFD4E426", help="JSESSIONID")
    parser.add_argument("-i", "--card-index", default="1894101490", help="card index / offer id")

    parser.add_argument("--range-begin", required=True, help="扫描起点，格式 YYYY-MM-DD-HH")
    parser.add_argument("--range-end", required=True, help="扫描终点，格式 YYYY-MM-DD-HH")
    parser.add_argument("--min-gap", type=float, default=15.0, help="距开场最小间隔小时数，默认15")
    parser.add_argument("--scan-interval", type=int, default=300, help="扫描周期（秒），默认300")

    parser.add_argument("-p", "--priority", nargs="+", type=int, default=[7, 8, 9, 1, 6], help="优先场地编号")
    parser.add_argument("--backup", nargs="+", type=int, default=[2, 3, 4, 5, 10, 11, 12], help="备选场地编号")

    # 邮件参数
    parser.add_argument("--smtp-host", default="smtp.qq.com", help="SMTP 服务器，默认 smtp.qq.com")
    parser.add_argument("--smtp-port", type=int, default=465, help="SMTP SSL 端口，默认465")
    parser.add_argument("--smtp-sender", default="1425623506@qq.com", help="发件人邮箱")
    parser.add_argument(
        "--smtp-password",
        default="YOUR_QQ_SMTP_AUTH_CODE",
        help="QQ 邮箱 SMTP 授权码，占位参数，运行时请自行填写"
    )
    parser.add_argument("--notify-to", default="2010307209@stu.pku.edu.cn", help="收件人邮箱")

    args = parser.parse_args()

    try:
        bot = MonitorBookingBot(args)
        bot.run()
    except ValueError as e:
        print(f"[参数错误] {e}")
        sys.exit(1)
    except KeyboardInterrupt:
        print("\n[中断] 用户手动终止")
        sys.exit(130)
    except Exception as e:
        print(f"[严重错误] {e}")
        print(traceback.format_exc())
        sys.exit(1)


if __name__ == "__main__":
    main()
    # python monitor_book2026-03-12.py --range-begin 2026-03-12-18 --range-end 2026-03-15-22 --min-gap 15 --scan-interval 300 --smtp-password dxrwvewalrzcfgce