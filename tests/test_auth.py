import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace

from fastapi.testclient import TestClient

from atlas.api.main import create_app
from atlas.auth import (
    AdminSessionService,
    AuthenticationError,
    hash_password,
    normalize_username,
    verify_password,
)
from atlas.config import Settings
from atlas.services.errors import DependencyError


class SecretReader:
    def __init__(self, records):
        self.records = records

    def read_secret(self, path):
        try:
            return self.records[path]
        except KeyError:
            raise DependencyError("secret_not_found") from None


class AtlasAuthenticationTests(unittest.TestCase):
    def test_argon2id_hash_round_trip_and_no_plaintext(self):
        password = "correct horse battery staple"
        password_hash = hash_password(password)

        self.assertTrue(password_hash.startswith("$argon2id$"))
        self.assertNotIn(password, password_hash)
        self.assertTrue(verify_password(password_hash, password))
        self.assertFalse(verify_password(password_hash, "wrong password value"))

    def test_login_returns_expiring_opaque_session(self):
        now = [100.0]
        reader = SecretReader(
            {
                "auth/local/users/admin": {
                    "username": "admin",
                    "role": "admin",
                    "active": True,
                    "password_hash": hash_password("correct horse battery staple"),
                }
            }
        )
        service = AdminSessionService(reader, ttl_seconds=300, clock=lambda: now[0])

        token, csrf, principal, ttl = service.login(
            "ADMIN", "correct horse battery staple"
        )

        self.assertEqual(principal.username, "admin")
        self.assertEqual(ttl, 300)
        self.assertGreaterEqual(len(token), 32)
        self.assertGreaterEqual(len(csrf), 32)
        self.assertEqual(service.authenticate(token)[0], principal)
        self.assertEqual(service.remaining_ttl(token), 300)
        now[0] = 401.0
        self.assertIsNone(service.authenticate(token))
        self.assertEqual(service.remaining_ttl(token), 0)

    def test_unknown_user_and_wrong_password_share_safe_error(self):
        service = AdminSessionService(SecretReader({}))
        with self.assertRaisesRegex(AuthenticationError, "Invalid username or password"):
            service.login("admin", "correct horse battery staple")

    def test_username_is_canonical_and_path_safe(self):
        self.assertEqual(normalize_username(" Admin.User "), "admin.user")
        for value in ("ab", "../admin", "admin/name", "1admin"):
            with self.assertRaises(ValueError):
                normalize_username(value)

    def test_browser_session_requires_csrf_for_logout(self):
        reader = SecretReader(
            {
                "auth/local/users/admin": {
                    "username": "admin",
                    "role": "admin",
                    "active": True,
                    "password_hash": hash_password("correct horse battery staple"),
                }
            }
        )
        with TemporaryDirectory() as directory:
            token_file = Path(directory) / "operator-token"
            token_file.write_text("x" * 40, encoding="utf-8")
            app = create_app(
                Settings(
                    environment="test",
                    monitoring_operator_token_file=token_file,
                ),
                dependencies=SimpleNamespace(openbao=reader, monitoring=None),
            )
            with TestClient(app) as client:
                login = client.post(
                    "/api/auth/login",
                    json={
                        "username": "admin",
                        "password": "correct horse battery staple",
                    },
                )
                self.assertEqual(login.status_code, 200)
                self.assertNotIn("access_token", login.json())
                current = client.get("/api/auth/me")
                self.assertEqual(current.status_code, 200)
                self.assertGreater(current.json()["expires_in"], 0)
                self.assertEqual(client.post("/api/auth/logout").status_code, 403)
                csrf = client.cookies.get("atlas_csrf")
                self.assertEqual(
                    client.post(
                        "/api/auth/logout", headers={"X-CSRF-Token": csrf}
                    ).status_code,
                    204,
                )


if __name__ == "__main__":
    unittest.main()
