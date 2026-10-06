import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import call, patch

import yaml

from atlas import __version__
from scripts import deploy_atlas, deploy_backend


ROOT = Path(__file__).resolve().parent.parent


def backend_config(**overrides):
    config = {
        "backend": {
            "version": __version__,
            "docker_image": f"aricoma-atlas-backend:{__version__}",
            "python_base_image": "docker.io/library/python:3.13.16-slim-bookworm",
            "dockerfile": "deployment/backend/Dockerfile",
            "environment": "production",
            "log_level": "INFO",
            "ssh_strict_host_keys": True,
            "prophylaxis_result_retention": 10000,
        },
        "runtime": {
            "directory": ".runtime/backend",
            "compose_project": "atlas-backend",
            "startup_timeout": 120,
        },
        "network": {
            "listen_address": "127.0.0.1",
            "host_port": 8081,
            "container_port": 8000,
        },
    }
    for section, values in overrides.items():
        config[section].update(values)
    return config


class BackendConfigurationTests(unittest.TestCase):
    def test_repository_configuration_matches_package_version(self):
        config = deploy_backend.load_config(ROOT / "deployment/backend.yaml")

        self.assertEqual(config["backend"]["version"], __version__)
        self.assertEqual(
            config["backend"]["docker_image"],
            f"aricoma-atlas-backend:{__version__}",
        )

    def test_public_listen_address_is_rejected(self):
        config = backend_config(network={"listen_address": "0.0.0.0"})

        with self.assertRaisesRegex(ValueError, "loopback"):
            deploy_backend.validate_config(config)

    def test_unversioned_image_is_rejected(self):
        config = backend_config(backend={"docker_image": "aricoma-atlas-backend"})

        with self.assertRaisesRegex(ValueError, "explicit version tag"):
            deploy_backend.validate_config(config)

    def test_package_version_mismatch_is_rejected(self):
        config = backend_config(backend={"version": "9.9.9"})

        with self.assertRaisesRegex(ValueError, "package version"):
            deploy_backend.validate_config(config)

    def test_non_boolean_host_key_setting_is_rejected(self):
        config = backend_config(backend={"ssh_strict_host_keys": "false"})

        with self.assertRaisesRegex(ValueError, "must be a boolean"):
            deploy_backend.validate_config(config)

    def test_invalid_prophylaxis_retention_is_rejected(self):
        config = backend_config(backend={"prophylaxis_result_retention": 0})

        with self.assertRaisesRegex(ValueError, "result_retention"):
            deploy_backend.validate_config(config)


class BackendRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        dockerfile = self.root / "deployment/backend/Dockerfile"
        dockerfile.parent.mkdir(parents=True)
        dockerfile.write_text("FROM scratch\n", encoding="utf-8")
        identity = self.root / ".runtime/openbao-backend.json"
        identity.parent.mkdir(parents=True)
        identity.write_text("{}\n", encoding="utf-8")
        known_hosts = self.root / ".runtime/oxidized/ssh/known_hosts"
        known_hosts.parent.mkdir(parents=True)
        known_hosts.write_text("fixture host key\n", encoding="utf-8")
        self.inventory_directory = self.root / "run/atlas/oxidized"
        self.inventory_directory.mkdir(parents=True)
        (self.inventory_directory / "router.json").write_text(
            "[]\n",
            encoding="utf-8",
        )
        self.environment = patch.dict(
            os.environ,
            {"ATLAS_OXIDIZED_RUN_DIR": str(self.inventory_directory)},
        )
        self.environment.start()
        self.config = deploy_backend.validate_config(backend_config())

    def tearDown(self):
        self.environment.stop()
        self.temporary.cleanup()

    def test_prepare_runtime_writes_hardened_loopback_only_compose(self):
        compose_file = deploy_backend.prepare_runtime(self.config, self.root)
        data = yaml.safe_load(compose_file.read_text(encoding="utf-8"))
        service = data["services"]["backend"]

        self.assertEqual(service["image"], f"aricoma-atlas-backend:{__version__}")
        self.assertEqual(service["ports"], ["127.0.0.1:8081:8000"])
        self.assertEqual(service["build"]["context"], str(self.root.resolve()))
        self.assertEqual(
            service["build"]["dockerfile"],
            "deployment/backend/Dockerfile",
        )
        self.assertTrue(service["read_only"])
        self.assertEqual(service["cap_drop"], ["ALL"])
        self.assertEqual(service["security_opt"], ["no-new-privileges:true"])
        self.assertEqual(service["environment"]["ATLAS_ENVIRONMENT"], "production")
        self.assertEqual(
            service["environment"]["ATLAS_OPENBAO_URL"],
            "http://atlas-openbao:8200",
        )
        self.assertEqual(
            service["environment"]["ATLAS_NETBOX_URL"],
            "http://atlas-netbox:8080",
        )
        self.assertEqual(
            service["environment"]["ATLAS_OXIDIZED_URL"],
            "http://atlas-oxidized:8888",
        )
        self.assertEqual(
            service["environment"]["ATLAS_OXIDIZED_INVENTORY_FILE"],
            "/run/atlas/oxidized/router.json",
        )
        self.assertEqual(
            service["environment"]["ATLAS_PROPHYLAXIS_RESULTS_FILE"],
            "/run/atlas/prophylaxis/results.sqlite3",
        )
        self.assertEqual(
            service["environment"]["ATLAS_PROPHYLAXIS_RESULT_RETENTION"],
            "10000",
        )
        self.assertEqual(
            service["environment"]["ATLAS_SSH_KNOWN_HOSTS_FILE"],
            "/run/atlas/ssh/known_hosts",
        )
        self.assertEqual(
            service["environment"]["ATLAS_SSH_STRICT_HOST_KEYS"],
            "true",
        )
        self.assertEqual(service["user"], f"{os.geteuid()}:{os.getegid()}")
        self.assertNotEqual(service["user"].split(":", 1)[0], "0")
        self.assertIn(
            f"{self.root / '.runtime/openbao-backend.json'}:"
            "/run/secrets/atlas-backend.json:ro",
            service["volumes"],
        )
        self.assertIn(
            f"{self.inventory_directory}:/run/atlas/oxidized:rw",
            service["volumes"],
        )
        self.assertIn(
            f"{self.root / '.runtime/prophylaxis'}:"
            "/run/atlas/prophylaxis:rw",
            service["volumes"],
        )
        self.assertEqual(
            os.stat(self.root / ".runtime/prophylaxis").st_mode & 0o777,
            0o700,
        )
        self.assertIn(
            f"{self.root / '.runtime/oxidized/ssh/known_hosts'}:"
            "/run/atlas/ssh/known_hosts:ro",
            service["volumes"],
        )
        self.assertTrue(
            all(
                "/var/run/docker.sock" not in volume
                for volume in service["volumes"]
            )
        )
        self.assertEqual(
            set(service["networks"]),
            {"openbao", "netbox", "oxidized"},
        )
        self.assertEqual(
            data["networks"],
            {
                "openbao": {
                    "external": True,
                    "name": "atlas-openbao-api",
                },
                "netbox": {
                    "external": True,
                    "name": "atlas-netbox-web",
                },
                "oxidized": {
                    "external": True,
                    "name": "atlas-oxidized-web",
                },
            },
        )
        self.assertIn("healthcheck", service)
        self.assertEqual(os.stat(compose_file).st_mode & 0o777, 0o600)
        self.assertEqual(os.stat(compose_file.parent).st_mode & 0o777, 0o700)

    def test_runtime_symlink_is_rejected(self):
        (self.root / ".runtime/openbao-backend.json").unlink()
        (self.root / ".runtime/oxidized/ssh/known_hosts").unlink()
        (self.root / ".runtime/oxidized/ssh").rmdir()
        (self.root / ".runtime/oxidized").rmdir()
        (self.root / ".runtime").rmdir()
        target = self.root / "outside-runtime"
        target.mkdir()
        (self.root / ".runtime").symlink_to(target, target_is_directory=True)

        with self.assertRaisesRegex(RuntimeError, "cannot be a symlink"):
            deploy_backend.prepare_runtime(self.config, self.root)

    def test_development_mode_does_not_require_or_mount_known_hosts(self):
        known_hosts = self.root / ".runtime/oxidized/ssh/known_hosts"
        known_hosts.unlink()
        config = deploy_backend.validate_config(
            backend_config(
                backend={
                    "environment": "development",
                    "ssh_strict_host_keys": False,
                }
            )
        )

        compose_file = deploy_backend.prepare_runtime(config, self.root)
        service = yaml.safe_load(compose_file.read_text(encoding="utf-8"))[
            "services"
        ]["backend"]

        self.assertEqual(
            service["environment"]["ATLAS_SSH_STRICT_HOST_KEYS"],
            "false",
        )
        self.assertFalse(
            any("known_hosts" in volume for volume in service["volumes"])
        )

    def test_intermediate_runtime_symlink_cannot_escape_repository(self):
        runtime_base = self.root / ".runtime"
        target = self.root / "outside-runtime"
        target.mkdir()
        (runtime_base / "nested").symlink_to(target, target_is_directory=True)
        config = deploy_backend.validate_config(
            backend_config(runtime={"directory": ".runtime/nested/backend"})
        )

        with self.assertRaisesRegex(RuntimeError, "escaped"):
            deploy_backend.prepare_runtime(config, self.root)

    @patch("scripts.deploy_backend.wait_healthy")
    @patch("scripts.deploy_backend.compose")
    def test_deploy_builds_starts_and_waits_for_health(self, compose_mock, wait_mock):
        compose_file = self.root / ".runtime/backend/docker-compose.yml"

        deploy_backend.deploy_runtime(self.config, compose_file)

        self.assertEqual(
            compose_mock.call_args_list,
            [
                call(compose_file, "atlas-backend", "config", "--quiet"),
                call(compose_file, "atlas-backend", "build", "--pull", "backend"),
                call(
                    compose_file,
                    "atlas-backend",
                    "up",
                    "-d",
                    "--force-recreate",
                    "backend",
                ),
            ],
        )
        wait_mock.assert_called_once_with(
            compose_file,
            "atlas-backend",
            "backend",
            120,
        )


