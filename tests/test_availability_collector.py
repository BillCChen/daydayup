import sqlite3
import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path

import availability_collector as collector
from easyserp_client import EasySerpError


class FakeClient:
    def __init__(self, places_by_date=None, failing_dates=None):
        self.places_by_date = places_by_date or {}
        self.failing_dates = set(failing_dates or [])
        self.calls = []

    def get(self, endpoint, params=None):
        params = params or {}
        target_date = params.get("dateymd")
        self.calls.append((endpoint, dict(params)))
        if target_date in self.failing_dates:
            raise EasySerpError(f"failed token=secret for {target_date}")
        return {
            "msg": "success",
            "data": {
                "placeArray": self.places_by_date.get(target_date, []),
            },
        }


def make_config(db_path):
    return collector.CollectorConfig(
        db_path=Path(db_path),
        token="token",
        jsessionid="session",
        shop_num="1001",
        base_url="https://example.invalid",
        timeout=1.0,
    )


def make_place(court_number=7, start_time="15:00", end_time="16:00", state=1, money=80.0):
    return {
        "projectName": {"shortname": f"ymq{court_number}", "name": f"Badminton {court_number}"},
        "projectInfo": [
            {
                "oldMoney": money,
                "money": money,
                "starttime": start_time,
                "endtime": end_time,
                "state": state,
            }
        ],
    }


def count_rows(db_path, sql, params=()):
    with sqlite3.connect(db_path) as connection:
        return connection.execute(sql, params).fetchone()[0]


class AvailabilityCollectorTest(unittest.TestCase):
    def test_query_node_calculation(self):
        tz = collector.COLLECTOR_TZ
        self.assertEqual(
            collector.next_query_node(datetime(2026, 5, 17, 0, 0, tzinfo=tz)),
            datetime(2026, 5, 17, 0, 15, tzinfo=tz),
        )
        self.assertEqual(
            collector.next_query_node(datetime(2026, 5, 17, 0, 15, 1, tzinfo=tz)),
            datetime(2026, 5, 17, 0, 45, tzinfo=tz),
        )
        self.assertEqual(
            collector.current_query_node(datetime(2026, 5, 17, 0, 14, tzinfo=tz)),
            datetime(2026, 5, 16, 23, 45, tzinfo=tz),
        )
        self.assertEqual(
            collector.current_query_node(datetime(2026, 5, 17, 0, 45, tzinfo=tz)),
            datetime(2026, 5, 17, 0, 45, tzinfo=tz),
        )

    def test_target_date_range(self):
        self.assertEqual(
            collector.target_date_values(date(2026, 5, 17)),
            ["2026-05-17", "2026-05-18", "2026-05-19", "2026-05-20", "2026-05-21", "2026-05-22"],
        )

    def test_collect_once_writes_full_matrix_and_schema(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "availability.sqlite3"
            planned = datetime(2026, 5, 17, 0, 15, tzinfo=collector.COLLECTOR_TZ)
            summary = collector.collect_once(
                make_config(db_path),
                planned_node_at=planned,
                today=date(2026, 5, 17),
                client=FakeClient(),
            )

            self.assertEqual(summary.status, "success")
            self.assertEqual(summary.observation_count, 1080)
            self.assertEqual(count_rows(db_path, "SELECT COUNT(*) FROM availability_observations"), 1080)
            self.assertEqual(
                count_rows(db_path, "SELECT COUNT(*) FROM availability_observations WHERE target_date = ?", ("2026-05-17",)),
                180,
            )
            with sqlite3.connect(db_path) as connection:
                self.assertEqual(connection.execute("SELECT value FROM schema_meta WHERE key = 'schema_version'").fetchone()[0], "1")
                self.assertEqual(connection.execute("PRAGMA journal_mode").fetchone()[0], "wal")
                self.assertEqual(connection.execute("PRAGMA busy_timeout").fetchone()[0], 5000)

    def test_bookable_and_missing_slots_are_recorded(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "availability.sqlite3"
            planned = datetime(2026, 5, 17, 0, 15, tzinfo=collector.COLLECTOR_TZ)
            places = {"2026-05-18": [make_place(court_number=7, start_time="15:00", end_time="16:00", money=80.0)]}
            collector.collect_once(
                make_config(db_path),
                planned_node_at=planned,
                today=date(2026, 5, 17),
                client=FakeClient(places_by_date=places),
            )

            with sqlite3.connect(db_path) as connection:
                available = connection.execute(
                    """
                    SELECT is_bookable, price_value, pay_value, source_present
                    FROM availability_observations
                    WHERE target_date = '2026-05-18' AND court_number = 7 AND start_time = '15:00'
                    """
                ).fetchone()
                missing = connection.execute(
                    """
                    SELECT is_bookable, pay_value, source_present
                    FROM availability_observations
                    WHERE target_date = '2026-05-18' AND court_number = 8 AND start_time = '15:00'
                    """
                ).fetchone()

            self.assertEqual(tuple(available), (1, 80.0, 20.0, 1))
            self.assertEqual(tuple(missing), (0, 20.0, 0))

    def test_rerun_same_node_is_idempotent(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "availability.sqlite3"
            planned = datetime(2026, 5, 17, 0, 15, tzinfo=collector.COLLECTOR_TZ)
            fake_client = FakeClient(places_by_date={"2026-05-18": [make_place()]})
            for _ in range(2):
                collector.collect_once(
                    make_config(db_path),
                    planned_node_at=planned,
                    today=date(2026, 5, 17),
                    client=fake_client,
                )

            self.assertEqual(count_rows(db_path, "SELECT COUNT(*) FROM collector_runs"), 1)
            self.assertEqual(count_rows(db_path, "SELECT COUNT(*) FROM availability_raw_responses"), 6)
            self.assertEqual(count_rows(db_path, "SELECT COUNT(*) FROM availability_observations"), 1080)

    def test_single_date_failure_records_partial_run(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "availability.sqlite3"
            planned = datetime(2026, 5, 17, 0, 15, tzinfo=collector.COLLECTOR_TZ)
            summary = collector.collect_once(
                make_config(db_path),
                planned_node_at=planned,
                today=date(2026, 5, 17),
                client=FakeClient(failing_dates={"2026-05-19"}),
            )

            self.assertEqual(summary.status, "partial")
            self.assertEqual(summary.success_count, 5)
            self.assertEqual(summary.failure_count, 1)
            self.assertEqual(summary.observation_count, 900)
            with sqlite3.connect(db_path) as connection:
                run = connection.execute("SELECT status, failure_count FROM collector_runs").fetchone()
                raw = connection.execute(
                    """
                    SELECT status, error_text
                    FROM availability_raw_responses
                    WHERE target_date = '2026-05-19'
                    """
                ).fetchone()
            self.assertEqual(tuple(run), ("partial", 1))
            self.assertEqual(raw[0], "failed")
            self.assertIn("<redacted>", raw[1])


if __name__ == "__main__":
    unittest.main()
