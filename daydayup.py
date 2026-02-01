#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
北大医学部体育馆羽毛球场地自动预约脚本 - 修复版
支持：自动合并连续时段（如 16-17 + 17-18 = 16-18）
"""

import requests
import json
import time
import threading
import argparse
import sys
import re
from datetime import datetime, timedelta

DEFAULTS = {
    "shop_num": "1001",
    "base_url": "http://wechat.sportplayer.cn/easyserpClient",
}

class FixedBookingBot:
    def __init__(self, args):
        self.args = args
        self.session = requests.Session()
        
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
        
        self.target_date = args.date
        # 解析目标小时范围
        start_h, end_h = map(int, args.time.split('-'))
        self.target_start_h = start_h
        self.target_end_h = end_h
        self.target_hours = end_h - start_h  # 需要连续的小时数
        
        print(f"[配置] 日期: {self.target_date}")
        print(f"[配置] 目标: {start_h}:00-{end_h}:00 (共{self.target_hours}小时)")
        print(f"[配置] 场地: {args.court}")
        
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
            resp = self.session.get(url, params=params, timeout=5)
            data = resp.json()
            if data.get("msg") == "success":
                return data.get("data", {}).get("placeArray", [])
        except Exception as e:
            print(f"[错误] 获取场地信息失败: {e}")
        return None
    
    def find_continuous_slots(self, places, target_court):
        """
        查找连续可用时段
        例如要约16-18，需要找到16-17和17-18都可约
        """
        print(f"\n[查找] 场地 {target_court} 的连续{self.target_hours}小时时段...")
        
        for place in places:
            proj_name = place.get("projectName", {})
            if proj_name.get("shortname") != target_court:
                continue
            
            slots = place.get("projectInfo", [])
            fullname = proj_name.get("name")
            
            # 构建时间段映射表 (hour -> slot)
            slot_map = {}
            for slot in slots:
                start = slot.get("starttime", "")[:5]  # 取 HH:MM
                state = slot.get("state")
                if state == 4:  # 可约
                    hour = int(start.split(':')[0])
                    slot_map[hour] = slot
            
            # 检查是否有足够的连续时段
            continuous_slots = []
            for h in range(self.target_start_h, self.target_end_h):
                if h in slot_map:
                    continuous_slots.append(slot_map[h])
                else:
                    break
            
            if len(continuous_slots) == self.target_hours:
                print(f"[✓] 找到连续时段！")
                for s in continuous_slots:
                    print(f"    {s['starttime'][:5]}-{s['endtime'][:5]}")
                return continuous_slots, fullname
            else:
                print(f"[×] 只有 {len(continuous_slots)}/{self.target_hours} 小时可约")
                return None, None
        
        return None, None
    
    def book_court(self, slots, fullname):
        """提交预约（合并连续时段）"""
        # 计算总时间和费用
        start_time = slots[0]["starttime"]  # 第一个开始时间
        end_time = slots[-1]["endtime"]     # 最后一个结束时间
        hours = len(slots)
        
        # 计算价格（注意：可能有不同的oldMoney，这里取第一个的，或分别计算）
        total_old = sum(s["oldMoney"] for s in slots)
        total_new = sum(s["money"] for s in slots)
        
        # 构建 fieldinfo（包含所有连续时段）
        field_info = []
        for slot in slots:
            field_info.append({
                "day": self.target_date,
                "startTime": slot["starttime"],
                "endTime": slot["endtime"],
                "placeShortName": self.args.court,
                "name": fullname,
                "stageTypeShortName": "ymq"
            })
        
        print(f"\n[预约] 提交 {hours} 小时连续预约")
        print(f"  总时段: {start_time[:5]} - {end_time[:5]}")
        print(f"  费用: ¥{total_new} (原价¥{total_old})")
        
        # Step 1: getOfferInfo
        try:
            r1 = self.session.post(
                f"{DEFAULTS['base_url']}/common/getOfferInfo",
                data={
                    "token": self.args.token,
                    "payMoney": str(total_old),
                    "shopNum": DEFAULTS['shop_num'],
                    "projectType": "3",
                    "projectInfo": json.dumps(field_info)
                },
                timeout=3
            )
            if r1.json().get("msg") != "success":
                print(f"  [Step1失败] {r1.text[:100]}")
                return False
            print("  [Step1] 获取优惠成功")
        except Exception as e:
            print(f"  [Step1错误] {e}")
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
                timeout=3
            )
            if r2.json().get("msg") != "success":
                print(f"  [Step2失败] {r2.text[:100]}")
                return False
            print("  [Step2] 获取卡信息成功")
        except Exception as e:
            print(f"  [Step2错误] {e}")
            return False
        
        # Step 3: reservationPlace
        try:
            r3 = self.session.post(
                f"{DEFAULTS['base_url']}/place/reservationPlace",
                data={
                    "token": self.args.token,
                    "shopNum": DEFAULTS['shop_num'],
                    "fieldinfo": json.dumps(field_info),
                    "oldTotal": str(total_old),
                    "cardPayType": "0",
                    "type": "羽毛球",
                    "offerId": self.args.card_index,
                    "offerType": "3",
                    "total": str(total_new),
                    "premerother": "",
                    "cardIndex": self.args.card_index,
                    "masterCardNum": "",
                    "zengzhiMoney": "0"
                },
                timeout=5
            )
            result = r3.json()
            
            if result.get("msg") == "success":
                print(f"\n[✓✓✓] 预约成功!")
                print(f"  场地: {fullname}")
                print(f"  时间: {self.target_date} {start_time[:5]}-{end_time[:5]}")
                print(f"  支付: ¥{total_new}")
                return True
            else:
                print(f"\n[×××] 预约失败: {result.get('msg')}")
                return False
        except Exception as e:
            print(f"  [Step3错误] {e}")
            return False
    
    def run(self):
        """主流程"""
        print("="*60)
        print("连续时段自动合并预约")
        print("="*60)
        
        places = self.get_place_info()
        if not places:
            print("[结束] 无法获取场地")
            return
        
        slots, fullname = self.find_continuous_slots(places, self.args.court)
        
        if slots:
            success = self.book_court(slots, fullname)
            if success:
                print("\n[SUCCESS] 完成!")
            else:
                print("\n[FAILED] 预约失败")
        else:
            print(f"\n[FAILED] 找不到连续的{self.target_hours}小时空闲时段")


def main():
    parser = argparse.ArgumentParser(description="连续时段预约（自动合并）")
    parser.add_argument("-k", "--token", required=True, help="微信Token")
    parser.add_argument("-j", "--jsessionid", required=True, help="JSESSIONID")
    parser.add_argument("-i", "--card-index", required=True, help="会员卡号")
    parser.add_argument("-d", "--date", required=True, help="日期 YYYY-MM-DD")
    parser.add_argument("-t", "--time", required=True, help="时间 如 16-18（表示16:00-18:00）")
    parser.add_argument("-c", "--court", default="ymq8", help="场地 如 ymq8")
    
    args = parser.parse_args()
    bot = FixedBookingBot(args)
    bot.run()


if __name__ == "__main__":
    main()


    