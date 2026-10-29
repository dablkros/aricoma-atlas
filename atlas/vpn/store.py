"""Process-safe SQLite persistence for secret-free VPN connection metadata."""

from contextlib import contextmanager
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sqlite3
import uuid

from atlas.vpn.models import VPNConnectionCreate, VPNConnectionRecord


class VPNStoreError(RuntimeError):
    code = "vpn_registry_unavailable"


class VPNConnectionNotFound(VPNStoreError):
    code = "connection_not_found"


class VPNConnectionConflict(VPNStoreError):
    code = "connection_id_conflict"


class SQLiteVPNConnectionStore:
    SCHEMA_VERSION = 1

    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    def _connect(self) -> sqlite3.Connection:
        connection = None
        try:
            if self.path.is_symlink() or self.path.parent.is_symlink():
                raise VPNStoreError()
            self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            os.chmod(self.path.parent, 0o700)
            if self.path.exists() and not self.path.is_file():
                raise VPNStoreError()
            connection = sqlite3.connect(self.path, timeout=10)
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute("PRAGMA busy_timeout = 10000")
            self._migrate(connection)
            os.chmod(self.path, 0o600)
            return connection
        except VPNStoreError:
            if connection is not None:
                connection.close()
            raise
        except (OSError, sqlite3.Error) as exc:
            if connection is not None:
                connection.close()
            raise VPNStoreError() from exc

    @contextmanager
    def database(self):
        connection = self._connect()
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def _migrate(self, connection: sqlite3.Connection) -> None:
        connection.execute(
            "CREATE TABLE IF NOT EXISTS vpn_registry_migrations "
            "(version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)"
        )
        versions = {
            row[0]
            for row in connection.execute(
                "SELECT version FROM vpn_registry_migrations ORDER BY version"
            )
        }
        if any(version > self.SCHEMA_VERSION for version in versions):
            raise VPNStoreError()
        if 1 not in versions:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS vpn_connections (
                    id TEXT PRIMARY KEY,
                    connection_id TEXT NOT NULL UNIQUE,
                    name TEXT NOT NULL,
                    tenant_id INTEGER NOT NULL CHECK (tenant_id > 0),
                    vendor TEXT NOT NULL,
                    protocol TEXT NOT NULL,
                    driver TEXT NOT NULL,
                    gateway_host TEXT NOT NULL,
                    gateway_port INTEGER,
                    auth_method TEXT NOT NULL,
                    secret_ref TEXT NOT NULL,
                    management_vrf_id INTEGER NOT NULL CHECK (management_vrf_id > 0),
                    allowed_prefixes_json TEXT NOT NULL,
                    administrative_status TEXT NOT NULL
                        CHECK (administrative_status IN ('planned', 'enabled', 'disabled')),
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                "CREATE INDEX vpn_connections_tenant_idx "
                "ON vpn_connections (tenant_id, connection_id)"
            )
            connection.execute(
                "INSERT OR IGNORE INTO vpn_registry_migrations "
                "(version, applied_at) VALUES (?, ?)",
                (1, datetime.now(timezone.utc).isoformat()),
            )
        connection.commit()

    @staticmethod
    def _record(row: sqlite3.Row, tenant_slug: str) -> VPNConnectionRecord:
        try:
            prefixes = json.loads(row["allowed_prefixes_json"])
            return VPNConnectionRecord(
                id=row["id"],
                connection_id=row["connection_id"],
                name=row["name"],
                tenant_id=row["tenant_id"],
                tenant_slug=tenant_slug,
                vendor=row["vendor"],
                protocol=row["protocol"],
                driver=row["driver"],
                gateway_host=row["gateway_host"],
                gateway_port=row["gateway_port"],
                auth_method=row["auth_method"],
                secret_ref=row["secret_ref"],
                management_vrf_id=row["management_vrf_id"],
                allowed_prefixes=prefixes,
                administrative_status=row["administrative_status"],
                created_at=row["created_at"],
                updated_at=row["updated_at"],
            )
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise VPNStoreError() from exc

    def create(self, value: VPNConnectionCreate, tenant_slug: str) -> VPNConnectionRecord:
        now = datetime.now(timezone.utc).isoformat()
        identifier = str(uuid.uuid4())
        fields = value.model_dump(mode="json")
        try:
            with self.database() as database:
                database.execute(
                    """
                    INSERT INTO vpn_connections (
                        id, connection_id, name, tenant_id, vendor, protocol,
                        driver, gateway_host, gateway_port, auth_method,
                        secret_ref, management_vrf_id, allowed_prefixes_json,
                        administrative_status, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        identifier, fields["connection_id"], fields["name"],
                        fields["tenant_id"], fields["vendor"], fields["protocol"],
                        fields["driver"], fields["gateway_host"], fields["gateway_port"],
                        fields["auth_method"], fields["secret_ref"],
                        fields["management_vrf_id"],
                        json.dumps(fields["allowed_prefixes"], separators=(",", ":")),
                        fields["administrative_status"], now, now,
                    ),
                )
                row = database.execute(
                    "SELECT * FROM vpn_connections WHERE id = ?", (identifier,)
                ).fetchone()
        except sqlite3.IntegrityError as exc:
            if "connection_id" in str(exc):
                raise VPNConnectionConflict() from None
            raise VPNStoreError() from exc
        except (OSError, sqlite3.Error) as exc:
            raise VPNStoreError() from exc
        if row is None:
            raise VPNStoreError()
        return self._record(row, tenant_slug)

    def raw(self, connection_id: str) -> dict:
        try:
            with self.database() as database:
                row = database.execute(
                    "SELECT * FROM vpn_connections WHERE connection_id = ?",
                    (connection_id,),
                ).fetchone()
        except (OSError, sqlite3.Error) as exc:
            raise VPNStoreError() from exc
        if row is None:
            raise VPNConnectionNotFound()
        value = dict(row)
        try:
            value["allowed_prefixes"] = json.loads(value.pop("allowed_prefixes_json"))
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise VPNStoreError() from exc
        return value

    def list_raw(
        self, *, tenant_id: int | None, limit: int, offset: int
    ) -> tuple[list[dict], int]:
        where = " WHERE tenant_id = ?" if tenant_id is not None else ""
        parameters = (tenant_id,) if tenant_id is not None else ()
        try:
            with self.database() as database:
                total = database.execute(
                    f"SELECT COUNT(*) FROM vpn_connections{where}", parameters
                ).fetchone()[0]
                rows = database.execute(
                    f"SELECT * FROM vpn_connections{where} "
                    "ORDER BY connection_id LIMIT ? OFFSET ?",
                    (*parameters, limit, offset),
                ).fetchall()
        except (OSError, sqlite3.Error) as exc:
            raise VPNStoreError() from exc
        values = []
        for row in rows:
            value = dict(row)
            try:
                value["allowed_prefixes"] = json.loads(
                    value.pop("allowed_prefixes_json")
                )
            except (TypeError, ValueError, json.JSONDecodeError) as exc:
                raise VPNStoreError() from exc
            values.append(value)
        return values, total

    def update(
        self, value: VPNConnectionCreate, tenant_slug: str
    ) -> VPNConnectionRecord:
        fields = value.model_dump(mode="json")
        now = datetime.now(timezone.utc).isoformat()
        try:
            with self.database() as database:
                cursor = database.execute(
                    """
                    UPDATE vpn_connections SET
                        name = ?, vendor = ?, protocol = ?, driver = ?,
                        gateway_host = ?, gateway_port = ?, auth_method = ?,
                        secret_ref = ?, management_vrf_id = ?,
                        allowed_prefixes_json = ?, administrative_status = ?,
                        updated_at = ?
                    WHERE connection_id = ?
                    """,
                    (
                        fields["name"], fields["vendor"], fields["protocol"],
                        fields["driver"], fields["gateway_host"], fields["gateway_port"],
                        fields["auth_method"], fields["secret_ref"],
                        fields["management_vrf_id"],
                        json.dumps(fields["allowed_prefixes"], separators=(",", ":")),
                        fields["administrative_status"], now, fields["connection_id"],
                    ),
                )
                if cursor.rowcount != 1:
                    raise VPNConnectionNotFound()
                row = database.execute(
                    "SELECT * FROM vpn_connections WHERE connection_id = ?",
                    (fields["connection_id"],),
                ).fetchone()
        except VPNConnectionNotFound:
            raise
        except (OSError, sqlite3.Error) as exc:
            raise VPNStoreError() from exc
        if row is None:
            raise VPNStoreError()
        return self._record(row, tenant_slug)


__all__ = [
    "SQLiteVPNConnectionStore",
    "VPNConnectionConflict",
    "VPNConnectionNotFound",
    "VPNStoreError",
]
