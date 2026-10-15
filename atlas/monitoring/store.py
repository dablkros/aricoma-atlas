"""Persistent secret-free jobs and process-safe locks on the appliance filesystem."""
from contextlib import contextmanager
import fcntl
import json
import os
from pathlib import Path
import sqlite3
import time
import uuid

from atlas.monitoring.models import MonitoringError

TERMINAL = {"COMPLETED", "FAILED", "PARTIAL"}


class JobStore:
    def __init__(self, directory):
        self.directory = Path(directory)

    def connect(self):
        if self.directory.is_symlink():
            raise MonitoringError("unsafe_job_directory", 503)
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        path = self.directory / "jobs.sqlite3"
        if path.is_symlink():
            raise MonitoringError("unsafe_job_database", 503)
        connection = sqlite3.connect(path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("CREATE TABLE IF NOT EXISTS jobs (id TEXT PRIMARY KEY, device_id INTEGER, body TEXT NOT NULL)")
        connection.execute("CREATE TABLE IF NOT EXISTS plans (id TEXT PRIMARY KEY, body TEXT NOT NULL)")
        connection.commit()
        os.chmod(path, 0o600)
        return connection

    @contextmanager
    def database(self):
        connection = self.connect()
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    @contextmanager
    def lock(self, key):
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        # Keys are generated internally from validated integer device IDs or 'zabbix'.
        path = self.directory / f"{key}.lock"
        fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise MonitoringError("operation_already_running", 409) from None
            yield
        finally:
            os.close(fd)

    def plan(self, value):
        identifier = str(uuid.uuid4())
        value = {**value, "created_at": time.time()}
        with self.database() as db:
            db.execute("INSERT INTO plans VALUES (?, ?)", (identifier, json.dumps(value)))
            db.execute("DELETE FROM plans WHERE json_extract(body, '$.created_at') < ?", (time.time() - 3600,))
        return identifier

    def get_plan(self, identifier):
        with self.database() as db:
            row = db.execute("SELECT body FROM plans WHERE id=?", (identifier,)).fetchone()
        if not row:
            raise MonitoringError("plan_not_found", 404)
        value = json.loads(row[0])
        if time.time() - value["created_at"] > 900:
            raise MonitoringError("plan_expired", 409)
        return value

    def create(self, device_id, mode):
        identifier = str(uuid.uuid4())
        value = dict(job_id=identifier, device_id=device_id, mode=mode, status="PENDING",
                     current_step="PENDING", started_at=time.time(), finished_at=None,
                     changes=[], error_code=None, error_message=None,
                     device_configuration="PENDING", zabbix_registration="PENDING",
                     verification="PENDING", mutation_attempted=False)
        with self.database() as db:
            db.execute("INSERT INTO jobs VALUES (?, ?, ?)", (identifier, device_id, json.dumps(value)))
        return value

    def update(self, job, **values):
        job.update(values)
        with self.database() as db:
            db.execute("UPDATE jobs SET body=? WHERE id=?", (json.dumps(job), job["job_id"]))

    def get(self, identifier):
        with self.database() as db:
            row = db.execute("SELECT body FROM jobs WHERE id=?", (identifier,)).fetchone()
        if not row:
            raise MonitoringError("job_not_found", 404)
        return json.loads(row[0])

    def recover(self):
        with self.database() as db:
            jobs = [json.loads(r[0]) for r in db.execute("SELECT body FROM jobs")]
        for job in jobs:
            if job["status"] in TERMINAL:
                continue
            try:
                with self.lock(f"device-{job['device_id']}"):
                    self.update(job, status="PARTIAL", current_step="INTERRUPTED", finished_at=time.time(),
                                error_code="interrupted_replan_required", error_message="Interrupted; inspect and plan before retry")
            except MonitoringError:
                pass  # Another live worker owns this job.
