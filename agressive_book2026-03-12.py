#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
北大医学部体育馆羽毛球场地自动预约脚本 - 更激进的抢场版

特性：
1. 仅 state == 1 视为可约
2. 按 priority + backup 顺序逐场尝试，失败即切下一个场地
3. 默认 12 个场地 * 3 轮 = 36 次尝试
4. 更激进的等待、请求、刷新策略
5. 控制台 + 文件双日志，便于复盘
"""

import requests
import json
import time
import argparse
import sys
import os
import traceback
import logging
from logging.handlers import RotatingFileHandler
from collections import Counter
from datetime import datetime, timedelta
from requests.adapters import HTTPAdapter

BASE_URL = "http://wechat.sportplayer.cn/easyserpClient"


class AggressiveBookingBot:
    def __init__(self, args):
        self.args = args
        self.session = requests.Session()

        # 连接池调大一点，避免频繁建连
        adapter = HTTPAdapter(pool_connections=20, pool_maxsize=20, max_retries=0)
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

        if args.date:
            self.target_date = args.date
        elif args.in_days is not None:
            self.target_date = (datetime.now() + timedelta(days=args.in_days)).strftime("%Y-%m-%d")
        else:
            self.target_date = (datetime.now() + timedelta(days=4)).strftime("%Y-%m-%d")

        self.dt = datetime.strptime(self.target_date, "%Y-%m-%d")
        self.weekday = self.dt.weekday()

        self.start_h = int(args.time.split("-")[0])
        self.end_h = int(args.time.split("-")[1])
        self.duration = self.end_h - self.start_h
        if self.duration <= 0:
            raise ValueError("时间段非法：结束时间必须大于开始时间")

        self.old_total, self.actual_total = self._calculate_price()

        self.priority_list = [f"ymq{i}" for i in (args.priority or [7, 8, 9, 1, 6])]
        self.backup_list = [f"ymq{i}" for i in (args.backup or [2, 3, 4, 5, 10, 11, 12])]
        self.court_order = self.priority_list + self.backup_list

        self.rounds = args.rounds
        self.max_attempt = len(self.court_order) * self.rounds

        self.fail_stats = Counter()
        self.run_start_perf = None
        self.first_get_places_sent_perf = None
        self.first_reservation_sent_perf = None

        self._setup_logger()

        self.logger.info("=" * 96)
        self.logger.info("羽毛球场地预约脚本启动（更激进的抢场版）")
        self.logger.info(
            f"[配置] 日期={self.target_date} ({self._weekday_name()}) | "
            f"时段={self.start_h}:00-{self.end_h}:00 | 时长={self.duration}小时"
        )
        self.logger.info(
            f"[配置] 原价=¥{self.old_total} | 实付=¥{self.actual_total:.2f} | "
            f"rounds={self.rounds} | max_attempt={self.max_attempt}"
        )
        self.logger.info(
            f"[配置] refresh_every={self.args.refresh_every} | "
            f"connect_timeout={self.args.connect_timeout}s | read_timeout={self.args.read_timeout}s | "
            f"step_sleep={self.args.step_sleep}s | round_sleep={self.args.round_sleep}s"
        )
        self.logger.info(f"[配置] 优先场地={self.priority_list}")
        self.logger.info(f"[配置] 备选场地={self.backup_list}")
        self.logger.info("=" * 96)

    def _setup_logger(self):
        os.makedirs("logs", exist_ok=True)

        log_name = (
            f"aggressive_booking_{self.target_date}_{self.start_h}-{self.end_h}_"
            f"{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
        )
        log_path = os.path.join("logs", log_name)

        self.logger = logging.getLogger(f"aggressive_booking_bot_{id(self)}")
        self.logger.setLevel(logging.INFO)
        self.logger.propagate = False

        formatter = logging.Formatter(
            "%(asctime)s.%(msecs)03d | %(levelname)-7s | %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S"
        )

        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setLevel(logging.INFO)
        console_handler.setFormatter(formatter)

        file_handler = RotatingFileHandler(
            log_path,
            maxBytes=5 * 1024 * 1024,
            backupCount=3,
            encoding="utf-8"
        )
        file_handler.setLevel(logging.INFO)
        file_handler.setFormatter(formatter)

        self.logger.handlers.clear()
        self.logger.addHandler(console_handler)
        self.logger.addHandler(file_handler)

        self.log_path = log_path

    def _weekday_name(self):
        names = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
        return names[self.weekday]

    def _calculate_price(self):
        old_total = 0
        actual_total = 0

        for h in range(self.start_h, self.end_h):
            if self.weekday < 5:
                if 9 <= h < 15:
                    old_total += 80
                    actual_total += 20
                elif 15 <= h < 22:
                    old_total += 120
                    actual_total += 30
                else:
                    raise ValueError(f"工作日开放时间为9:00-22:00，{h}:00不在范围内")
            else:
                if 9 <= h < 21:
                    old_total += 120
                    actual_total += 30
                else:
                    raise ValueError(f"周末开放时间为9:00-21:00，{h}:00已关门")

        return old_total, actual_total

    def _request(self, method, url, *, params=None, data=None, step="", timeout=None):
        start = time.perf_counter()
        if timeout is None:
            timeout = (self.args.connect_timeout, self.args.read_timeout)

        try:
            if step == "get_places" and self.first_get_places_sent_perf is None and self.run_start_perf is not None:
                self.first_get_places_sent_perf = time.perf_counter()

            if step == "reservationPlace" and self.first_reservation_sent_perf is None and self.run_start_perf is not None:
                self.first_reservation_sent_perf = time.perf_counter()

            resp = self.session.request(
                method=method,
                url=url,
                params=params,
                data=data,
                timeout=timeout
            )
            elapsed = time.perf_counter() - start

            text_preview = resp.text[:800].replace("\n", "\\n").replace("\r", "")
            self.logger.info(
                f"[HTTP] step={step} method={method} status={resp.status_code} elapsed={elapsed:.3f}s"
            )
            self.logger.info(f"[HTTP] step={step} response_preview={text_preview}")

            try:
                j = resp.json()
            except Exception:
                self.fail_stats[f"{step}_json_decode_error"] += 1
                self.logger.error(f"[HTTP] step={step} JSON解析失败")
                return None, resp, elapsed

            return j, resp, elapsed

        except Exception as e:
            elapsed = time.perf_counter() - start
            self.fail_stats[f"{step}_exception"] += 1
            self.logger.error(
                f"[HTTP] step={step} method={method} exception={repr(e)} elapsed={elapsed:.3f}s"
            )
            self.logger.error(traceback.format_exc())
            return None, None, elapsed

    def wait_until_noon(self):
        if self.args.force:
            self.logger.info("[模式] force 模式，立即执行")
            return

        now = datetime.now()
        target = now.replace(hour=12, minute=0, second=0, microsecond=1000)

        if now > target:
            self.logger.info("[时间] 当前已过 12:00，立即执行")
            return

        self.logger.info(f"[等待] 当前时间 {now.strftime('%H:%M:%S.%f')[:-3]}，等待到 12:00:00.001")

        last_print_second = None

        while True:
            now = datetime.now()
            if now >= target:
                break

            diff = (target - now).total_seconds()

            if diff > 3:
                time.sleep(0.5)
            elif diff > 1:
                time.sleep(0.1)
            elif diff > 0.2:
                time.sleep(0.01)
            elif diff > 0.03:
                time.sleep(0.001)
            else:
                # 尾段极短 busy wait，抢更准一点
                pass

            now2 = datetime.now()
            if now2.second != last_print_second and now2.microsecond < 50000:
                last_print_second = now2.second
                remaining = max((target - now2).total_seconds(), 0)
                self.logger.info(f"[等待] 距离触发还有 {remaining:.3f}s")

        self.logger.info(f"[触发] 实际启动时间 {datetime.now().strftime('%H:%M:%S.%f')[:-3]}")

    def warmup(self):
        """
        轻量预热：
        - 只做一次 get_places 预热
        - 目的是建立连接、DNS/TCP/会话预热
        - 不保证一定更快，但通常能减少首包冷启动
        """
        if not self.args.warmup:
            return

        self.logger.info("[预热] 开始轻量连接预热")
        url = f"{BASE_URL}/datediscount/getPlaceInfoByShortNameDiscount"
        params = {
            "shopNum": "1001",
            "dateymd": self.target_date,
            "shortName": "ymq",
            "token": self.args.token
        }

        data, _, elapsed = self._request(
            "GET",
            url,
            params=params,
            step="warmup_get_places",
            timeout=(self.args.connect_timeout, self.args.read_timeout)
        )

        if data:
            self.logger.info(f"[预热] 完成，耗时={elapsed:.3f}s")
        else:
            self.logger.warning("[预热] 失败，但不影响正式流程")

    def get_places(self):
        url = f"{BASE_URL}/datediscount/getPlaceInfoByShortNameDiscount"
        params = {
            "shopNum": "1001",
            "dateymd": self.target_date,
            "shortName": "ymq",
            "token": self.args.token
        }

        data, _, elapsed = self._request("GET", url, params=params, step="get_places")
        if not data:
            self.fail_stats["get_places_fail"] += 1
            self.logger.warning("[get_places] 请求失败或返回空")
            return None

        msg = data.get("msg")
        if msg != "success":
            self.fail_stats["get_places_msg_not_success"] += 1
            self.logger.warning(f"[get_places] msg={msg} full={data}")
            return None

        places = data.get("data", {}).get("placeArray", [])
        self.logger.info(f"[get_places] 成功，返回场地数={len(places)}，耗时={elapsed:.3f}s")

        if not places:
            self.fail_stats["get_places_empty"] += 1

        return places

    def inspect_court_slots(self, places, court_id):
        for place in places:
            proj = place.get("projectName", {})
            if proj.get("shortname") != court_id:
                continue

            slots = place.get("projectInfo", [])
            fullname = proj.get("name", court_id)

            target_slots = []
            bookable_slots = []
            state_summary = []

            for slot in slots:
                start_raw = slot.get("starttime", "")
                end_raw = slot.get("endtime", "")
                state = slot.get("state")

                if not start_raw or not end_raw:
                    continue

                start = start_raw[:5]
                end = end_raw[:5]

                try:
                    hour = int(start.split(":")[0])
                except Exception:
                    continue

                if self.start_h <= hour < self.end_h:
                    target_slots.append(slot)
                    state_summary.append(f"{start}-{end}:state={state}")
                    if state == 1:
                        bookable_slots.append(slot)

            return {
                "court_id": court_id,
                "fullname": fullname,
                "target_slots": target_slots,
                "bookable_slots": sorted(bookable_slots, key=lambda x: x.get("starttime", "")),
                "state_summary": state_summary
            }

        return {
            "court_id": court_id,
            "fullname": court_id,
            "target_slots": [],
            "bookable_slots": [],
            "state_summary": ["court_not_found"]
        }

    def attempt_book(self, court_info, global_attempt, round_index, court_seq):
        court_id = court_info["court_id"]
        fullname = court_info["fullname"]
        target_slots = court_info["target_slots"]
        bookable_slots = court_info["bookable_slots"]

        self.logger.info(
            f"[尝试] 全局序号={global_attempt}/{self.max_attempt} | "
            f"轮次={round_index}/{self.rounds} | 场地序号={court_seq}/{len(self.court_order)} | "
            f"场地={fullname}({court_id})"
        )
        self.logger.info(f"[场地状态] {fullname} -> {court_info['state_summary']}")

        if len(target_slots) < self.duration:
            self.fail_stats["slot_missing"] += 1
            self.logger.warning(
                f"[跳过] {fullname} 目标时段slot不完整：需要={self.duration} 实际返回={len(target_slots)}"
            )
            return False

        if len(bookable_slots) != self.duration:
            self.fail_stats["slot_not_fully_bookable"] += 1
            self.logger.warning(
                f"[跳过] {fullname} 可预约slot不足：需要={self.duration} 可约={len(bookable_slots)}"
            )
            return False

        canbook_fields = [{
            "day": self.target_date,
            "startTime": slot["starttime"][:5],
            "endTime": slot["endtime"][:5],
            "placeShortName": court_id
        } for slot in bookable_slots]

        field_info_full = [{
            "day": self.target_date,
            "startTime": bookable_slots[0]["starttime"][:5],
            "endTime": bookable_slots[-1]["endtime"][:5],
            "placeShortName": court_id,
            "name": fullname,
            "stageTypeShortName": "ymq"
        }]

        self.logger.info(
            f"[下单参数] court={court_id} | fieldinfo={field_info_full} | "
            f"oldTotal={self.old_total} | total={self.actual_total:.2f}"
        )

        # Step 0: canBook
        r0, _, _ = self._request(
            "POST",
            f"{BASE_URL}/place/canBook",
            data={
                "fieldinfo": json.dumps(canbook_fields, ensure_ascii=False),
                "shopNum": "1001",
                "token": self.args.token
            },
            step="canBook"
        )
        if not r0:
            self.fail_stats["canBook_fail"] += 1
            self.logger.warning(f"[失败] {fullname} canBook 请求失败")
            return False
        if r0.get("msg") != "success":
            self.fail_stats["canBook_not_success"] += 1
            self.logger.warning(f"[失败] {fullname} canBook未通过：{r0}")
            return False

        if self.args.step_sleep > 0:
            time.sleep(self.args.step_sleep)

        # Step 1: getOfferInfo
        r1, _, _ = self._request(
            "POST",
            f"{BASE_URL}/common/getOfferInfo",
            data={
                "token": self.args.token,
                "payMoney": str(self.old_total),
                "shopNum": "1001",
                "projectType": "3",
                "projectInfo": json.dumps(field_info_full, ensure_ascii=False)
            },
            step="getOfferInfo"
        )
        if not r1:
            self.fail_stats["getOfferInfo_fail"] += 1
            self.logger.warning(f"[失败] {fullname} getOfferInfo 请求失败")
            return False
        if r1.get("msg") != "success":
            self.fail_stats["getOfferInfo_not_success"] += 1
            self.logger.warning(f"[失败] {fullname} getOfferInfo失败：{r1}")
            return False

        if self.args.step_sleep > 0:
            time.sleep(self.args.step_sleep)

        # Step 2: getUseCardInfo
        r2, _, _ = self._request(
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
        if not r2:
            self.fail_stats["getUseCardInfo_fail"] += 1
            self.logger.warning(f"[失败] {fullname} getUseCardInfo 请求失败")
            return False
        if r2.get("msg") != "success":
            self.fail_stats["getUseCardInfo_not_success"] += 1
            self.logger.warning(f"[失败] {fullname} getUseCardInfo失败：{r2}")
            return False

        if self.args.step_sleep > 0:
            time.sleep(self.args.step_sleep)

        # Step 3: reservationPlace
        payload = {
            "token": self.args.token,
            "shopNum": "1001",
            "fieldinfo": json.dumps(field_info_full, ensure_ascii=False),
            "oldTotal": str(self.old_total),
            "cardPayType": "0",
            "type": "羽毛球",
            "offerId": self.args.card_index,
            "offerType": "3",
            "total": f"{self.actual_total:.2f}",
            "premerother": "",
            "cardIndex": self.args.card_index,
            "masterCardNum": "",
            "zengzhiMoney": "0"
        }

        r3, _, _ = self._request(
            "POST",
            f"{BASE_URL}/place/reservationPlace",
            data=payload,
            step="reservationPlace",
            timeout=(self.args.connect_timeout, max(self.args.read_timeout, 4.0))
        )

        if not r3:
            self.fail_stats["reservationPlace_fail"] += 1
            self.logger.warning(f"[失败] {fullname} reservationPlace 请求失败")
            return False

        if r3.get("msg") == "success":
            run_elapsed = time.perf_counter() - self.run_start_perf if self.run_start_perf else -1
            self.logger.info(
                f"[成功] 预约成功 | 场地={fullname}({court_id}) | 日期={self.target_date} | "
                f"时段={self.start_h}:00-{self.end_h}:00 | 启动后耗时={run_elapsed:.3f}s"
            )
            return True

        self.fail_stats["reservationPlace_not_success"] += 1
        self.logger.warning(f"[失败] {fullname} reservationPlace失败：{r3}")
        return False

    def print_summary(self):
        self.logger.info("=" * 96)
        self.logger.info("[汇总] 本次预约任务结束")
        self.logger.info(f"[汇总] 日志文件：{self.log_path}")

        if self.run_start_perf is not None and self.first_get_places_sent_perf is not None:
            self.logger.info(
                f"[汇总] 启动后首个 get_places 发起延迟："
                f"{self.first_get_places_sent_perf - self.run_start_perf:.6f}s"
            )

        if self.run_start_perf is not None and self.first_reservation_sent_perf is not None:
            self.logger.info(
                f"[汇总] 启动后首个 reservationPlace 发起延迟："
                f"{self.first_reservation_sent_perf - self.run_start_perf:.6f}s"
            )

        if self.fail_stats:
            self.logger.info(f"[汇总] 失败统计：{dict(self.fail_stats)}")
        else:
            self.logger.info("[汇总] 无失败统计")

        self.logger.info("=" * 96)

    def run(self):
        # 可选预热：建议在真正开抢前执行一次
        if not self.args.force and self.args.warmup:
            # 提前一点预热，避免太早离正式开抢太久
            now = datetime.now()
            target = now.replace(hour=12, minute=0, second=0, microsecond=1000)
            if now < target:
                diff = (target - now).total_seconds()
                if diff > self.args.warmup_ahead:
                    sleep_s = diff - self.args.warmup_ahead
                    self.logger.info(f"[预热] 将在 {self.args.warmup_ahead:.1f}s 前执行预热，先等待 {sleep_s:.3f}s")
                    time.sleep(max(sleep_s, 0))
                self.warmup()

        try:
            self.wait_until_noon()
        except ValueError as e:
            self.logger.error(f"[错误] {e}")
            return

        self.run_start_perf = time.perf_counter()
        self.logger.info(
            f"[开始] 启动抢场流程：共 {self.rounds} 轮，每轮 {len(self.court_order)} 个场地，总尝试上限 {self.max_attempt}"
        )

        global_attempt = 0

        for round_index in range(1, self.rounds + 1):
            self.logger.info("-" * 96)
            self.logger.info(f"[轮次] 开始第 {round_index}/{self.rounds} 轮")

            places = None

            for court_seq, court_id in enumerate(self.court_order, start=1):
                global_attempt += 1

                # 更激进的刷新策略：
                # 1. 轮次开始时一定刷新
                # 2. 每 refresh_every 个场地重新刷新一次
                if places is None or (court_seq - 1) % self.args.refresh_every == 0:
                    self.logger.info(
                        f"[刷新] round={round_index} court_seq={court_seq} 重新拉取场地快照"
                    )
                    places = self.get_places()
                    if not places:
                        self.logger.warning("[刷新] 本次场地快照获取失败，直接进入下一个尝试位")
                        self.fail_stats["refresh_places_fail"] += 1
                        continue

                court_info = self.inspect_court_slots(places, court_id)

                success = self.attempt_book(
                    court_info=court_info,
                    global_attempt=global_attempt,
                    round_index=round_index,
                    court_seq=court_seq
                )

                if success:
                    self.print_summary()
                    return

            if round_index < self.rounds and self.args.round_sleep > 0:
                time.sleep(self.args.round_sleep)

        self.logger.warning("[结束] 未达到目标，预约失败")
        self.print_summary()


def main():
    parser = argparse.ArgumentParser(
        description="羽毛球场地预约 - 更激进的抢场版",
        epilog="""
