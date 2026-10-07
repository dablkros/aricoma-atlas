"""Persistent, secret-free local result storage for Profylaxia."""

import json
import os
from pathlib import Path
import sqlite3

from atlas.prophylaxis.models import (
    CPUUtilizationValues,
    CheckId,
    CheckResult,
    StoredCheckResult,
)


class ResultStoreError(RuntimeError):
    code = "result_store_unavailable"


class SQLiteResultStore:
    """Store normalized results in a VM-local SQLite database."""

    def __init__(self, path: Path, max_results: int = 10_000) -> None:
        self.path = Path(path)
        if isinstance(max_results, bool) or not 100 <= max_results <= 1_000_000:
            raise ValueError("max_results must be between 100 and 1000000")
        self.max_results = max_results

    def _connect(self) -> sqlite3.Connection:
        try:
            if self.path.is_symlink() or self.path.parent.is_symlink():
                raise ResultStoreError()
            self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            if self.path.exists() and not self.path.is_file():
                raise ResultStoreError()
            connection = sqlite3.connect(self.path, timeout=5)
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS check_results (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    schema_version INTEGER NOT NULL,
                    collected_at TEXT NOT NULL,
                    device_id INTEGER,
                    device TEXT NOT NULL,
                    platform TEXT,
                    check_id TEXT NOT NULL,
                    status TEXT NOT NULL
                        CHECK (status IN ('ok', 'error', 'unsupported')),
                    values_json TEXT,
                    error_code TEXT
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS result_store_migrations (
                    name TEXT PRIMARY KEY
                )
                """
            )
            self._migrate_legacy(connection)
            connection.commit()
            os.chmod(self.path, 0o600)
            return connection
        except ResultStoreError:
            raise
        except (OSError, sqlite3.Error) as exc:
            raise ResultStoreError() from exc

    @staticmethod
    def _migrate_legacy(connection: sqlite3.Connection) -> None:
        migration_name = "cpu_check_results_to_check_results_v1"
        migrated = connection.execute(
            "SELECT 1 FROM result_store_migrations WHERE name = ?",
            (migration_name,),
        ).fetchone()
        if migrated is not None:
            return
        legacy = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'cpu_check_results'"
        ).fetchone()
        if legacy is None:
            connection.execute(
                "INSERT INTO result_store_migrations (name) VALUES (?)",
                (migration_name,),
            )
            return
        existing = connection.execute("SELECT COUNT(*) FROM check_results").fetchone()[0]
        if existing:
            connection.execute(
                "INSERT INTO result_store_migrations (name) VALUES (?)",
                (migration_name,),
            )
            return
        for row in connection.execute("SELECT * FROM cpu_check_results ORDER BY id"):
            values = None
            if row["status"] == "ok":
                values = {
                    "current_percent": row["current_percent"],
                    "one_minute_percent": row["one_minute_percent"],
                    "five_minute_percent": row["five_minute_percent"],
                }
            connection.execute(
                """
                INSERT INTO check_results (
                    id, schema_version, collected_at, device_id, device,
                    platform, check_id, status, values_json, error_code
                ) VALUES (?, 1, ?, NULL, ?, ?, ?, ?, ?, ?)
                """,
                (
                    row["id"],
                    row["collected_at"],
                    row["device"],
                    row["platform"],
                    CheckId.CPU_UTILIZATION.value,
                    row["status"],
                    json.dumps(values) if values is not None else None,
                    row["error_code"],
                ),
            )
        connection.execute(
            "INSERT INTO result_store_migrations (name) VALUES (?)",
            (migration_name,),
        )

    @staticmethod
    def _stored(row: sqlite3.Row) -> StoredCheckResult:
        values = None
        if row["values_json"] is not None:
            decoded = json.loads(row["values_json"])
            values = CPUUtilizationValues.model_validate(decoded)
        return StoredCheckResult(
            id=row["id"],
            schema_version=row["schema_version"],
            collected_at=row["collected_at"],
            device_id=row["device_id"],
            device=row["device"],
            platform=row["platform"],
            check=row["check_id"],
            status=row["status"],
            values=values,
            error_code=row["error_code"],
        )

    def save(self, result: CheckResult) -> StoredCheckResult:
        values_json = (
            json.dumps(result.values.model_dump(mode="json"), sort_keys=True)
            if result.values is not None
            else None
        )
        try:
            with self._connect() as connection:
                cursor = connection.execute(
                    """
                    INSERT INTO check_results (
                        schema_version, collected_at, device_id, device,
                        platform, check_id, status, values_json, error_code
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        result.schema_version,
                        result.collected_at.isoformat(),
                        result.device_id,
                        result.device,
                        result.platform,
                        result.check.value,
                        result.status,
                        values_json,
                        result.error_code,
                    ),
                )
                row = connection.execute(
                    "SELECT * FROM check_results WHERE id = ?",
                    (cursor.lastrowid,),
                ).fetchone()
                connection.execute(
                    """
                    DELETE FROM check_results
                    WHERE id NOT IN (
                        SELECT id FROM check_results ORDER BY id DESC LIMIT ?
                    )
                    """,
                    (self.max_results,),
                )
        except (OSError, sqlite3.Error) as exc:
            raise ResultStoreError() from exc
        if row is None:
            raise ResultStoreError()
        try:
            return self._stored(row)
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise ResultStoreError() from exc

    def list(self, limit: int = 20) -> list[StoredCheckResult]:
        if isinstance(limit, bool) or not 1 <= limit <= 100:
            raise ValueError("limit must be between 1 and 100")
        try:
            with self._connect() as connection:
                rows = connection.execute(
                    "SELECT * FROM check_results ORDER BY id DESC LIMIT ?",
                    (limit,),
                ).fetchall()
        except (OSError, sqlite3.Error) as exc:
            raise ResultStoreError() from exc
        try:
            return [self._stored(row) for row in rows]
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise ResultStoreError() from exc


__all__ = ["ResultStoreError", "SQLiteResultStore"]
