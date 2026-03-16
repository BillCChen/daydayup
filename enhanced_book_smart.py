#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
北大医学部体育馆羽毛球场地自动预约脚本 - 智能单/双小时版

功能：
1. 支持预约 1 小时或 2 小时（通过参数 --duration 控制）
2. 当 duration=2 时：
   - 第一步先在目标范围内搜索任意一个可约小时
   - 但不是随便抢，而是优先抢“更容易补成连续两小时”的那个小时
   - 第二步只搜索与第一单相邻的一个小时，并单独再次下单
3. 当 duration=1 时：
   - 只执行第一阶段，抢到任意一个小时即结束
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

BASE_URL = "http://wechat.sportplayer.cn/easyserpClient"


class SmartBookingBot:
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

        if args.date:
            self.target_date = args.date
        elif args.in_days is not None:
            self.target_date = (datetime.now() + timedelta(days=args.in_days)).strftime("%Y-%m-%d")
        else:
            self.target_date = (datetime.now() + timedelta(days=4)).strftime("%Y-%m-%d")

        self.dt = datetime.strptime(self.target_date, "%Y-%m-%d")
        self.weekday = self.dt.weekday()

        self.range_start_h = int(args.time.split("-")[0])
        self.range_end_h = int(args.time.split("-")[1])

        if self.range_end_h <= self.range_start_h:
            raise ValueError("结束时间必须大于开始时间")
        if args.duration not in (1, 2):
            raise ValueError("--duration 只能是 1 或 2")
        if args.duration == 2 and self.range_end_h - self.range_start_h < 2:
            raise ValueError("当 --duration 2 时，时间范围长度至少为2小时")

        self.target_duration = args.duration

        self.priority_list = [f"ymq{i}" for i in (args.priority or [7, 8, 9, 1, 6])]
        self.backup_list = [f"ymq{i}" for i in (args.backup or [2, 3, 4, 5, 10, 11, 12])]
        self.court_pool = self.priority_list + self.backup_list

        self.rounds = args.rounds
        self.second_rounds = args.second_rounds
        self.fail_stats = Counter()
        self.start_run_ts = None

        self.first_booking = None
        self.second_booking = None

        self._setup_logger()

        self.logger.info("=" * 110)
        self.logger.info("羽毛球场地预约脚本启动（智能单/双小时版）")
        self.logger.info(
            f"[配置] 日期={self.target_date} ({self._weekday_name()}) | "
            f"目标范围={self.range_start_h}:00-{self.range_end_h}:00 | 目标时长={self.target_duration}小时"
        )
        self.logger.info(
            f"[配置] 第一阶段轮数={self.rounds} | 第二阶段轮数={self.second_rounds} | "
            f"step_sleep={self.args.step_sleep}s | round_sleep={self.args.round_sleep}s"
        )
        self.logger.info(f"[配置] 场地池={self.court_pool}")
        self.logger.info("=" * 110)

    def _setup_logger(self):
        os.makedirs("logs", exist_ok=True)

        log_name = (
            f"booking_smart_{self.target_date}_{self.range_start_h}-{self.range_end_h}_d{self.target_duration}_"
            f"{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
        )
        log_path = os.path.join("logs", log_name)

        self.logger = logging.getLogger(f"smart_booking_bot_{id(self)}")
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

    def _calculate_price(self, start_h, end_h):
        old_total = 0
        actual_total = 0

        for h in range(start_h, end_h):
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

    def _request(self, method, url, *, params=None, data=None, timeout=5, step=""):
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

        last_print_sec = None
        while True:
            now = datetime.now()
            if now >= target:
                break

            diff = (target - now).total_seconds()
            if diff > 1:
                time.sleep(min(0.2, diff - 0.5))
            elif diff > 0.1:
                time.sleep(0.02)
            else:
                time.sleep(0.001)

            now2 = datetime.now()
            if now2.second != last_print_sec and now2.microsecond < 50000:
                last_print_sec = now2.second
                remaining = max((target - now2).total_seconds(), 0)
                self.logger.info(f"[等待] 距离触发还有 {remaining:.3f}s")

        self.logger.info(f"[触发] 实际启动时间 {datetime.now().strftime('%H:%M:%S.%f')[:-3]}")

    def get_places(self):
        url = f"{BASE_URL}/datediscount/getPlaceInfoByShortNameDiscount"
        params = {
            "shopNum": "1001",
            "dateymd": self.target_date,
            "shortName": "ymq",
            "token": self.args.token
        }

        data, _, elapsed = self._request(
            "GET", url, params=params, timeout=5, step="get_places"
        )

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

    def build_hour_slot_table(self, places):
        result = {}

        for place in places:
            proj = place.get("projectName", {})
            court_id = proj.get("shortname")
            if court_id not in self.court_pool:
                continue

            fullname = proj.get("name", court_id)
            slots = place.get("projectInfo", [])

            hour_slots = {}
            hour_states = {}

            for slot in slots:
                start_raw = slot.get("starttime", "")
                if not start_raw:
                    continue

                start_txt = start_raw[:5]
                try:
                    hour = int(start_txt.split(":")[0])
                except Exception:
                    continue

                if self.range_start_h <= hour < self.range_end_h:
                    hour_slots[hour] = slot
                    hour_states[hour] = slot.get("state")

            result[court_id] = {
                "fullname": fullname,
                "slots": hour_slots,
                "states": hour_states
            }

        return result

    def log_snapshot(self, hour_table):
        for court_id in self.court_pool:
            info = hour_table.get(court_id)
            if not info:
                self.logger.info(f"[场地快照] {court_id} -> court_not_found")
                continue

            summary = []
            for h in range(self.range_start_h, self.range_end_h):
                summary.append(f"{h}:00-{h+1}:00:state={info['states'].get(h, 'missing')}")
            self.logger.info(f"[场地快照] {info['fullname']}({court_id}) -> {summary}")

    def log_bookable_hours(self, hour_table, hours=None):
        if hours is None:
            hours = list(range(self.range_start_h, self.range_end_h))

        any_bookable = False
        for h in hours:
            courts = []
            for court_id in self.court_pool:
                info = hour_table.get(court_id)
                if not info:
                    continue
                slot = info["slots"].get(h)
                if slot and slot.get("state") == 1:
                    courts.append(f"{info['fullname']}({court_id})")
            if courts:
                any_bookable = True
                self.logger.info(f"[可约小时] {h}:00-{h+1}:00 -> {courts}")
            else:
                self.logger.info(f"[可约小时] {h}:00-{h+1}:00 -> []")

        if not any_bookable:
            self.logger.info("[可约小时汇总] 目标范围内没有任何 state=1 的小时段")

    def get_bookable_count_for_hour(self, hour_table, hour):
        count = 0
        for court_id in self.court_pool:
            info = hour_table.get(court_id)
            if not info:
                continue
            slot = info["slots"].get(hour)
            if slot and slot.get("state") == 1:
                count += 1
        return count

    def generate_first_stage_candidates(self, hour_table, round_index):
        """
        第一阶段候选：
        - duration=1: 只按单小时可约生成候选
        - duration=2: 给每个可约小时打“可补齐连续两小时”的分数，优先分高的
        """
        candidates = []

        for h in range(self.range_start_h, self.range_end_h):
            left_count = self.get_bookable_count_for_hour(hour_table, h - 1) if h - 1 >= self.range_start_h else 0
            right_count = self.get_bookable_count_for_hour(hour_table, h + 1) if h + 1 < self.range_end_h else 0
            adjacency_count = left_count + right_count
            adjacency_max = max(left_count, right_count)

            for court_id in self.court_pool:
                info = hour_table.get(court_id)
                if not info:
                    continue

                slot = info["slots"].get(h)
                if slot and slot.get("state") == 1:
                    candidates.append({
                        "hour": h,
                        "court_id": court_id,
                        "court_name": info["fullname"],
                        "slot": slot,
                        "left_count": left_count,
                        "right_count": right_count,
                        "adjacency_count": adjacency_count,
                        "adjacency_max": adjacency_max
                    })

        if not candidates:
            return []

        if self.target_duration == 2:
            # 优先更容易补成连续两小时：
            # 1) 相邻总资源数越多越优先
            # 2) 单侧最强资源数越多越优先（避免左右都弱）
            # 3) 当前小时自身越热门不直接加权，因为当前小时已确认可约
            candidates.sort(
                key=lambda x: (
                    -x["adjacency_count"],
                    -x["adjacency_max"],
                    x["hour"],
                    x["court_id"]
                )
            )
        else:
            # duration=1 时，不需要特别偏好连续性
            candidates.sort(
                key=lambda x: (
                    x["hour"],
                    x["court_id"]
                )
            )

        # 为减少每轮都从完全相同候选起点开始，做分组轮转
        shift = (round_index - 1) % len(candidates)
        candidates = candidates[shift:] + candidates[:shift]

        return candidates

    def generate_second_stage_target_hours(self, booked_hour):
        target_hours = []
        if booked_hour - 1 >= self.range_start_h:
            target_hours.append(booked_hour - 1)
        if booked_hour + 1 < self.range_end_h:
            target_hours.append(booked_hour + 1)
        return target_hours

    def generate_second_stage_candidates(self, hour_table, booked_hour, round_index):
        """
        第二阶段：只关注已预约小时的相邻小时。
        这里同样做一点智能排序：
        - 优先选与第一单拼成连续两小时的唯一方向
        - 若左右都有，则优先相邻小时本身可约场地更多的方向
        """
        target_hours = self.generate_second_stage_target_hours(booked_hour)
        hour_counts = {h: self.get_bookable_count_for_hour(hour_table, h) for h in target_hours}

        # 先决定方向偏好
        preferred_hours = sorted(target_hours, key=lambda h: (-hour_counts[h], h))

        candidates = []
        for h in preferred_hours:
            for court_id in self.court_pool:
                info = hour_table.get(court_id)
                if not info:
                    continue
                slot = info["slots"].get(h)
                if slot and slot.get("state") == 1:
                    candidates.append({
                        "hour": h,
                        "court_id": court_id,
                        "court_name": info["fullname"],
                        "slot": slot,
                        "bookable_count_same_hour": hour_counts[h]
                    })

        if candidates:
            shift = (round_index - 1) % len(candidates)
            candidates = candidates[shift:] + candidates[:shift]

        return candidates

    def log_first_stage_candidates(self, candidates):
        if not candidates:
            self.logger.info("[第一阶段候选] 未生成任何单小时候选")
            return

        for c in candidates:
            if self.target_duration == 2:
                self.logger.info(
                    f"[第一阶段候选] {c['hour']}:00-{c['hour']+1}:00 | "
                    f"{c['court_name']}({c['court_id']}) | "
                    f"left={c['left_count']} right={c['right_count']} "
                    f"adj_total={c['adjacency_count']} adj_max={c['adjacency_max']}"
                )
            else:
                self.logger.info(
                    f"[第一阶段候选] {c['hour']}:00-{c['hour']+1}:00 | "
                    f"{c['court_name']}({c['court_id']})"
                )

    def log_second_stage_candidates(self, candidates, booked_hour):
        target_hours = self.generate_second_stage_target_hours(booked_hour)
        if not candidates:
            self.logger.info(
                f"[第二阶段候选] 围绕已预约小时 {booked_hour}:00-{booked_hour+1}:00，"
                f"相邻目标小时={target_hours}，未生成任何候选"
            )
            return

        self.logger.info(
            f"[第二阶段候选] 围绕已预约小时 {booked_hour}:00-{booked_hour+1}:00，"
            f"相邻目标小时={target_hours}"
        )
        for c in candidates:
            self.logger.info(
                f"[第二阶段候选] {c['hour']}:00-{c['hour']+1}:00 | "
                f"{c['court_name']}({c['court_id']}) | "
                f"same_hour_bookable={c['bookable_count_same_hour']}"
            )

    def attempt_single_hour_booking(self, candidate, stage_name, round_index, candidate_index, candidate_total):
        hour = candidate["hour"]
        court_id = candidate["court_id"]
        court_name = candidate["court_name"]
        slot = candidate["slot"]

        old_total, actual_total = self._calculate_price(hour, hour + 1)

        self.logger.info(
            f"[尝试] stage={stage_name} | 轮次={round_index} | 候选={candidate_index}/{candidate_total} | "
            f"时段={hour}:00-{hour+1}:00 | 场地={court_name}({court_id})"
        )

        canbook_fields = [{
            "day": self.target_date,
            "startTime": slot["starttime"][:5],
            "endTime": slot["endtime"][:5],
            "placeShortName": court_id
        }]

        field_info_full = [{
            "day": self.target_date,
            "startTime": slot["starttime"][:5],
            "endTime": slot["endtime"][:5],
            "placeShortName": court_id,
            "name": court_name,
            "stageTypeShortName": "ymq"
        }]

        self.logger.info(
            f"[下单参数] stage={stage_name} | canBook_fieldinfo={canbook_fields} | "
            f"reservation_fieldinfo={field_info_full} | oldTotal={old_total} | total={actual_total:.2f}"
        )

        r0, _, _ = self._request(
            "POST",
            f"{BASE_URL}/place/canBook",
            data={
                "fieldinfo": json.dumps(canbook_fields, ensure_ascii=False),
                "shopNum": "1001",
                "token": self.args.token
            },
            timeout=3,
            step=f"{stage_name}_canBook"
        )
        if not r0:
            self.fail_stats[f"{stage_name}_canBook_fail"] += 1
            self.logger.warning(f"[失败] stage={stage_name} canBook 请求失败")
            return False
        if r0.get("msg") != "success":
            self.fail_stats[f"{stage_name}_canBook_not_success"] += 1
            self.logger.warning(f"[失败] stage={stage_name} canBook未通过：{r0}")
            return False

        if self.args.step_sleep > 0:
            time.sleep(self.args.step_sleep)

        r1, _, _ = self._request(
            "POST",
            f"{BASE_URL}/common/getOfferInfo",
            data={
                "token": self.args.token,
                "payMoney": str(old_total),
                "shopNum": "1001",
                "projectType": "3",
                "projectInfo": json.dumps(field_info_full, ensure_ascii=False)
            },
            timeout=3,
            step=f"{stage_name}_getOfferInfo"
        )
        if not r1:
            self.fail_stats[f"{stage_name}_getOfferInfo_fail"] += 1
            self.logger.warning(f"[失败] stage={stage_name} getOfferInfo 请求失败")
            return False
        if r1.get("msg") != "success":
            self.fail_stats[f"{stage_name}_getOfferInfo_not_success"] += 1
            self.logger.warning(f"[失败] stage={stage_name} getOfferInfo失败：{r1}")
            return False

        if self.args.step_sleep > 0:
            time.sleep(self.args.step_sleep)

        r2, _, _ = self._request(
            "POST",
            f"{BASE_URL}/common/getUseCardInfo",
            data={
                "token": self.args.token,
                "shopNum": "1001",
                "projectType": "3",
                "projectInfo": json.dumps(field_info_full, ensure_ascii=False)
            },
            timeout=3,
            step=f"{stage_name}_getUseCardInfo"
        )
        if not r2:
            self.fail_stats[f"{stage_name}_getUseCardInfo_fail"] += 1
            self.logger.warning(f"[失败] stage={stage_name} getUseCardInfo 请求失败")
            return False
        if r2.get("msg") != "success":
            self.fail_stats[f"{stage_name}_getUseCardInfo_not_success"] += 1
            self.logger.warning(f"[失败] stage={stage_name} getUseCardInfo失败：{r2}")
            return False

        if self.args.step_sleep > 0:
            time.sleep(self.args.step_sleep)

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

        r3, _, _ = self._request(
            "POST",
            f"{BASE_URL}/place/reservationPlace",
            data=payload,
            timeout=5,
            step=f"{stage_name}_reservationPlace"
        )
        if not r3:
            self.fail_stats[f"{stage_name}_reservationPlace_fail"] += 1
            self.logger.warning(f"[失败] stage={stage_name} reservationPlace 请求失败")
            return False

        if r3.get("msg") == "success":
            self.logger.info(
                f"[成功] stage={stage_name} 预约成功 | 日期={self.target_date} | "
                f"时段={hour}:00-{hour+1}:00 | 场地={court_name}({court_id})"
            )
            return True

        self.fail_stats[f"{stage_name}_reservationPlace_not_success"] += 1
        self.logger.warning(f"[失败] stage={stage_name} reservationPlace失败：{r3}")
        return False

    def run_first_stage(self):
        self.logger.info("-" * 110)
        if self.target_duration == 2:
            self.logger.info("[第一阶段] 开始搜索目标范围内“更容易补成连续两小时”的可约小时")
        else:
            self.logger.info("[第一阶段] 开始搜索目标范围内任意一个可约小时")

        for round_index in range(1, self.rounds + 1):
            self.logger.info("-" * 110)
            self.logger.info(f"[第一阶段] 开始第 {round_index}/{self.rounds} 轮")

            places = self.get_places()
            if not places:
                self.logger.warning("[第一阶段] 本轮未获取到有效场地数据")
                continue

            hour_table = self.build_hour_slot_table(places)
            self.log_snapshot(hour_table)
            self.log_bookable_hours(hour_table)

            candidates = self.generate_first_stage_candidates(hour_table, round_index)
            self.logger.info(f"[第一阶段] 候选总数={len(candidates)}")
            self.log_first_stage_candidates(candidates)

            for idx, candidate in enumerate(candidates, start=1):
                success = self.attempt_single_hour_booking(
                    candidate=candidate,
                    stage_name="first_stage",
                    round_index=round_index,
                    candidate_index=idx,
                    candidate_total=len(candidates)
                )
                if success:
                    self.first_booking = candidate
                    return True

            if round_index < self.rounds and self.args.round_sleep > 0:
                time.sleep(self.args.round_sleep)

        return False

    def run_second_stage(self):
        if self.target_duration != 2:
            return False
        if not self.first_booking:
            return False

        booked_hour = self.first_booking["hour"]

        self.logger.info("-" * 110)
        self.logger.info(
            f"[第二阶段] 第一单已成功：{booked_hour}:00-{booked_hour+1}:00 "
            f"{self.first_booking['court_name']}({self.first_booking['court_id']})"
        )
        self.logger.info("[第二阶段] 开始只搜索相邻一个小时的任意场地")

        target_hours = self.generate_second_stage_target_hours(booked_hour)
        if not target_hours:
            self.logger.warning("[第二阶段] 第一单位于边界，范围内不存在相邻小时，第二阶段终止")
            return False

        for round_index in range(1, self.second_rounds + 1):
            self.logger.info("-" * 110)
            self.logger.info(f"[第二阶段] 开始第 {round_index}/{self.second_rounds} 轮 | 目标小时={target_hours}")

            places = self.get_places()
            if not places:
                self.logger.warning("[第二阶段] 本轮未获取到有效场地数据")
                continue

            hour_table = self.build_hour_slot_table(places)
            self.log_bookable_hours(hour_table, hours=target_hours)

            candidates = self.generate_second_stage_candidates(hour_table, booked_hour, round_index)
            self.logger.info(f"[第二阶段] 候选总数={len(candidates)}")
            self.log_second_stage_candidates(candidates, booked_hour)

            filtered_candidates = [c for c in candidates if c["hour"] != booked_hour]

            for idx, candidate in enumerate(filtered_candidates, start=1):
                success = self.attempt_single_hour_booking(
                    candidate=candidate,
                    stage_name="second_stage",
                    round_index=round_index,
                    candidate_index=idx,
                    candidate_total=len(filtered_candidates)
                )
                if success:
                    self.second_booking = candidate
                    return True

            if round_index < self.second_rounds and self.args.round_sleep > 0:
                time.sleep(self.args.round_sleep)

        return False

    def print_summary(self):
        self.logger.info("=" * 110)
        self.logger.info("[汇总] 本次预约任务结束")
        self.logger.info(f"[汇总] 日志文件：{self.log_path}")

        if self.first_booking:
            self.logger.info(
                f"[汇总] 第一单成功：{self.first_booking['hour']}:00-{self.first_booking['hour']+1}:00 "
                f"{self.first_booking['court_name']}({self.first_booking['court_id']})"
            )
        else:
            self.logger.info("[汇总] 第一单未成功")

        if self.target_duration == 2:
            if self.second_booking:
                self.logger.info(
                    f"[汇总] 第二单成功：{self.second_booking['hour']}:00-{self.second_booking['hour']+1}:00 "
                    f"{self.second_booking['court_name']}({self.second_booking['court_id']})"
                )
            else:
                self.logger.info("[汇总] 第二单未成功")

            if self.first_booking and self.second_booking:
                hours = sorted([self.first_booking["hour"], self.second_booking["hour"]])
                if len(hours) == 2 and hours[1] - hours[0] == 1:
                    self.logger.info(f"[汇总] 最终实现连续两小时：{hours[0]}:00-{hours[1]+1}:00")
                else:
                    self.logger.info("[汇总] 两单成功，但未构成连续两小时")
            else:
                self.logger.info("[汇总] 未完成连续两小时目标")
        else:
            if self.first_booking:
                self.logger.info("[汇总] 已完成单小时预约目标")

        self.logger.info(f"[汇总] 失败统计：{dict(self.fail_stats)}")
        self.logger.info("=" * 110)

    def run(self):
        try:
            self.wait_until_noon()
        except ValueError as e:
            self.logger.error(f"[错误] {e}")
            return

        self.start_run_ts = time.perf_counter()
        self.logger.info(
            f"[开始] 启动预约流程：目标范围 {self.range_start_h}:00-{self.range_end_h}:00 | "
            f"目标时长={self.target_duration}小时"
        )

        first_ok = self.run_first_stage()
        if not first_ok:
            self.logger.warning("[结束] 第一阶段未抢到任何一个小时")
            self.print_summary()
            return

        if self.target_duration == 1:
            self.logger.info("[完成] 单小时目标已达成")
            self.print_summary()
            return

        second_ok = self.run_second_stage()
        if not second_ok:
            self.logger.warning("[结束] 第一阶段成功，但第二阶段未抢到相邻小时")
            self.print_summary()
            return

        self.logger.info("[完成] 两阶段均成功")
        self.print_summary()