示例:
  python aggressive_book.py -k TOKEN -j JSESSIONID -i CARDINDEX -t 16-18
  python aggressive_book.py -k TOKEN -j JSESSIONID -i CARDINDEX --in-days 4 -t 16-18
  python aggressive_book.py -k TOKEN -j JSESSIONID -i CARDINDEX -d 2026-03-16 -t 19-21 --force
        """
    )

    parser.add_argument("-k", "--token", default="oRjsg6asr0-oCgFLVvrunP9NmGOM", help="token")
    parser.add_argument("-j", "--jsessionid", default="63C410E47DB5E8401C58FEEBAFD4E426", help="JSESSIONID")
    parser.add_argument("-i", "--card-index", default="1894101490", help="card index / offer id")

    date_group = parser.add_mutually_exclusive_group()
    date_group.add_argument("-d", "--date", help="指定日期 YYYY-MM-DD")
    date_group.add_argument("--in-days", type=int, help="N天后")

    parser.add_argument("-t", "--time", required=True, help="时段，如 9-11, 14-16, 16-18")
    parser.add_argument("-p", "--priority", nargs="+", type=int, default=[7, 8, 9, 1, 6], help="优先场地编号")
    parser.add_argument("--backup", nargs="+", type=int, default=[2, 3, 4, 5, 10, 11, 12], help="备选场地编号")

    parser.add_argument("--force", action="store_true", help="立即执行，不等待12:00")
    parser.add_argument("--rounds", type=int, default=3, help="重复轮数，默认3轮")
    parser.add_argument("--refresh-every", type=int, default=3, help="每隔多少个场地刷新一次 places，默认3")
    parser.add_argument("--step-sleep", type=float, default=0.0, help="下单步骤间休眠，默认0")
    parser.add_argument("--round-sleep", type=float, default=0.03, help="轮次间休眠，默认0.03秒")
    parser.add_argument("--connect-timeout", type=float, default=1.2, help="连接超时，默认1.2秒")
    parser.add_argument("--read-timeout", type=float, default=1.8, help="读取超时，默认1.8秒")

    parser.add_argument("--warmup", action="store_true", help="正式开抢前进行一次轻量预热")
    parser.add_argument("--warmup-ahead", type=float, default=8.0, help="距离12:00前多少秒做预热，默认8秒")

    args = parser.parse_args()

    try:
        bot = AggressiveBookingBot(args)
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

    # 一般使用 
    # python aggressive_book.py -k xxx -j yyy -i zzz --in-days 4 -t 16-18 --warmup

    # 调试
    # python aggressive_book.py -k xxx -j yyy -i zzz --in-days 4 -t 16-18 --force