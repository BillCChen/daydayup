from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from typing import Callable

import fcntl


def make_cache_key(metric: str, params: dict[str, Any]) -> str:
    payload = json.dumps(params, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return f"{metric}:{hashlib.sha256(payload.encode('utf-8')).hexdigest()}"


def _connect_cache_db(cache_db_path: Path) -> sqlite3.Connection:
    cache_db_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(str(cache_db_path))
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA busy_timeout = 5000")
    connection.execute("PRAGMA journal_mode = WAL")
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS analytics_cache(
            cache_key TEXT PRIMARY KEY,
            metric TEXT NOT NULL,
            params_json TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            source_signature TEXT NOT NULL,
            generated_at INTEGER NOT NULL,
            expires_at INTEGER NOT NULL,
            row_count INTEGER
        )
        """
    )
    connection.commit()
    return connection


def _read_cached(connection: sqlite3.Connection, cache_key: str) -> sqlite3.Row | None:
    return connection.execute(
        """
        SELECT metric, params_json, payload_json, source_signature, generated_at, expires_at, row_count
        FROM analytics_cache
        WHERE cache_key = ?
        """,
        (cache_key,),
    ).fetchone()


def _upsert_cached(
    connection: sqlite3.Connection,
    *,
    cache_key: str,
    metric: str,
    params_json: str,
    payload_json: str,
    source_signature: str,
    generated_at: int,
    expires_at: int,
    row_count: int | None,
) -> None:
    connection.execute(
        """
        INSERT INTO analytics_cache(
            cache_key, metric, params_json, payload_json, source_signature,
            generated_at, expires_at, row_count
        )
        VALUES(?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(cache_key) DO UPDATE SET
            metric = excluded.metric,
            params_json = excluded.params_json,
            payload_json = excluded.payload_json,
            source_signature = excluded.source_signature,
            generated_at = excluded.generated_at,
            expires_at = excluded.expires_at,
            row_count = excluded.row_count
        """,
        (cache_key, metric, params_json, payload_json, source_signature, generated_at, expires_at, row_count),
    )
    connection.commit()


@contextmanager
def _file_lock(lock_path: Path):
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    handle = lock_path.open("a+", encoding="utf-8")
    try:
        fcntl.flock(handle, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(handle, fcntl.LOCK_UN)
        handle.close()


def get_cached_payload(
    *,
    cache_db_path: Path,
    cache_key: str,
    metric: str,
    params: dict[str, Any],
    source_signature: str,
    ttl_seconds: int,
    compute_payload: Callable[[], dict[str, Any]],
    now: float | None = None,
) -> dict[str, Any]:
    params_json = json.dumps(params, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    now_ts = int(now if now is not None else time.time())
    with _connect_cache_db(cache_db_path) as connection:
        row = _read_cached(connection, cache_key)
        if row and int(row["expires_at"]) >= now_ts and row["source_signature"] == source_signature:
            payload = json.loads(row["payload_json"])
            payload["cache"] = {"hit": True, "generated_at": int(row["generated_at"])}
            return payload

    with _file_lock(cache_db_path.with_suffix(".lock")):
        with _connect_cache_db(cache_db_path) as connection:
            row = _read_cached(connection, cache_key)
            if row and int(row["expires_at"]) >= now_ts and row["source_signature"] == source_signature:
                payload = json.loads(row["payload_json"])
                payload["cache"] = {"hit": True, "generated_at": int(row["generated_at"])}
                return payload

            payload = compute_payload()
            generated_at = now_ts
            expires_at = now_ts + int(max(0, ttl_seconds))
            row_count = int(payload.get("observation_count", 0))
            _upsert_cached(
                connection,
                cache_key=cache_key,
                metric=metric,
                params_json=params_json,
                payload_json=json.dumps(payload, ensure_ascii=False),
                source_signature=source_signature,
                generated_at=generated_at,
                expires_at=expires_at,
                row_count=row_count,
            )
            payload["cache"] = {"hit": False, "generated_at": generated_at}
            return payload