def main():
    parser = argparse.ArgumentParser(
        description="羽毛球场地预约 - 智能单/双小时版",
        epilog="""
示例:
  # 只预约 1 小时
  python enhanced_book_smart.py -k TOKEN -j JSESSIONID -i CARDINDEX -t 17-21 --duration 1

  # 预约 2 小时，优先抢更容易拼成连续两小时的那个小时
  python enhanced_book_smart.py -k TOKEN -j JSESSIONID -i CARDINDEX -t 17-21 --duration 2

  # 指定日期
  python enhanced_book_smart.py -k TOKEN -j JSESSIONID -i CARDINDEX -d 2026-03-19 -t 16-21 --duration 2

  # 调试
  python enhanced_book_smart.py -k TOKEN -j JSESSIONID -i CARDINDEX --in-days 4 -t 17-21 --duration 2 --force
        """
    )

    parser.add_argument("-k", "--token", default="oRjsg6asr0-oCgFLVvrunP9NmGOM", help="token")
    parser.add_argument("-j", "--jsessionid", default="63C410E47DB5E8401C58FEEBAFD4E426", help="JSESSIONID")
    parser.add_argument("-i", "--card-index", default="1894101490", help="card index / offer id")

    date_group = parser.add_mutually_exclusive_group()
    date_group.add_argument("-d", "--date", help="指定日期 YYYY-MM-DD")
    date_group.add_argument("--in-days", type=int, help="N天后")

    parser.add_argument("-t", "--time", required=True, help="时间范围，如 17-18, 17-19, 17-21")
    parser.add_argument("--duration", type=int, default=2, help="目标预约时长，只能为 1 或 2，默认2")

    parser.add_argument("-p", "--priority", nargs="+", type=int, default=[7, 8, 9, 1, 6], help="priority 场地编号")
    parser.add_argument("--backup", nargs="+", type=int, default=[2, 3, 4, 5, 10, 11, 12], help="backup 场地编号")
    parser.add_argument("--force", action="store_true", help="立即执行，不等待12:00")
    parser.add_argument("--rounds", type=int, default=100, help="第一阶段轮数，默认100")
    parser.add_argument("--second-rounds", type=int, default=100, help="第二阶段轮数，默认100")
    parser.add_argument("--step-sleep", type=float, default=0.05, help="下单步骤间短暂间隔，默认0.05秒")
    parser.add_argument("--round-sleep", type=float, default=0.10, help="轮次间间隔，默认0.10秒")

    args = parser.parse_args()

    try:
        bot = SmartBookingBot(args)
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
    # 约 1 小时
    # python enhanced_book_smart.py --in-days 4 -t 17-21 --duration 1

    # 约 2 小时
    # python enhanced_book_smart.py --in-days 4 -t 17-21 --duration 2

    # 调试
    # python enhanced_book_smart.py --in-days 4 -t 17-21 --duration 2 --force
