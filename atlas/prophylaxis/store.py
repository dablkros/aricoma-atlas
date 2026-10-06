"""Persistent, secret-free local result storage for Profylaxia."""

from datetime import datetime, timezone
import os
from pathlib import Path
import sqlite3

from atlas.prophylaxis.models import (
    CPUCheckResult,
    CPUUtilizationValues,
    StoredCPUCheckResult,
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
                CREATE TABLE IF NOT EXISTS cpu_check_results (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    collected_at TEXT NOT NULL,
                    device TEXT NOT NULL,
                    platform TEXT,
                    check_name TEXT NOT NULL,
                    status TEXT NOT NULL CHECK (status IN ('ok', 'error')),
                    current_percent REAL,
                    one_minute_percent REAL,
                    five_minute_percent REAL,
                    error_code TEXT
                )
                """
            )
            connection.commit()
            os.chmod(self.path, 0o600)
            return connection
        except (OSError, sqlite3.Error) as exc:
            raise ResultStoreError() from exc

    @staticmethod
    def _stored(row: sqlite3.Row) -> StoredCPUCheckResult:
        values = None
        if row["status"] == "ok":
            values = CPUUtilizationValues(
                current_percent=row["current_percent"],
                one_minute_percent=row["one_minute_percent"],
                five_minute_percent=row["five_minute_percent"],
            )
        return StoredCPUCheckResult(
            id=row["id"],
            collected_at=datetime.fromisoformat(row["collected_at"]),
            device=row["device"],
            platform=row["platform"],
            check=row["check_name"],
            status=row["status"],
            values=values,
            error=row["error_code"],
        )

    def save(self, result: CPUCheckResult) -> StoredCPUCheckResult:
        collected_at = datetime.now(timezone.utc)
        values = result.values
        try:
            with self._connect() as connection:
                cursor = connection.execute(
                    """
                    INSERT INTO cpu_check_results (
                        collected_at, device, platform, check_name, status,
                        current_percent, one_minute_percent,
                        five_minute_percent, error_code
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        collected_at.isoformat(),
                        result.device,
                        result.platform,
                        result.check,
                        result.status,
                        values.current_percent if values else None,
                        values.one_minute_percent if values else None,
                        values.five_minute_percent if values else None,
                        result.error,
                    ),
                )
                row = connection.execute(
                    "SELECT * FROM cpu_check_results WHERE id = ?",
                    (cursor.lastrowid,),
                ).fetchone()
                connection.execute(
                    """
                    DELETE FROM cpu_check_results
                    WHERE id NOT IN (
                        SELECT id FROM cpu_check_results
                        ORDER BY id DESC
                        LIMIT ?
                    )
                    """,
                    (self.max_results,),
                )
        except sqlite3.Error as exc:
            raise ResultStoreError() from exc
        if row is None:
            raise ResultStoreError()
        try:
            return self._stored(row)
        except (TypeError, ValueError) as exc:
            raise ResultStoreError() from exc

    def list(self, limit: int = 20) -> list[StoredCPUCheckResult]:
        if isinstance(limit, bool) or not 1 <= limit <= 100:
            raise ValueError("limit must be between 1 and 100")
        try:
            with self._connect() as connection:
                rows = connection.execute(
                    """
                    SELECT * FROM cpu_check_results
                    ORDER BY id DESC
                    LIMIT ?
                    """,
                    (limit,),
                ).fetchall()
        except sqlite3.Error as exc:
            raise ResultStoreError() from exc
        try:
            return [self._stored(row) for row in rows]
        except (TypeError, ValueError) as exc:
            raise ResultStoreError() from exc


__all__ = ["ResultStoreError", "SQLiteResultStore"]
