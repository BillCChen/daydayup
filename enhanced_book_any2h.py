#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
北大医学部体育馆羽毛球场地自动预约脚本 - 任意两小时优先版

设计目标：
1. 输入一个时间范围，例如 17-21
2. 在整个范围内搜索任意连续两小时组合：
   - 17-19
   - 18-20
   - 19-21
3. 只要存在任意可约的两小时组合，就立刻尝试预约
4. 不再显式优先某个时间段或某个 priority 场地
5. 同场、跨场都允许
6. 一切以“尽快约到两小时”为最高优先级
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


class Any2HourBookingBot:
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

        if self.range_end_h - self.range_start_h < 2:
            raise ValueError("时间范围长度至少为2小时，例如 17-19 或 17-21")

        # 这里仍然保留 priority / backup 作为“可搜索场地全集”
        # 但后续不再把它们当作严格优先序列使用
        self.priority_list = [f"ymq{i}" for i in (args.priority or [7, 8, 9, 1, 6])]
        self.backup_list = [f"ymq{i}" for i in (args.backup or [2, 3, 4, 5, 10, 11, 12])]
        self.court_pool = self.priority_list + self.backup_list

        self.rounds = args.rounds
        self.fail_stats = Counter()
        self.start_run_ts = None

        self._setup_logger()

        self.logger.info("=" * 100)
        self.logger.info("羽毛球场地预约脚本启动（任意两小时优先版）")
        self.logger.info(
            f"[配置] 日期={self.target_date} ({self._weekday_name()}) | "
            f"目标范围={self.range_start_h}:00-{self.range_end_h}:00"
        )
        self.logger.info(
            f"[配置] rounds={self.rounds} | step_sleep={self.args.step_sleep}s | round_sleep={self.args.round_sleep}s"
        )
        self.logger.info(f"[配置] priority={self.priority_list}")
        self.logger.info(f"[配置] backup={self.backup_list}")
        self.logger.info(f"[配置] 候选场地池={self.court_pool}")
        self.logger.info("=" * 100)

    def _setup_logger(self):
        os.makedirs("logs", exist_ok=True)

        log_name = (
            f"booking_any2h_{self.target_date}_{self.range_start_h}-{self.range_end_h}_"
            f"{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
        )
        log_path = os.path.join("logs", log_name)

        self.logger = logging.getLogger(f"any2h_booking_bot_{id(self)}")
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
                if 9 <= h < 16:
                    old_total += 80
                    actual_total += 20
                elif 16 <= h < 22:
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
            # self.logger.info(f"[HTTP] step={step} response_preview={text_preview}")

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
        """
        构造按小时和场地索引的可用表。
        返回：
        {
            "ymq7": {
                "fullname": "...",
                "slots": {17: slot_obj, 18: slot_obj, ...},
                "states": {17: 1, 18: 3, ...}
            },
            ...
        }
        """
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
            # self.logger.info(f"[场地快照] {info['fullname']}({court_id}) -> {summary}")

    def _is_bookable_slot(self, hour_table, court_id, hour):
        info = hour_table.get(court_id)
        if not info:
            return None
        slot = info["slots"].get(hour)
        if slot and slot.get("state") == 1:
            return slot
        return None

    def generate_candidate_pool(self, hour_table, round_index):
        """
        在整个范围内一次性生成所有“两小时候选组合”。

        候选结构：
        {
            "mode": "same_court" / "cross_court",
            "start_h": 17,
            "end_h": 19,
            "slots": [slot1, slot2],
            "court_ids": ["ymq7", "ymq8"],
            "court_names": ["羽毛球7号场", "羽毛球8号场"]
        }

        这里不再把时间和场地作为强优先级。
        但为了避免每轮顺序完全固定，这里做一个“轮次偏移”。
        """
        all_candidates = []
        dedup_keys = set()

        windows = [(h, h + 2) for h in range(self.range_start_h, self.range_end_h - 1)]

        # 收集每个小时所有可约场地
        hour_bookable = {}
        for h in range(self.range_start_h, self.range_end_h):
            bookable_list = []
            for court_id in self.court_pool:
                slot = self._is_bookable_slot(hour_table, court_id, h)
                if slot:
                    info = hour_table[court_id]
                    bookable_list.append({
                        "court_id": court_id,
                        "court_name": info["fullname"],
                        "slot": slot
                    })
            hour_bookable[h] = bookable_list

        # 先扫描所有时间窗，但不人为设置强优先级
        for start_h, end_h in windows:
            h1 = start_h
            h2 = start_h + 1

            first_hour = hour_bookable.get(h1, [])
            second_hour = hour_bookable.get(h2, [])

            if not first_hour or not second_hour:
                continue

            # 同场
            map_h2 = {x["court_id"]: x for x in second_hour}
            for x1 in first_hour:
                cid = x1["court_id"]
                if cid in map_h2:
                    x2 = map_h2[cid]
                    key = ("same_court", h1, cid, cid)
                    if key not in dedup_keys:
                        dedup_keys.add(key)
                        all_candidates.append({
                            "mode": "same_court",
                            "start_h": h1,
                            "end_h": end_h,
                            "slots": [x1["slot"], x2["slot"]],
                            "court_ids": [cid, cid],
                            "court_names": [x1["court_name"], x2["court_name"]],
                        })

            # 跨场
            for x1 in first_hour:
                for x2 in second_hour:
                    cid1 = x1["court_id"]
                    cid2 = x2["court_id"]
                    if cid1 == cid2:
                        continue
                    key = ("cross_court", h1, cid1, cid2)
                    if key not in dedup_keys:
                        dedup_keys.add(key)
                        all_candidates.append({
                            "mode": "cross_court",
                            "start_h": h1,
                            "end_h": end_h,
                            "slots": [x1["slot"], x2["slot"]],
                            "court_ids": [cid1, cid2],
                            "court_names": [x1["court_name"], x2["court_name"]],
                        })

        # 为了避免每轮总是同一个顺序，做轮次偏移轮转
        if all_candidates:
            shift = (round_index - 1) % len(all_candidates)
            all_candidates = all_candidates[shift:] + all_candidates[:shift]

        return all_candidates

    def attempt_book_candidate(self, candidate, global_attempt, round_index, candidate_index, candidate_total):
        start_h = candidate["start_h"]
        end_h = candidate["end_h"]
        slots = candidate["slots"]
        court_ids = candidate["court_ids"]
        court_names = candidate["court_names"]
        mode = candidate["mode"]

        old_total, actual_total = self._calculate_price(start_h, end_h)

        self.logger.info(
            f"[尝试] 全局序号={global_attempt} | 轮次={round_index}/{self.rounds} | "
            f"候选={candidate_index}/{candidate_total} | mode={mode} | "
            f"时段={start_h}:00-{end_h}:00 | 场地={list(zip(court_names, court_ids))}"
        )

        canbook_fields = [{
            "day": self.target_date,
            "startTime": slots[i]["starttime"][:5],
            "endTime": slots[i]["endtime"][:5],
            "placeShortName": court_ids[i]
        } for i in range(2)]

        # 同场用整体区间；跨场用逐小时明细
        if mode == "same_court":
            field_info_full = [{
                "day": self.target_date,
                "startTime": slots[0]["starttime"][:5],
                "endTime": slots[-1]["endtime"][:5],
                "placeShortName": court_ids[0],
                "name": court_names[0],
                "stageTypeShortName": "ymq"
            }]
        else:
            field_info_full = [{
                "day": self.target_date,
                "startTime": slots[i]["starttime"][:5],
                "endTime": slots[i]["endtime"][:5],
                "placeShortName": court_ids[i],
                "name": court_names[i],
                "stageTypeShortName": "ymq"
            } for i in range(2)]

        self.logger.info(
            f"[下单参数] mode={mode} | canBook_fieldinfo={canbook_fields} | "
            f"reservation_fieldinfo={field_info_full} | oldTotal={old_total} | total={actual_total:.2f}"
        )

        # Step 0
        r0, _, _ = self._request(
            "POST",
            f"{BASE_URL}/place/canBook",
            data={
                "fieldinfo": json.dumps(canbook_fields, ensure_ascii=False),
                "shopNum": "1001",
                "token": self.args.token
            },
            timeout=3,
            step="canBook"
        )
        if not r0:
            self.fail_stats["canBook_fail"] += 1
            self.logger.warning("[失败] canBook 请求失败")
            return False
        if r0.get("msg") != "success":
            self.fail_stats["canBook_not_success"] += 1
            self.logger.warning(f"[失败] canBook未通过：{r0}")
            return False

        if self.args.step_sleep > 0:
            time.sleep(self.args.step_sleep)

        # Step 1
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
            step="getOfferInfo"
        )
        if not r1:
            self.fail_stats["getOfferInfo_fail"] += 1
            self.logger.warning("[失败] getOfferInfo 请求失败")
            return False
        if r1.get("msg") != "success":
            self.fail_stats["getOfferInfo_not_success"] += 1
            self.logger.warning(f"[失败] getOfferInfo失败：{r1}")
            return False

        if self.args.step_sleep > 0:
            time.sleep(self.args.step_sleep)

        # Step 2
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
            step="getUseCardInfo"
        )
        if not r2:
            self.fail_stats["getUseCardInfo_fail"] += 1
            self.logger.warning("[失败] getUseCardInfo 请求失败")
            return False
        if r2.get("msg") != "success":
            self.fail_stats["getUseCardInfo_not_success"] += 1
            self.logger.warning(f"[失败] getUseCardInfo失败：{r2}")
            return False

        if self.args.step_sleep > 0:
            time.sleep(self.args.step_sleep)

        # Step 3
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
            step="reservationPlace"
        )
        if not r3:
            self.fail_stats["reservationPlace_fail"] += 1
            self.logger.warning("[失败] reservationPlace 请求失败")
            return False

        if r3.get("msg") == "success":
            elapsed = time.perf_counter() - self.start_run_ts if self.start_run_ts else -1
            self.logger.info(
                f"[成功] 预约成功 | mode={mode} | 日期={self.target_date} | "
                f"时段={start_h}:00-{end_h}:00 | 场地={list(zip(court_names, court_ids))} | "
                f"启动后耗时={elapsed:.3f}s"
            )
            return True

        self.fail_stats["reservationPlace_not_success"] += 1
        self.logger.warning(f"[失败] reservationPlace失败：{r3}")
        return False

    def print_summary(self):
        self.logger.info("=" * 100)
        self.logger.info("[汇总] 本次预约任务结束")
        self.logger.info(f"[汇总] 日志文件：{self.log_path}")
        if self.fail_stats:
            self.logger.info(f"[汇总] 失败统计：{dict(self.fail_stats)}")
        else:
            self.logger.info("[汇总] 无失败统计")
        self.logger.info("=" * 100)

    def run(self):
        try:
            self.wait_until_noon()
        except ValueError as e:
            self.logger.error(f"[错误] {e}")
            return

        self.start_run_ts = time.perf_counter()
        self.logger.info(
            f"[开始] 启动预约流程：共 {self.rounds} 轮，目标范围 {self.range_start_h}:00-{self.range_end_h}:00"
        )

        global_attempt = 0

        for round_index in range(1, self.rounds + 1):
            self.logger.info("-" * 100)
            self.logger.info(f"[轮次] 开始第 {round_index}/{self.rounds} 轮")

            places = self.get_places()
            if not places:
                self.logger.warning("[轮次] 本轮未获取到有效场地数据，进入下一轮")
                continue

            hour_table = self.build_hour_slot_table(places)
            self.log_snapshot(hour_table)
            self.log_bookable_hours(hour_table)

            candidates = self.generate_candidate_pool(hour_table, round_index)
            self.logger.info(f"[候选] 本轮生成候选组合数={len(candidates)}")
            self.log_candidate_summary(candidates)

            if not candidates:
                self.fail_stats["no_candidate_found"] += 1
            else:
                for idx, candidate in enumerate(candidates, start=1):
                    global_attempt += 1
                    success = self.attempt_book_candidate(
                        candidate=candidate,
                        global_attempt=global_attempt,
                        round_index=round_index,
                        candidate_index=idx,
                        candidate_total=len(candidates)
                    )
                    if success:
                        self.print_summary()
                        return

            if round_index < self.rounds and self.args.round_sleep > 0:
                time.sleep(self.args.round_sleep)

        self.logger.warning("[结束] 未达到目标，预约失败")
        self.print_summary()
    def log_bookable_hours(self, hour_table):
        any_bookable = False

        for h in range(self.range_start_h, self.range_end_h):
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
    def log_candidate_summary(self, candidates):
        if not candidates:
            self.logger.info("[两小时候选汇总] 未生成任何可预约候选")
            return

        grouped = {}
        for c in candidates:
            key = f"{c['start_h']}:00-{c['end_h']}:00"
            grouped.setdefault(key, {"same_court": [], "cross_court": []})

            if c["mode"] == "same_court":
                grouped[key]["same_court"].append(c["court_ids"][0])
            else:
                grouped[key]["cross_court"].append(tuple(c["court_ids"]))

        for window, info in grouped.items():
            self.logger.info(
                f"[两小时候选] {window} -> "
                f"同场={info['same_court']} | 跨场={info['cross_court']}"
            )


