"""Local Atlas administrator authentication and short-lived sessions."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import re
import secrets
from threading import Lock
import time

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

from atlas.services.errors import DependencyError


USERNAME_PATTERN = re.compile(r"[a-z][a-z0-9_.-]{2,63}")
ROLE_ADMIN = "admin"
PASSWORD_HASHER = PasswordHasher()
DUMMY_PASSWORD_HASH = PASSWORD_HASHER.hash("atlas-invalid-login-placeholder")


class AuthenticationError(RuntimeError):
    """An authentication attempt failed without exposing the cause."""


class AuthenticationUnavailable(RuntimeError):
    """The administrator identity store cannot currently be queried."""


def normalize_username(value: str) -> str:
    username = value.strip().lower() if isinstance(value, str) else ""
    if not USERNAME_PATTERN.fullmatch(username):
        raise ValueError(
            "Administrator username must be 3-64 lowercase letters, digits, dots, "
            "underscores or hyphens and start with a letter"
        )
    return username


def validate_password(value: str) -> str:
    if not isinstance(value, str) or len(value) < 14:
        raise ValueError("Administrator password must contain at least 14 characters")
    if len(value) > 1024:
        raise ValueError("Administrator password is too long")
    return value


def hash_password(password: str) -> str:
    return PASSWORD_HASHER.hash(validate_password(password))


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return PASSWORD_HASHER.verify(password_hash, password)
    except (InvalidHashError, VerificationError, VerifyMismatchError, TypeError):
        return False


@dataclass(frozen=True)
class Principal:
    username: str
    role: str
    authentication: str = "session"


@dataclass
class _Session:
    principal: Principal
    csrf_token: str
    expires_at: float


class AdminSessionService:
    """Authenticate OpenBao-backed users and keep opaque sessions in memory."""

    def __init__(self, secret_reader, *, ttl_seconds: int = 900, clock=time.monotonic):
        self.secret_reader = secret_reader
        self.ttl_seconds = ttl_seconds
        self.clock = clock
        self._sessions: dict[str, _Session] = {}
        self._failures: dict[str, tuple[int, float]] = {}
        self._lock = Lock()

    @staticmethod
    def _session_key(token: str) -> str:
        return hashlib.sha256(token.encode("utf-8")).hexdigest()

    def login(self, username: str, password: str) -> tuple[str, str, Principal, int]:
        if self.secret_reader is None:
            raise AuthenticationUnavailable("Administrator authentication is unavailable")
        try:
            username = normalize_username(username)
        except ValueError:
            verify_password(DUMMY_PASSWORD_HASH, password)
            raise AuthenticationError("Invalid username or password") from None
        with self._lock:
            failures, blocked_until = self._failures.get(username, (0, 0.0))
            if blocked_until > self.clock():
                raise AuthenticationError("Invalid username or password")
        try:
            record = self.secret_reader.read_secret(f"auth/local/users/{username}")
        except DependencyError as exc:
            if exc.reason == "secret_not_found":
                verify_password(DUMMY_PASSWORD_HASH, password)
                self._record_failure(username)
                raise AuthenticationError("Invalid username or password") from None
            raise AuthenticationUnavailable("Administrator authentication is unavailable") from exc

        if not isinstance(record, dict):
            verify_password(DUMMY_PASSWORD_HASH, password)
            self._record_failure(username)
            raise AuthenticationError("Invalid username or password")
        stored_username = record.get("username")
        role = record.get("role")
        password_hash = record.get("password_hash")
        active = record.get("active")
        if (
            stored_username != username
            or role != ROLE_ADMIN
            or active is not True
            or not isinstance(password_hash, str)
            or not verify_password(password_hash, password)
        ):
            self._record_failure(username)
            raise AuthenticationError("Invalid username or password")

        token = secrets.token_urlsafe(48)
        csrf_token = secrets.token_urlsafe(32)
        principal = Principal(username=username, role=role)
        expires_at = self.clock() + self.ttl_seconds
        with self._lock:
            self._purge_locked()
            self._failures.pop(username, None)
            self._sessions[self._session_key(token)] = _Session(
                principal=principal,
                csrf_token=csrf_token,
                expires_at=expires_at,
            )
        return token, csrf_token, principal, self.ttl_seconds

    def _record_failure(self, username: str) -> None:
        with self._lock:
            count, blocked_until = self._failures.get(username, (0, 0.0))
            now = self.clock()
            if blocked_until <= now:
                blocked_until = 0.0
            count += 1
            if count >= 5:
                count = 0
                blocked_until = now + 300
            self._failures[username] = (count, blocked_until)

    def authenticate(self, token: str) -> tuple[Principal, str] | None:
        if not isinstance(token, str) or len(token) < 32:
            return None
        with self._lock:
            self._purge_locked()
            session = self._sessions.get(self._session_key(token))
            if session is None:
                return None
            return session.principal, session.csrf_token

    def logout(self, token: str) -> None:
        if isinstance(token, str) and token:
            with self._lock:
                self._sessions.pop(self._session_key(token), None)

    def _purge_locked(self) -> None:
        now = self.clock()
        expired = [key for key, value in self._sessions.items() if value.expires_at <= now]
        for key in expired:
            self._sessions.pop(key, None)


__all__ = [
    "AdminSessionService",
    "AuthenticationError",
    "AuthenticationUnavailable",
    "Principal",
    "ROLE_ADMIN",
    "hash_password",
    "normalize_username",
    "validate_password",
    "verify_password",
]