class AtlasDeploymentOrderTests(unittest.TestCase):
    def test_backend_runs_after_oxidized_and_before_proxy(self):
        order = []
        args = SimpleNamespace(prepare_only=False, verbose=False)

        with (
            patch("scripts.deploy_atlas.parse_args", return_value=args),
            patch("scripts.deploy_atlas.load_proxy_config"),
            patch(
                "scripts.deploy_atlas.deploy_openbao",
                side_effect=lambda: order.append("openbao"),
            ),
            patch(
                "scripts.deploy_atlas.deploy_netbox",
                side_effect=lambda _: order.append("netbox"),
            ),
            patch(
                "scripts.deploy_atlas.deploy_oxidized",
                side_effect=lambda _: order.append("oxidized"),
            ),
            patch(
                "scripts.deploy_atlas.deploy_backend",
                side_effect=lambda _: order.append("backend"),
            ),
            patch(
                "scripts.deploy_atlas.deploy_proxy",
                side_effect=lambda _: order.append("proxy"),
            ),
            patch("scripts.deploy_atlas.print_result"),
            patch("scripts.deploy_atlas.print_initial_unseal_keys"),
        ):
            deploy_atlas.main()

        self.assertEqual(
            order,
            ["openbao", "netbox", "oxidized", "backend", "proxy"],
        )


class BackendImageDefinitionTests(unittest.TestCase):
    def test_image_runs_as_non_root_and_has_healthcheck(self):
        dockerfile = (ROOT / "deployment/backend/Dockerfile").read_text(
            encoding="utf-8"
        )

        self.assertIn("USER 10001:10001", dockerfile)
        self.assertIn("HEALTHCHECK", dockerfile)
        self.assertIn("--no-access-log", dockerfile)


if __name__ == "__main__":
    unittest.main()