def main():
    parser = argparse.ArgumentParser(
        description="羽毛球场地预约 - 任意两小时优先版",
        epilog="""
示例:
  # 17-21 范围内任意连续两小时都可以
  python enhanced_book_any2h.py -k TOKEN -j JSESSIONID -i CARDINDEX -t 17-21

  # 指定日期
  python enhanced_book_any2h.py -k TOKEN -j JSESSIONID -i CARDINDEX -d 2026-03-16 -t 17-21

  # force 调试
  python enhanced_book_any2h.py -k TOKEN -j JSESSIONID -i CARDINDEX --in-days 4 -t 17-21 --force
        """
    )

    parser.add_argument("-k", "--token", default="oRjsg6asr0-oCgFLVvrunP9NmGOM", help="token")
    parser.add_argument("-j", "--jsessionid", default="63C410E47DB5E8401C58FEEBAFD4E426", help="JSESSIONID")
    parser.add_argument("-i", "--card-index", default="1894101490", help="card index / offer id")

    date_group = parser.add_mutually_exclusive_group()
    date_group.add_argument("-d", "--date", help="指定日期 YYYY-MM-DD")
    date_group.add_argument("--in-days", type=int, help="N天后")

    parser.add_argument("-t", "--time", required=True, help="时间范围，如 17-19, 17-21")
    parser.add_argument("-p", "--priority", nargs="+", type=int, default=[7, 8, 9, 1, 6], help="priority 场地编号")
    parser.add_argument("--backup", nargs="+", type=int, default=[2, 3, 4, 5, 10, 11, 12], help="backup 场地编号")
    parser.add_argument("--force", action="store_true", help="立即执行，不等待12:00")
    parser.add_argument("--rounds", type=int, default=3, help="重复轮数，默认3轮")
    parser.add_argument("--step-sleep", type=float, default=0.0625, help="下单步骤间短暂间隔，默认0.05秒")
    parser.add_argument("--round-sleep", type=float, default=0.1625, help="轮次间间隔，默认0.10秒")

    args = parser.parse_args()

    try:
        bot = Any2HourBookingBot(args)
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
    # python enhanced_book_any2h.py --in-days 4 -t 17-21 --force