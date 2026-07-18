import unittest
from unittest.mock import patch

import web_console


BILL_NUM = "1001202605111436114115757138231"


def active_order():
    return {
        "billNum": BILL_NUM,
        "prestatus": "等待",
        "payType": "会员卡支付",
        "stagenum": "羽毛球5",
        "itemorgoodshortname": "ymq",
        "readycashnum": 30.0,
        "readydate": "2026-05-15",
        "readystarttime": "22:00:00",
        "readyendtime": "23:00:00",
        "preTime": "2026-05-11 14:36:11",
    }


def cancelled_order():
    order = active_order()
    order["prestatus"] = "取消"
    return order


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


class FakeCancelClient:
    def __init__(
        self,
        *,
        cancel_response=None,
        apply_cancel=True,
        visibility_delay=0,
        post_error=None,
        reconcile_get_errors=0,
        card_get_error=None,
    ):
        self.gets = []
        self.posts = []
        self.cancel_response = cancel_response or {"msg": "success", "data": ""}
        self.apply_cancel = apply_cancel
        self.visibility_delay = visibility_delay
        self.post_error = post_error
        self.reconcile_get_errors = reconcile_get_errors
        self.card_get_error = card_get_error
        self.cancel_requested = False
        self.reconcile_get_count = 0

    def get(self, endpoint, params=None):
        self.gets.append((endpoint, dict(params or {})))
        if endpoint == "place/getPlaceOrder":
            if self.cancel_requested:
                self.reconcile_get_count += 1
                if self.reconcile_get_count <= self.reconcile_get_errors:
                    raise web_console.EasySerpError("order reconciliation unavailable")
                cancellation_visible = self.apply_cancel and self.reconcile_get_count > self.visibility_delay
                return {"msg": "success", "data": [cancelled_order() if cancellation_visible else active_order()]}
            return {"msg": "success", "data": [active_order()]}
        if endpoint == "common/getRefundTime":
            return {
                "msg": "success",
                "data": [
                    {
                        "refundPercentage": 100,
                        "canceltime": 12,
                        "cancleTimeType": 1,
                        "shortname": "ymq",
                    }
                ],
            }
        if endpoint == "place/getCanclePlaceMoney":
            return {
                "msg": "success",
                "data": {
                    "payMoney": 30.0,
                    "zengzhiMoney": 0,
                    "placeMoney": 30.0,
                    "reFundMoney": 30.0,
                },
            }
        if endpoint == "card/getCardByUser":
            if self.card_get_error:
                raise self.card_get_error
            return {
                "msg": "success",
                "data": [
                    {
                        "cardname": "学生球类卡",
                        "cardindex": "1894101490",
                        "cardcash": 166.5,
                    }
                ],
            }
        raise AssertionError(f"unexpected GET {endpoint}")

    def post(self, endpoint, data=None):
        self.posts.append((endpoint, dict(data or {})))
        if endpoint == "place/canclePlaceAppointment":
            self.cancel_requested = True
            if self.post_error:
                raise self.post_error
            return self.cancel_response
        raise AssertionError(f"unexpected POST {endpoint}")


def make_console(client):
    console = web_console.WebConsole.__new__(web_console.WebConsole)
    console.config = web_console.ServerConfig(
        shop_num="1001",
        base_url="https://www.147soft.cn/easyserpClient",
        timeout=1.0,
    )
    console.users = FakeUserStore()
    console.client = lambda user: client
    return console


