import io
import json
import os
import subprocess
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import Mock, patch

import yaml

from scripts import deploy_oxidized


ROOT = Path(__file__).resolve().parents[1]


def config():
    return {
        "runtime": {
            "directory": ".runtime/oxidized",
            "inventory_directory": "/run/atlas/oxidized",
            "compose_project": "atlas-oxidized",
            "startup_timeout": 60,
        },
        "oxidized": {
            "docker_image": "oxidized/oxidized:0.37.0",
            "interval": 3600,
            "threads": 30,
            "timeout": 20,
        },
    }


def device(name, address, enabled=True):
    return {
        "name": name,
        "platform": {"slug": "cisco-ios-xe"},
        "device_type": {"manufacturer": {"slug": "cisco"}},
        "primary_ip4": {"address": address},
        "custom_fields": {
            "oxidized_enabled": enabled,
            "credential_profile": "cisco-default",
        },
    }


class FakeOpenBao:
    def __init__(self, password='p:a"ss', enable="enable-secret"):
        self.secret = {
            "username": "admin",
            "password": password,
            "enable_password": enable,
        }

    def kv_read(self, token, path, mount="atlas"):
        self.last_request = (token, path, mount)
        return dict(self.secret)


class OxidizedRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.environment = patch.dict(
            os.environ,
            {"ATLAS_OXIDIZED_RUN_DIR": ""},
        )
        self.environment.start()

    def tearDown(self):
        self.environment.stop()

    def test_canonical_config_uses_jsonfile_strict_ssh_and_preserves_git(self):
        result = deploy_oxidized.canonical_oxidized_config(config()["oxidized"])

        self.assertTrue(result["input"]["ssh"]["secure"])
        self.assertFalse(result["output"]["clean_obsolete_nodes"])
        self.assertEqual(result["source"]["default"], "jsonfile")
        self.assertEqual(
            result["source"]["jsonfile"]["map"],
            {
                "name": "name",
                "ip": "ip",
                "model": "model",
                "username": "username",
                "password": "password",
            },
        )
        self.assertEqual(
            result["source"]["jsonfile"]["vars_map"],
            {"enable": "enable"},
        )

    def test_prepare_runtime_creates_ephemeral_json_and_persistent_trust(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            legacy = root / ".runtime/oxidized/router.db"
            legacy.parent.mkdir(parents=True)
            legacy.write_text("legacy:ios:user:password\n", encoding="utf-8")

            compose_file = deploy_oxidized.prepare_runtime(config(), root=root)
            source = deploy_oxidized.inventory_path(config(), root=root)
            known_hosts = root / ".runtime/oxidized/ssh/known_hosts"

            self.assertEqual(json.loads(source.read_text(encoding="utf-8")), [])
            self.assertEqual(source.stat().st_mode & 0o777, 0o644)
            self.assertEqual(source.parent.stat().st_mode & 0o777, 0o755)
            self.assertFalse(legacy.exists())
            self.assertTrue(known_hosts.exists())
            self.assertEqual(known_hosts.stat().st_mode & 0o777, 0o644)

            compose_data = yaml.safe_load(compose_file.read_text(encoding="utf-8"))
            volumes = compose_data["services"]["oxidized"]["volumes"]
            self.assertIn(
                f"{source.parent}:/etc/atlas-oxidized:ro",
                volumes,
            )
            self.assertTrue(
                any(":/home/oxidized/.ssh:ro" in volume for volume in volumes)
            )

            deploy_oxidized.prepare_runtime(config(), root=root)
            self.assertEqual(json.loads(source.read_text(encoding="utf-8")), [])

    def test_tmpfiles_recreates_secure_ephemeral_runtime_after_boot(self):
        content = (
            ROOT / "deployment/tmpfiles.d/aricoma-atlas.conf"
        ).read_text(encoding="utf-8")

        self.assertIn("d /run/atlas 0710 root atlas -", content)
        self.assertIn("d /run/atlas/oxidized 0755 atlas atlas -", content)
        self.assertIn(
            "f /run/atlas/oxidized/router.json 0644 atlas atlas - []",
            content,
        )

    def test_empty_inventory_and_special_characters_are_valid_json(self):
        self.assertEqual(deploy_oxidized.render_inventory([]), "[]\n")

        inventory = deploy_oxidized.build_inventory(
            [device("SW-CORE-01", "10.10.10.1/32")],
            FakeOpenBao(),
            "test-token",
        )
        rendered = deploy_oxidized.render_inventory(inventory)
        decoded = json.loads(rendered)

        self.assertEqual(decoded[0]["name"], "SW-CORE-01")
        self.assertEqual(decoded[0]["ip"], "10.10.10.1")
        self.assertEqual(decoded[0]["model"], "ios")
        self.assertEqual(decoded[0]["password"], 'p:a"ss')
        self.assertEqual(decoded[0]["enable"], "enable-secret")

    def test_add_and_remove_generate_the_complete_desired_inventory(self):
        bao = FakeOpenBao(enable="")
        sw1 = device("SW1", "192.0.2.1/32")
        sw2 = device("SW2", "192.0.2.2/32")
        sw3 = device("SW3", "192.0.2.3/32")

        before = deploy_oxidized.build_inventory([sw1, sw2], bao, "test-token")
        after_add = deploy_oxidized.build_inventory(
            [sw1, sw2, sw3], bao, "test-token"
        )
        after_remove = deploy_oxidized.build_inventory(
            [sw1, sw3], bao, "test-token"
        )

        self.assertEqual([entry["name"] for entry in before], ["SW1", "SW2"])
        self.assertEqual(
            [entry["name"] for entry in after_add],
            ["SW1", "SW2", "SW3"],
        )
        self.assertEqual(
            [entry["name"] for entry in after_remove],
            ["SW1", "SW3"],
        )

    def test_atomic_write_failure_preserves_previous_inventory(self):
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "router.json"
            original = [{
                "name": "SW1",
                "ip": "192.0.2.1",
                "model": "ios",
                "username": "old-user",
                "password": "old-password",
            }]
            deploy_oxidized.write_inventory(source, original)

            with patch("atlas.deployment.os.replace", side_effect=OSError("fixture")):
                with self.assertRaises(OSError):
                    deploy_oxidized.write_inventory(source, [])

            self.assertEqual(deploy_oxidized.read_inventory(source), original)

    def test_reload_runs_only_after_successful_atomic_replace(self):
        source = Path("/test/router.json")
        reload_callback = Mock()

        with patch.object(deploy_oxidized, "write_inventory") as write_inventory:
            deploy_oxidized.apply_inventory(source, [], reload_callback)

        write_inventory.assert_called_once_with(source, [])
        reload_callback.assert_called_once_with()

        reload_callback.reset_mock()
        with patch.object(
            deploy_oxidized,
            "write_inventory",
            side_effect=OSError("fixture"),
        ):
            with self.assertRaises(OSError):
                deploy_oxidized.apply_inventory(source, [], reload_callback)

        reload_callback.assert_not_called()

    def test_reconcile_only_does_not_run_compose_deploy_operations(self):
        settings = config()
        compose_file = Path("/test/docker-compose.yml")
        source = Path("/test/router.json")
        inventory = [{
            "name": "SW1",
            "ip": "192.0.2.1",
            "model": "ios",
            "username": "ci",
            "password": "ci-only",
        }]

        with patch.object(deploy_oxidized, "require_running_container") as running, \
             patch.object(deploy_oxidized, "write_inventory") as write_inventory, \
             patch.object(deploy_oxidized, "reload_inventory") as reload_inventory, \
             patch.object(deploy_oxidized, "compose") as compose:
            deploy_oxidized.reconcile_running(
                settings,
                compose_file,
                source,
                inventory,
            )

        running.assert_called_once()
        write_inventory.assert_called_once_with(source, inventory)
        reload_inventory.assert_called_once()
        compose.assert_not_called()

    def test_empty_inventory_accepts_waiting_reload_response(self):
        with patch.object(
            deploy_oxidized,
            "reload_status",
            return_value=503,
        ):
            deploy_oxidized.reload_inventory(
                Path("/test/docker-compose.yml"),
                "atlas-oxidized",
                [],
                1,
            )

    def test_secret_values_are_not_printed_during_inventory_generation(self):
        output = io.StringIO()
        with redirect_stdout(output):
            deploy_oxidized.build_inventory(
                [device("SW1", "192.0.2.1/32")],
                FakeOpenBao(password="do-not-log-this"),
                "test-runtime-token",
            )

        self.assertNotIn("do-not-log-this", output.getvalue())
        self.assertNotIn("test-runtime-token", output.getvalue())

    def test_enabled_invalid_device_fails_instead_of_using_defaults(self):
        invalid = device("SW1", "192.0.2.1/32")
        invalid["platform"] = None

        with self.assertRaisesRegex(RuntimeError, "Missing NetBox platform"):
            deploy_oxidized.build_inventory(
                [invalid],
                FakeOpenBao(),
                "test-token",
            )

    def test_host_key_accept_is_explicit_idempotent_and_mismatch_safe(self):
        scanned = {
            "line": "192.0.2.1 ssh-ed25519 YWJj",
            "key_type": "ssh-ed25519",
            "fingerprint": "SHA256:expected",
        }
        old = {
            "line": "192.0.2.1 ssh-ed25519 ZGVm",
            "key_type": "ssh-ed25519",
            "fingerprint": "SHA256:old",
        }

        with tempfile.TemporaryDirectory() as temporary:
            known_hosts = Path(temporary) / "known_hosts"
            known_hosts.write_text("", encoding="utf-8")

            with patch.object(
                deploy_oxidized, "scan_host_keys", return_value=[scanned]
            ), patch.object(
                deploy_oxidized, "trusted_host_keys", return_value=[]
            ):
                self.assertEqual(
                    deploy_oxidized.accept_host_key(
                        known_hosts,
                        "192.0.2.1",
                        "SHA256:expected",
                    ),
                    "accepted",
                )

            accepted = known_hosts.read_text(encoding="utf-8")

            with patch.object(
                deploy_oxidized, "scan_host_keys", return_value=[scanned]
            ), patch.object(
                deploy_oxidized, "trusted_host_keys", return_value=[scanned]
            ):
                self.assertEqual(
                    deploy_oxidized.accept_host_key(
                        known_hosts,
                        "192.0.2.1",
                        "SHA256:expected",
                    ),
                    "already_trusted",
                )

            self.assertEqual(known_hosts.read_text(encoding="utf-8"), accepted)

            with patch.object(
                deploy_oxidized, "scan_host_keys", return_value=[scanned]
            ), patch.object(
                deploy_oxidized, "trusted_host_keys", return_value=[old]
            ):
                with self.assertRaisesRegex(RuntimeError, "mismatch"):
                    deploy_oxidized.accept_host_key(
                        known_hosts,
                        "192.0.2.1",
                        "SHA256:expected",
                    )

            self.assertEqual(known_hosts.read_text(encoding="utf-8"), accepted)

    def test_runtime_secrets_and_trust_files_are_not_tracked(self):
        tracked = subprocess.run(
            ["git", "ls-files", "--", "*router.json", "*known_hosts"],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
        self.assertEqual(tracked.stdout, "")
        ignored = (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
        self.assertIn("router.json", ignored)
        self.assertIn("known_hosts", ignored)

    def test_waiting_backend_supports_empty_json_inventory(self):
        self.assertIn("JSON.parse", deploy_oxidized.WAIT_ENTRYPOINT)
        self.assertIn('path.start_with?("/nodes.json")', deploy_oxidized.WAIT_SERVER)
        self.assertIn('body = "[]\\n"', deploy_oxidized.WAIT_SERVER)
        self.assertIn('path.start_with?("/reload")', deploy_oxidized.WAIT_SERVER)
        self.assertIn("503 Service Unavailable", deploy_oxidized.WAIT_SERVER)


if __name__ == "__main__":
    unittest.main()
