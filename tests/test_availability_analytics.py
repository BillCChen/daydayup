import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import availability_analytics


TZ = ZoneInfo("Asia/Shanghai")


def create_db_path(tmpdir: str) -> Path:
    return Path(tmpdir) / "availability.sqlite3"


def prepare_db(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(path)
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS availability_observations(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            planned_node_at TEXT NOT NULL,
            observed_at TEXT NOT NULL,
            target_date TEXT NOT NULL,
            court_number INTEGER NOT NULL,
            court_id TEXT NOT NULL,
            court_name TEXT NOT NULL,
            start_time TEXT NOT NULL,
            end_time TEXT NOT NULL,
            is_bookable INTEGER NOT NULL,
            price_value REAL,
            pay_value REAL,
            raw_state TEXT NOT NULL DEFAULT '',
            source_present INTEGER NOT NULL DEFAULT 0,
            UNIQUE(planned_node_at, target_date, court_number, start_time)
        )
        """
    )
    connection.execute("CREATE INDEX IF NOT EXISTS idx_availability_observations_target ON availability_observations(target_date, court_number, start_time)")
    connection.execute("CREATE INDEX IF NOT EXISTS idx_availability_observations_node ON availability_observations(planned_node_at)")
    connection.commit()
    return connection


def insert_observation(
    connection: sqlite3.Connection,
    *,
    planned_at: datetime,
    observed_at: datetime,
    target_date: str,
    court_number: int,
    start_time: str,
    is_bookable: int,
) -> None:
    end_time = f"{(int(start_time[:2]) + 1) % 24:02d}:{start_time[3:]}"
    connection.execute(
        """
        INSERT INTO availability_observations(
            planned_node_at, observed_at, target_date, court_number, court_id, court_name,
            start_time, end_time, is_bookable, price_value, pay_value, raw_state, source_present
        ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(planned_node_at, target_date, court_number, start_time) DO UPDATE SET
            observed_at = excluded.observed_at,
            court_id = excluded.court_id,
            court_name = excluded.court_name,
            end_time = excluded.end_time,
            is_bookable = excluded.is_bookable,
            price_value = excluded.price_value,
            pay_value = excluded.pay_value,
            raw_state = excluded.raw_state,
            source_present = excluded.source_present
        """,
        (
            planned_at.isoformat(),
            observed_at.isoformat(),
            target_date,
            court_number,
            f"ymq{court_number}",
            f"Badminton {court_number}",
            start_time,
            end_time,
            is_bookable,
            None,
            None,
            "",
            1,
        ),
    )


def find_row(rows, slot):
    for row in rows:
        if row["slot"] == slot:
            return row
    raise AssertionError(f"missing row for slot {slot}")


class AvailabilityAnalyticsTest(unittest.TestCase):
    def test_analytics_hour_court(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = create_db_path(tmpdir)
            cache_path = Path(tmpdir) / "analytics_cache.sqlite3"
            connection = prepare_db(db_path)
            now = datetime(2026, 5, 24, 12, 0, tzinfo=TZ)
            insert_observation(
                connection,
                planned_at=now - timedelta(minutes=10),
                observed_at=now - timedelta(minutes=10),
                target_date="2026-05-24",
                court_number=1,
                start_time="08:00",
                is_bookable=1,
            )
            insert_observation(
                connection,
                planned_at=now - timedelta(minutes=9),
                observed_at=now - timedelta(minutes=9),
                target_date="2026-05-24",
                court_number=2,
                start_time="08:00",
                is_bookable=1,
            )
            insert_observation(
                connection,
                planned_at=now - timedelta(minutes=8),
                observed_at=now - timedelta(minutes=8),
                target_date="2026-05-24",
                court_number=1,
                start_time="09:00",
                is_bookable=1,
            )
            insert_observation(
                connection,
                planned_at=now - timedelta(minutes=7),
                observed_at=now - timedelta(minutes=7),
                target_date="2026-05-24",
                court_number=2,
                start_time="09:00",
                is_bookable=0,
            )
            connection.commit()
            result = availability_analytics.get_hour_court(
                availability_db_path=db_path,
                cache_db_path=cache_path,
                window_days=1,
                start_hour=8,
                end_hour=9,
                courts=[1, 2],
                now=now,
            )
            row_8 = find_row(result["rows"], "08:00")
            row_9 = find_row(result["rows"], "09:00")
            self.assertEqual(row_8["courts"][0]["hours"], 0.5)
            self.assertEqual(row_8["courts"][1]["hours"], 0.5)
            self.assertEqual(row_9["courts"][0]["hours"], 0.5)
            self.assertEqual(row_9["courts"][1]["hours"], 0.0)

    def test_analytics_court_day(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = create_db_path(tmpdir)
            cache_path = Path(tmpdir) / "analytics_cache.sqlite3"
            connection = prepare_db(db_path)
            now = datetime(2026, 5, 24, 12, 0, tzinfo=TZ)
            observations = [
                ("2026-05-22", "08:00", 1, 1),
                ("2026-05-23", "08:00", 1, 1),
                ("2026-05-23", "09:00", 1, 1),
            ]
            for index, (date_value, start_time, court, is_bookable) in enumerate(observations, start=1):
                observed_at = now - timedelta(hours=index, minutes=10)
                insert_observation(
                    connection,
                    planned_at=observed_at,
                    observed_at=observed_at,
                    target_date=date_value,
                    court_number=court,
                    start_time=start_time,
                    is_bookable=is_bookable,
                )
            connection.commit()
            result = availability_analytics.get_court_day(
                availability_db_path=db_path,
                cache_db_path=cache_path,
                window_days=3,
                courts=[1],
                now=now,
            )
            court_rows = result["rows"]
            self.assertEqual(len(court_rows), 1)
            days = court_rows[0]["days"]
            self.assertEqual(days[0]["date"], "2026-05-22")
            self.assertEqual(days[0]["hours"], 0.5)
            self.assertFalse(days[0]["is_weekend"])
            self.assertEqual(days[1]["date"], "2026-05-23")
            self.assertEqual(days[1]["hours"], 1.0)
            self.assertTrue(days[1]["is_weekend"])
            self.assertEqual(days[2]["date"], "2026-05-24")
            self.assertEqual(days[2]["hours"], 0.0)

    def test_analytics_timeseries(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = create_db_path(tmpdir)
            cache_path = Path(tmpdir) / "analytics_cache.sqlite3"
            connection = prepare_db(db_path)
            now = datetime(2026, 5, 24, 14, 0, tzinfo=TZ)
            observations = [
                (now - timedelta(hours=3), 1, "08:00", 1),
                (now - timedelta(hours=2), 1, "08:00", 1),
                (now - timedelta(hours=1), 1, "08:00", 1),
                (now - timedelta(hours=1), 2, "08:00", 1),
            ]
            for observed_at, court, start_time, is_bookable in observations:
                insert_observation(
                    connection,
                    planned_at=observed_at,
                    observed_at=observed_at,
                    target_date=observed_at.strftime("%Y-%m-%d"),
                    court_number=court,
                    start_time=start_time,
                    is_bookable=is_bookable,
                )
            connection.commit()
            result = availability_analytics.get_timeseries(
                availability_db_path=db_path,
                cache_db_path=cache_path,
                window_days=1,
                start_hour=8,
                end_hour=22,
                courts=[1, 2],
                now=now,
            )
            points = result["points"]
            self.assertEqual(len(points), 2)
            self.assertEqual(points[0]["hours"], 0.5)
            self.assertEqual(points[1]["hours"], 1.0)

    def test_cache_hit(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = create_db_path(tmpdir)
            cache_path = Path(tmpdir) / "analytics_cache.sqlite3"
            connection = prepare_db(db_path)
            now = datetime(2026, 5, 24, 12, 0, tzinfo=TZ)
            insert_observation(
                connection,
                planned_at=now - timedelta(minutes=5),
                observed_at=now - timedelta(minutes=5),
                target_date="2026-05-24",
                court_number=1,
                start_time="08:00",
                is_bookable=1,
            )
            connection.commit()
            first = availability_analytics.get_hour_court(
                availability_db_path=db_path,
                cache_db_path=cache_path,
                now=now,
                cache_ttl_seconds=600,
            )
            second = availability_analytics.get_hour_court(
                availability_db_path=db_path,
                cache_db_path=cache_path,
                now=now,
                cache_ttl_seconds=600,
            )
            self.assertFalse(first["cache"]["hit"])
            self.assertTrue(second["cache"]["hit"])
            self.assertEqual(first["cache"]["generated_at"], second["cache"]["generated_at"])

    def test_cache_stale(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = create_db_path(tmpdir)
            cache_path = Path(tmpdir) / "analytics_cache.sqlite3"
            connection = prepare_db(db_path)
            now = datetime.fromtimestamp(1000, tz=TZ)
            insert_observation(
                connection,
                planned_at=now - timedelta(minutes=5),
                observed_at=now - timedelta(minutes=5),
                target_date="2026-05-24",
                court_number=1,
                start_time="08:00",
                is_bookable=1,
            )
            connection.commit()
            first = availability_analytics.get_hour_court(
                availability_db_path=db_path,
                cache_db_path=cache_path,
                now=now,
                cache_ttl_seconds=1,
            )
            second = availability_analytics.get_hour_court(
                availability_db_path=db_path,
                cache_db_path=cache_path,
                now=datetime.fromtimestamp(1003, tz=TZ),
                cache_ttl_seconds=1,
            )
            self.assertEqual(first["rows"][0]["courts"][0]["hours"], 0.5)
            self.assertGreaterEqual(second["cache"]["generated_at"], first["cache"]["generated_at"] + 1)

    def test_signature_invalidation(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = create_db_path(tmpdir)
            cache_path = Path(tmpdir) / "analytics_cache.sqlite3"
            connection = prepare_db(db_path)
            now = datetime(2026, 5, 24, 12, 0, tzinfo=TZ)
            insert_observation(
                connection,
                planned_at=now - timedelta(minutes=5),
                observed_at=now - timedelta(minutes=5),
                target_date="2026-05-24",
                court_number=1,
                start_time="08:00",
                is_bookable=1,
            )
            connection.commit()
            first = availability_analytics.get_hour_court(
                availability_db_path=db_path,
                cache_db_path=cache_path,
                now=now,
                cache_ttl_seconds=600,
            )
            insert_observation(
                connection,
                planned_at=now - timedelta(minutes=4),
                observed_at=now - timedelta(minutes=4),
                target_date="2026-05-24",
                court_number=1,
                start_time="09:00",
                is_bookable=1,
            )
            connection.commit()
            second = availability_analytics.get_hour_court(
                availability_db_path=db_path,
                cache_db_path=cache_path,
                now=now,
                cache_ttl_seconds=600,
            )
            self.assertNotEqual(first["source_signature"], second["source_signature"])
            self.assertFalse(second["cache"]["hit"])
            self.assertEqual(find_row(second["rows"], "08:00")["courts"][0]["hours"], 0.5)
            self.assertEqual(find_row(second["rows"], "09:00")["courts"][0]["hours"], 0.5)


if __name__ == "__main__":
    unittest.main()