class CancelBookingTest(unittest.TestCase):
    def test_cancel_preview_uses_captured_refund_flow(self):
        client = FakeCancelClient()
        console = make_console(client)

        result = console.cancel_preview(BILL_NUM, "user_1")

        endpoints = [item[0] for item in client.gets]
        self.assertEqual(
            endpoints,
            [
                "place/getPlaceOrder",
                "common/getRefundTime",
                "place/getCanclePlaceMoney",
            ],
        )
        self.assertEqual(client.gets[1][1]["shortName"], "ymq")
        self.assertEqual(client.gets[2][1]["billNum"], BILL_NUM)
        self.assertEqual(result["refund"]["refund_money"], "30.00")
        self.assertEqual(result["rule"]["refund_percentage"], 100)

    def test_cancel_posts_captured_payload_and_confirms_status(self):
        client = FakeCancelClient()
        console = make_console(client)

        with patch("web_console.time.sleep", return_value=None):
            result = console.cancel(
                {
                    "user_key": "user_1",
                    "bill_num": BILL_NUM,
                    "confirmation": "CANCEL",
                    "reason": "天气原因",
                    "affiliate_card": "",
                    "require_confirmed": True,
                }
            )

        self.assertEqual(client.posts[0][0], "place/canclePlaceAppointment")
        self.assertEqual(
            client.posts[0][1],
            {
                "outtradeno": BILL_NUM,
                "token": "token",
                "reason": "天气原因",
                "affiliateCard": "",
            },
        )
        self.assertTrue(result["confirmed"])
        self.assertEqual(result["booking"]["status"], "取消")
        self.assertEqual(len(client.posts), 1)

    def test_cancel_reconciles_fail_response_after_applied_side_effect(self):
        client = FakeCancelClient(cancel_response={"msg": "fail", "data": "fail"})
        console = make_console(client)

        with patch("web_console.time.sleep", return_value=None):
            result = console.cancel(
                {
                    "user_key": "user_1",
                    "bill_num": BILL_NUM,
                    "confirmation": "CANCEL",
                    "require_confirmed": True,
                }
            )

        self.assertTrue(result["confirmed"])
        self.assertTrue(result["reconciled"])
        self.assertEqual(result["response"]["msg"], "fail")
        self.assertEqual(len(client.posts), 1)
        self.assertEqual(result["reconciliation_checks"], 1)

    def test_cancel_reconciles_request_error_after_applied_side_effect(self):
        client = FakeCancelClient(post_error=web_console.EasySerpError("cancel request timed out"))
        console = make_console(client)

        with patch("web_console.time.sleep", return_value=None):
            result = console.cancel(
                {
                    "user_key": "user_1",
                    "bill_num": BILL_NUM,
                    "confirmation": "CANCEL",
                    "require_confirmed": True,
                }
            )

        self.assertTrue(result["confirmed"])
        self.assertTrue(result["reconciled"])
        self.assertEqual(len(client.posts), 1)

    def test_cancel_retries_until_third_reconciliation_check(self):
        client = FakeCancelClient(visibility_delay=2)
        console = make_console(client)

        with patch("web_console.time.sleep", return_value=None):
            result = console.cancel(
                {
                    "user_key": "user_1",
                    "bill_num": BILL_NUM,
                    "confirmation": "CANCEL",
                    "require_confirmed": True,
                }
            )

        self.assertTrue(result["confirmed"])
        self.assertEqual(result["reconciliation_checks"], 3)
        self.assertEqual(client.reconcile_get_count, 4)
        self.assertEqual(len(client.posts), 1)

    def test_cancel_preserves_upstream_failure_when_order_remains_active(self):
        client = FakeCancelClient(
            cancel_response={"msg": "fail", "data": "operation rejected"},
            apply_cancel=False,
        )
        console = make_console(client)

        with patch("web_console.time.sleep", return_value=None):
            with self.assertRaisesRegex(web_console.EasySerpError, "canclePlaceAppointment failed: operation rejected"):
                console.cancel(
                    {
                        "user_key": "user_1",
                        "bill_num": BILL_NUM,
                        "confirmation": "CANCEL",
                        "require_confirmed": True,
                    }
                )

        self.assertEqual(client.reconcile_get_count, 3)
        self.assertEqual(len(client.posts), 1)

    def test_cancel_preserves_failure_when_reconciliation_queries_fail(self):
        client = FakeCancelClient(
            cancel_response={"msg": "fail", "data": "fail"},
            reconcile_get_errors=3,
        )
        console = make_console(client)

        with patch("web_console.time.sleep", return_value=None):
            with self.assertRaisesRegex(web_console.EasySerpError, "canclePlaceAppointment failed: fail"):
                console.cancel(
                    {
                        "user_key": "user_1",
                        "bill_num": BILL_NUM,
                        "confirmation": "CANCEL",
                        "require_confirmed": True,
                    }
                )

        self.assertEqual(client.reconcile_get_count, 3)
        self.assertEqual(len(client.posts), 1)

    def test_confirmed_cancel_is_not_reclassified_when_balance_refresh_fails(self):
        client = FakeCancelClient(card_get_error=web_console.EasySerpError("card refresh unavailable"))
        console = make_console(client)

        with patch("web_console.time.sleep", return_value=None):
            result = console.cancel(
                {
                    "user_key": "user_1",
                    "bill_num": BILL_NUM,
                    "confirmation": "CANCEL",
                    "require_confirmed": True,
                }
            )

        self.assertTrue(result["confirmed"])
        self.assertEqual(result["refresh_errors"], ["cards"])
        self.assertEqual(len(client.posts), 1)


if __name__ == "__main__":
    unittest.main()
