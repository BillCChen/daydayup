import unittest
import tempfile
from pathlib import Path

import web_console


def make_place(date_value="2026-05-18"):
    del date_value
    return [
        {
            "projectName": {"shortname": "ymq7", "name": "羽毛球7"},
            "projectInfo": [
                {"oldMoney": 80.0, "money": 80.0, "starttime": "15:00", "endtime": "16:00", "state": 1},
                {"oldMoney": 120.0, "money": 120.0, "starttime": "16:00", "endtime": "17:00", "state": 1},
            ],
        }
    ]


class FakeUserStore:
    def __init__(self):
        self.user = web_console.UserAccount(
            key="user_1",
            label="User 1",
            token="token",
            jsessionid="session",
            card_name="学生球类卡",
            enabled=True,
        )

    def get_user(self, user_key=""):
        del user_key
        return self.user


class FakeHistory:
    def __init__(self):
        self.records = []

    def create_exact(self, payload, result, user):
        self.records.append((payload, result, user))
        return "history-1"


class FakeClient:
    def __init__(self):
        self.posts = []

    def get(self, endpoint, params=None):
        del endpoint, params
        return {"msg": "success", "data": {"placeArray": make_place()}}

    def post(self, endpoint, data=None):
        self.posts.append((endpoint, data))
        return {"msg": "success", "data": {}}


class ExactBookingTest(unittest.TestCase):
    def test_pay_value_rule(self):
        self.assertEqual(web_console.slot_pay_value("2026-05-18", "15:00"), 20.0)
        self.assertEqual(web_console.slot_pay_value("2026-05-18", "16:00"), 30.0)
        self.assertEqual(web_console.slot_pay_value("2026-05-23", "09:00"), 30.0)

    def test_availability_serializes_pay_fields(self):
        day = web_console.serialize_availability_day("2026-05-18", make_place())
        courts = [court for hour in day["hours"] for court in hour["courts"]]
        pay_by_start = {court["start_time"]: court["pay_value"] for court in courts}
        self.assertEqual(pay_by_start["15:00"], 20.0)
        self.assertEqual(pay_by_start["16:00"], 30.0)

    def test_normalize_rejects_duplicate_hour(self):
        slots = [
            {"date": "2026-05-18", "start_time": "15:00", "end_time": "16:00", "id": "ymq7"},
            {"date": "2026-05-18", "start_time": "15:00", "end_time": "16:00", "id": "ymq8"},
        ]
        with self.assertRaises(web_console.EasySerpError):
            web_console.normalize_exact_slots(slots)

    def test_exact_booking_dry_run_does_not_post(self):
        console = web_console.WebConsole.__new__(web_console.WebConsole)
        console.config = web_console.ServerConfig(
            shop_num="1001",
            base_url="https://example.invalid",
            timeout=1.0,
        )
        console.users = FakeUserStore()
        console.history = FakeHistory()
        client = FakeClient()
        console.client = lambda user: client
        console.resolve_booking_card = lambda user: {
            "card_index_raw": "card-1",
            "cash_balance_value": 50.0,
            "card_index": "car...d-1",
        }

        result = console.book_exact(
            {
                "user_key": "user_1",
                "dry_run": True,
                "slots": [{"date": "2026-05-18", "start_time": "15:00", "end_time": "16:00", "id": "ymq7"}],
            }
        )

        self.assertEqual(result["status"], "dry_run")
        self.assertEqual(len(result["successes"]), 1)
        self.assertEqual(client.posts, [])

    def test_exact_booking_uses_pay_value_for_reservation_total(self):
        console = web_console.WebConsole.__new__(web_console.WebConsole)
        console.config = web_console.ServerConfig(
            shop_num="1001",
            base_url="https://example.invalid",
            timeout=1.0,
        )
        console.users = FakeUserStore()
        console.history = FakeHistory()
        client = FakeClient()
        console.client = lambda user: client
        console.resolve_booking_card = lambda user: {
            "card_index_raw": "card-1",
            "cash_balance_value": 50.0,
            "card_index": "car...d-1",
        }

        result = console.book_exact(
            {
                "user_key": "user_1",
                "slots": [{"date": "2026-05-18", "start_time": "15:00", "end_time": "16:00", "id": "ymq7"}],
            }
        )

        reservation = client.posts[-1]
        self.assertEqual(result["status"], "success")
        self.assertEqual(reservation[0], "place/reservationPlace")
        self.assertEqual(reservation[1]["oldTotal"], "80.00")
        self.assertEqual(reservation[1]["total"], "20.00")

    def test_exact_booking_history_keeps_failure_detail(self):
        console = web_console.WebConsole.__new__(web_console.WebConsole)
        console.config = web_console.ServerConfig(
            shop_num="1001",
            base_url="https://example.invalid",
            timeout=1.0,
        )
        console.users = FakeUserStore()
        console.history = FakeHistory()
        client = FakeClient()
        console.client = lambda user: client
        console.resolve_booking_card = lambda user: {
            "card_index_raw": "card-1",
            "cash_balance_value": 50.0,
            "card_index": "car...d-1",
        }
        console._reserve_exact_slot = lambda *args, **kwargs: (_ for _ in ()).throw(web_console.EasySerpError("slot is gone"))

        result = console.book_exact(
            {
                "user_key": "user_1",
                "slots": [{"date": "2026-05-18", "start_time": "15:00", "end_time": "16:00", "id": "ymq7"}],
            }
        )

        history_result = console.history.records[0][1]
        detail = web_console.exact_history_detail(history_result)
        self.assertEqual(result["status"], "failed")
        self.assertEqual(detail["failures"][0]["error"], "slot is gone")
        self.assertEqual(detail["failures"][0]["slot"]["time"], "15:00-16:00")
        self.assertEqual(detail["failures"][0]["slot"]["name"], "羽毛球7")

    def test_create_exact_writes_failure_detail(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            history = web_console.BookingHistoryStore(Path(tmpdir) / "booking_history.json")
            user = FakeUserStore().user
            result = {
                "status": "failed",
                "result_label": "失败",
                "dry_run": False,
                "success_targets": [],
                "successes": [],
                "failures": [
                    {
                        "slot": {"date": "2026-05-18", "time": "15:00-16:00", "name": "羽毛球7", "id": "ymq7"},
                        "error": "selected slot is no longer bookable",
                    }
                ],
            }

            history.create_exact(
                {"slots": [{"date": "2026-05-18", "start_time": "15:00", "end_time": "16:00", "id": "ymq7"}]},
                result,
                user,
            )

            record = history.list(limit=1, window_hours=None)[0]
            self.assertEqual(record["detail"]["failures"][0]["error"], "selected slot is no longer bookable")
            self.assertEqual(record["detail"]["failures"][0]["slot"]["name"], "羽毛球7")


if __name__ == "__main__":
    unittest.main()
