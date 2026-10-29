import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, call, patch

import yaml

from atlas import __version__
from atlas.openbao_client import OpenBaoAuthenticationError, OpenBaoNotFound
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
            "ansible_job_timeout": 120,
            "fortios_validate_certs": False,
            "ssh_strict_host_keys": True,
            "prophylaxis_result_retention": 10000,
            "prophylaxis_concurrency": 4,
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
        env_example = (ROOT / ".env.example").read_text(encoding="utf-8")

        self.assertEqual(config["backend"]["version"], __version__)
        self.assertEqual(
            config["backend"]["docker_image"],
            f"aricoma-atlas-backend:{__version__}",
        )
        self.assertIn(f"ATLAS_APP_VERSION={__version__}", env_example.splitlines())
        self.assertFalse(config["backend"]["fortios_validate_certs"])

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

    def test_invalid_ansible_and_fortios_settings_are_rejected(self):
        cases = (
            ({"ansible_job_timeout": 0}, "ansible_job_timeout"),
            ({"fortios_validate_certs": "true"}, "fortios_validate_certs"),
        )
        for values, message in cases:
            with self.subTest(values=values), self.assertRaisesRegex(
                ValueError, message
            ):
                deploy_backend.validate_config(backend_config(backend=values))

    def test_monitoring_egress_matches_host_route_source(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "monitoring.yaml"
            path.write_text(
                "version: 2\n"
                "allowed_device_networks: [10.0.0.0/8]\n"
                "fortios: {}\n"
                "monitoring:\n"
                "  egress_profiles:\n"
                "    lab:\n"
                "      device_networks: [10.255.253.0/24]\n"
                "      source_ip: 10.99.98.200\n",
                encoding="utf-8",
            )
            route_source = Mock(return_value="10.99.98.200")

            deploy_backend.validate_monitoring_egress(path, route_source)

            route_source.assert_called_once_with("10.255.253.1")

    def test_host_route_source_reads_kernel_selected_src(self):
        runner = Mock(
            return_value=SimpleNamespace(
                returncode=0,
                stdout=(
                    "10.255.253.10 via 10.99.98.1 dev ens192 "
                    "src 10.99.98.200 uid 1000\n"
                ),
            )
        )

        source = deploy_backend.host_route_source("10.255.253.10", runner)

        self.assertEqual(source, "10.99.98.200")
        runner.assert_called_once_with(
            ["ip", "route", "get", "10.255.253.10"],
            capture_output=True,
            text=True,
            check=False,
        )

    def test_monitoring_egress_rejects_wrong_host_route_source(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "monitoring.yaml"
            path.write_text(
                "version: 2\n"
                "allowed_device_networks: [10.0.0.0/8]\n"
                "fortios: {}\n"
                "monitoring:\n"
                "  egress_profiles:\n"
                "    lab: {device_networks: [10.255.253.0/24], source_ip: 10.200.200.10}\n",
                encoding="utf-8",
            )

            with self.assertRaisesRegex(
                RuntimeError, "expects source 10.200.200.10.*selects 10.99.98.200"
            ):
                deploy_backend.validate_monitoring_egress(
                    path, Mock(return_value="10.99.98.200")
                )

    def test_monitoring_egress_allows_multiple_route_selected_vm_addresses(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "monitoring.yaml"
            path.write_text(
                "version: 2\n"
                "allowed_device_networks: [10.0.0.0/8, 172.16.0.0/12]\n"
                "fortios: {}\n"
                "monitoring:\n"
                "  egress_profiles:\n"
                "    lab: {device_networks: [10.255.253.0/24], source_ip: 10.99.98.200}\n"
                "    dc: {device_networks: [172.20.0.0/16], source_ip: 172.20.0.10}\n",
                encoding="utf-8",
            )
            sources = {
                "10.255.253.1": "10.99.98.200",
                "172.20.0.1": "172.20.0.10",
            }

            deploy_backend.validate_monitoring_egress(
                path, lambda target: sources[target]
            )

    def test_proxy_egress_is_not_validated_against_atlas_host_route(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "monitoring.yaml"
            path.write_text(
                "version: 2\n"
                "allowed_device_networks: [10.0.0.0/8]\n"
                "fortios: {}\n"
                "monitoring:\n"
                "  egress_profiles:\n"
                "    remote:\n"
                "      device_networks: [10.20.0.0/16]\n"
                "      source_ip: 10.20.0.5\n"
                "      proxy_name: remote-proxy\n",
                encoding="utf-8",
            )
            route_source = Mock()

            deploy_backend.validate_monitoring_egress(path, route_source)

            route_source.assert_not_called()


class AtlasOperatorTokenTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        identity = self.root / ".runtime/openbao-approle.json"
        identity.parent.mkdir(parents=True)
        identity.write_text("{}\n", encoding="utf-8")
        self.client = Mock()
        self.client.login_from_identity.return_value = "deployment-token"

    def tearDown(self):
        self.temporary.cleanup()

    def test_existing_openbao_token_is_materialized_to_private_runtime_file(self):
        operator_token = "o" * 40
        self.client.kv_read_with_metadata.return_value = (
            {"operator_token": operator_token},
            {"version": 3},
        )

        token_file = deploy_backend.sync_backend_operator_token(
            self.root,
            self.client,
        )

        self.assertEqual(token_file.read_text(encoding="utf-8"), operator_token + "\n")
        self.assertEqual(os.stat(token_file).st_mode & 0o777, 0o600)
        self.assertEqual(os.stat(token_file.parent).st_mode & 0o777, 0o700)
        self.client.kv_write.assert_not_called()
        self.client.revoke_self.assert_called_once_with("deployment-token")

    def test_existing_local_token_is_migrated_without_rotation(self):
        operator_token = "l" * 40
        token_file = self.root / ".runtime/monitoring/operator-token"
        token_file.parent.mkdir(parents=True)
        token_file.write_text(operator_token + "\n", encoding="utf-8")
        self.client.kv_read_with_metadata.side_effect = OpenBaoNotFound("not found")

        deploy_backend.sync_backend_operator_token(self.root, self.client)

        self.client.kv_write.assert_called_once_with(
            "deployment-token",
            "backend",
            {"operator_token": operator_token},
            mount="atlas",
            cas=0,
        )
        self.client.kv_read.assert_not_called()
        self.assertEqual(token_file.read_text(encoding="utf-8"), operator_token + "\n")

    def test_fresh_install_creates_openbao_token_and_materializes_same_value(self):
        self.client.kv_read_with_metadata.side_effect = OpenBaoNotFound("not found")

        token_file = deploy_backend.sync_backend_operator_token(
            self.root,
            self.client,
        )

        stored = self.client.kv_write.call_args.args[2]["operator_token"]
        self.assertRegex(stored, r"^[A-Za-z0-9_-]{32,256}$")
        self.assertEqual(token_file.read_text(encoding="utf-8"), stored + "\n")

    def test_invalid_openbao_token_is_rejected_and_session_is_revoked(self):
        self.client.kv_read_with_metadata.return_value = (
            {"operator_token": "contains spaces"},
            {"version": 1},
        )

        with self.assertRaisesRegex(RuntimeError, "invalid format"):
            deploy_backend.sync_backend_operator_token(self.root, self.client)

        self.client.kv_write.assert_not_called()
        self.client.revoke_self.assert_called_once_with("deployment-token")

    def test_existing_backend_keys_are_preserved_when_operator_token_is_added(self):
        operator_token = "p" * 40
        token_file = self.root / ".runtime/monitoring/operator-token"
        token_file.parent.mkdir(parents=True)
        token_file.write_text(operator_token + "\n", encoding="utf-8")
        self.client.kv_read_with_metadata.return_value = (
            {"existing_key": "existing-value"},
            {"version": 7},
        )

        deploy_backend.sync_backend_operator_token(self.root, self.client)

        self.client.kv_write.assert_called_once_with(
            "deployment-token",
            "backend",
            {
                "existing_key": "existing-value",
                "operator_token": operator_token,
            },
            mount="atlas",
            cas=7,
        )

    def test_runtime_symlink_is_rejected_before_openbao_login(self):
        (self.root / ".runtime/openbao-approle.json").unlink()
        (self.root / ".runtime").rmdir()
        outside = self.root / "outside"
        outside.mkdir()
        (self.root / ".runtime").symlink_to(outside, target_is_directory=True)

        with self.assertRaisesRegex(RuntimeError, "cannot be a symlink"):
            deploy_backend.sync_backend_operator_token(self.root, self.client)

        self.client.login_from_identity.assert_not_called()


class AtlasAdminBootstrapTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        runtime = self.root / ".runtime"
        runtime.mkdir()
        (runtime / "openbao-approle.json").write_text("{}\n", encoding="utf-8")
        self.bootstrap = runtime / "auth/bootstrap-admin.json"
        self.bootstrap.parent.mkdir()
        self.record = {
            "schema_version": 1,
            "username": "admin",
            "role": "admin",
            "active": True,
            "password_hash": "$argon2id$v=19$m=65536,t=3,p=4$fixture$fixture",
            "recovery_password": "a readable recovery password",
        }
        self.login_record = {
            key: value
            for key, value in self.record.items()
            if key != "recovery_password"
        }
        self.bootstrap.write_text(json.dumps(self.record), encoding="utf-8")
        self.client = Mock()
        self.client.login_from_identity.return_value = "deployment-token"
        self.client.kv_read.side_effect = OpenBaoNotFound("missing")

    def tearDown(self):
        self.temporary.cleanup()

    def test_bootstrap_is_written_once_then_removed(self):
        marker = deploy_backend.sync_admin_bootstrap(self.root, self.client)

        self.assertEqual(
            self.client.kv_write.call_args_list,
            [
                call(
                    "deployment-token",
                    "auth/local/users/admin",
                    self.login_record,
                    mount="atlas",
                    cas=0,
                ),
                call(
                    "deployment-token",
                    "auth/recovery/users/admin",
                    {
                        "schema_version": 1,
                        "username": "admin",
                        "password": "a readable recovery password",
                    },
                    mount="atlas",
                    cas=0,
                ),
            ],
        )
        self.assertFalse(self.bootstrap.exists())
        self.assertEqual(os.stat(marker).st_mode & 0o777, 0o600)
        self.assertEqual(json.loads(marker.read_text())["username"], "admin")
        self.client.revoke_self.assert_called_once_with("deployment-token")

    def test_existing_different_user_record_is_not_overwritten(self):
        self.client.kv_read.side_effect = None
        self.client.kv_read.return_value = {**self.login_record, "active": False}

        with self.assertRaisesRegex(RuntimeError, "refusing to overwrite"):
            deploy_backend.sync_admin_bootstrap(self.root, self.client)

        self.assertTrue(self.bootstrap.exists())
        self.client.kv_write.assert_not_called()

    def test_upgrade_without_bootstrap_does_not_block_deploy(self):
        self.bootstrap.unlink()

        self.assertIsNone(deploy_backend.sync_admin_bootstrap(self.root, self.client))
        self.client.login_from_identity.assert_not_called()

    def test_stale_backend_policy_has_actionable_error(self):
        self.bootstrap.unlink()
        marker = self.root / ".runtime/auth/admin-configured"
        marker.write_text('{"schema_version": 1, "username": "admin"}\n')
        self.client.kv_read.side_effect = OpenBaoAuthenticationError(
            "forbidden", status_code=403
        )

        with self.assertRaisesRegex(RuntimeError, "reconcile Atlas backend roles"):
            deploy_backend.verify_admin_backend_access(self.root, self.client)

        self.client.revoke_self.assert_called_once_with("deployment-token")


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
        monitoring_identity = self.root / ".runtime/openbao-monitoring.json"
        monitoring_identity.write_text("{}\n", encoding="utf-8")
        credential_writer_identity = (
            self.root / ".runtime/openbao-credential-writer.json"
        )
        credential_writer_identity.write_text("{}\n", encoding="utf-8")
        operator_token = self.root / ".runtime/monitoring/operator-token"
        operator_token.parent.mkdir(parents=True)
        operator_token.write_text("t" * 40 + "\n", encoding="utf-8")
        monitoring_default = self.root / "deployment/monitoring.default.yaml"
        monitoring_default.write_text(
            "version: 2\nallowed_device_networks: [192.0.2.0/24]\n"
            "fortios: {default_vdom: root, default_https_port: 443, default_snmp_port: 161, tls_verify: true}\n"
            "monitoring: {}\n",
            encoding="utf-8",
        )
        known_hosts = self.root / ".runtime/oxidized/ssh/known_hosts"
        known_hosts.parent.mkdir(parents=True)
        known_hosts.write_text("fixture host key\n", encoding="utf-8")
        self.inventory_directory = self.root / "run/atlas/oxidized"
        self.inventory_directory.mkdir(parents=True)
        (self.inventory_directory / "router.json").write_text(
            "[]\n",
            encoding="utf-8",
        )
        (self.root / "build/device-types").mkdir(parents=True)
        (self.root / "build/catalog-report.json").write_text(
            "{}\n",
            encoding="utf-8",
        )
        (self.root / "devicetype-library/device-types").mkdir(parents=True)
        self.environment = patch.dict(
            os.environ,
            {"ATLAS_OXIDIZED_RUN_DIR": str(self.inventory_directory)},
        )
        self.environment.start()
        self.config = deploy_backend.validate_config(backend_config())
        self.ui_urls = {
            "netbox": "https://netbox.example.test/",
            "oxidized": "https://oxidized.example.test/",
            "zabbix": "https://zabbix.example.test/",
        }

    def tearDown(self):
        self.environment.stop()
        self.temporary.cleanup()

    def test_prepare_runtime_writes_hardened_loopback_only_compose(self):
        compose_file = deploy_backend.prepare_runtime(
            self.config,
            self.root,
            self.ui_urls,
        )
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
            service["environment"]["ATLAS_ZABBIX_URL"],
            "http://atlas-zabbix-web:8080/api_jsonrpc.php",
        )
        self.assertEqual(
            service["environment"]["ATLAS_ZABBIX_UI_URL"],
            "https://zabbix.example.test/",
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
            service["environment"]["ATLAS_INVENTORY_CONFIG"],
            "/run/atlas/inventory/config.yaml",
        )
        self.assertEqual(
            service["environment"]["ATLAS_INVENTORY_DEVICE_TYPES"],
            "/run/atlas/inventory/device-types",
        )
        self.assertEqual(
            service["environment"]["ATLAS_INVENTORY_TRUST_MOUNTED_CATALOG"],
            "true",
        )
        self.assertEqual(
            service["environment"]["ATLAS_PROPHYLAXIS_RESULT_RETENTION"],
            "10000",
        )
        self.assertEqual(
            service["environment"]["ATLAS_ANSIBLE_PROJECT_DIR"],
            "/app/automation",
        )
        self.assertEqual(
            service["environment"]["ATLAS_ANSIBLE_JOB_TIMEOUT"],
            "120",
        )
        self.assertEqual(
            service["environment"]["ATLAS_FORTIOS_VALIDATE_CERTS"],
            "false",
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
            f"{(self.root / '.runtime/openbao-backend.json').resolve()}:"
            "/run/secrets/atlas-backend.json:ro",
            service["volumes"],
        )
        self.assertIn(
            f"{(self.root / '.runtime/openbao-monitoring.json').resolve()}:"
            "/run/secrets/atlas-monitoring.json:ro",
            service["volumes"],
        )
        self.assertIn(
            f"{(self.root / '.runtime/openbao-credential-writer.json').resolve()}:"
            "/run/secrets/atlas-credential-writer.json:ro",
            service["volumes"],
        )
        self.assertEqual(
            service["environment"]["ATLAS_CREDENTIAL_WRITER_IDENTITY_FILE"],
            "/run/secrets/atlas-credential-writer.json",
        )
        self.assertEqual(
            service["environment"]["ATLAS_MONITORING_JOBS_DIRECTORY"],
            "/run/atlas/monitoring/jobs",
        )
        self.assertEqual(
            os.stat(self.root / ".runtime/monitoring/operator-token").st_mode & 0o777,
            0o600,
        )
        self.assertIn(
            f"{self.inventory_directory}:/run/atlas/oxidized:rw",
            service["volumes"],
        )
        self.assertIn(
            f"{(self.root / '.runtime/prophylaxis').resolve()}:"
            "/run/atlas/prophylaxis:rw",
            service["volumes"],
        )
        self.assertIn(
            f"{(self.root / '.runtime/inventory/device-types').resolve()}:"
            "/run/atlas/inventory/device-types:rw",
            service["volumes"],
        )
        self.assertIn(
            f"{(self.root / 'build').resolve()}:/app/build:ro",
            service["volumes"],
        )
        self.assertIn(
            f"{(self.root / 'devicetype-library/device-types').resolve()}:"
            "/app/devicetype-library/device-types:ro",
            service["volumes"],
        )
        self.assertEqual(
            os.stat(self.root / ".runtime/prophylaxis").st_mode & 0o777,
            0o700,
        )
        self.assertIn(
            f"{(self.root / '.runtime/oxidized/ssh/known_hosts').resolve()}:"
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
            {"openbao", "netbox", "oxidized", "zabbix"},
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
                "zabbix": {
                    "external": True,
                    "name": "atlas-zabbix-api",
                },
            },
        )
        self.assertIn("healthcheck", service)
        self.assertEqual(os.stat(compose_file).st_mode & 0o777, 0o600)
        self.assertEqual(os.stat(compose_file.parent).st_mode & 0o777, 0o700)

    def test_runtime_symlink_is_rejected(self):
        (self.root / ".runtime/openbao-backend.json").unlink()
        (self.root / ".runtime/openbao-monitoring.json").unlink()
        (self.root / ".runtime/openbao-credential-writer.json").unlink()
        (self.root / ".runtime/monitoring/operator-token").unlink()
        (self.root / ".runtime/monitoring").rmdir()
        (self.root / ".runtime/oxidized/ssh/known_hosts").unlink()
        (self.root / ".runtime/oxidized/ssh").rmdir()
        (self.root / ".runtime/oxidized").rmdir()
        (self.root / ".runtime").rmdir()
        target = self.root / "outside-runtime"
        target.mkdir()
        (self.root / ".runtime").symlink_to(target, target_is_directory=True)

        with self.assertRaisesRegex(RuntimeError, "cannot be a symlink"):
            deploy_backend.prepare_runtime(self.config, self.root, self.ui_urls)

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

        compose_file = deploy_backend.prepare_runtime(
            config,
            self.root,
            self.ui_urls,
        )
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
            deploy_backend.prepare_runtime(config, self.root, self.ui_urls)

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
    def test_openbao_gate_validates_deployer_and_backend_identities(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            deployer = root / "deployer.json"
            backend = root / "backend.json"
            monitoring = root / "monitoring.json"
            credential_writer = root / "credential-writer.json"
            deployer.write_text("{}\n", encoding="utf-8")
            backend.write_text("{}\n", encoding="utf-8")
            monitoring.write_text("{}\n", encoding="utf-8")
            credential_writer.write_text("{}\n", encoding="utf-8")
            client = Mock()
            client.seal_status.return_value = {
                "initialized": True,
                "sealed": False,
            }
            client.login_from_identity.side_effect = [
                "deployer-token",
                "backend-token",
                "monitoring-token",
                "credential-writer-token",
            ]
            with (
                patch.object(deploy_atlas, "OPENBAO_IDENTITY_FILE", deployer),
                patch.object(
                    deploy_atlas,
                    "OPENBAO_BACKEND_IDENTITY_FILE",
                    backend,
                ),
                patch.object(
                    deploy_atlas,
                    "OPENBAO_MONITORING_IDENTITY_FILE",
                    monitoring,
                ),
                patch.object(
                    deploy_atlas,
                    "OPENBAO_CREDENTIAL_WRITER_IDENTITY_FILE",
                    credential_writer,
                ),
                patch.object(deploy_atlas, "OpenBaoClient", return_value=client),
                patch.object(deploy_atlas, "header"),
                patch.object(deploy_atlas, "ok"),
            ):
                deploy_atlas.verify_openbao_ready()

            self.assertEqual(
                client.login_from_identity.call_args_list,
                [
                    call(deployer),
                    call(backend),
                    call(monitoring),
                    call(credential_writer),
                ],
            )
            self.assertEqual(
                client.revoke_self.call_args_list,
                [
                    call("deployer-token"),
                    call("backend-token"),
                    call("monitoring-token"),
                    call("credential-writer-token"),
                ],
            )

    def test_openbao_gate_has_actionable_backend_reconcile_error(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            deployer = root / "deployer.json"
            backend = root / "backend.json"
            deployer.write_text("{}\n", encoding="utf-8")
            backend.write_text("{}\n", encoding="utf-8")
            client = Mock()
            client.seal_status.return_value = {
                "initialized": True,
                "sealed": False,
            }
            client.login_from_identity.side_effect = [
                "deployer-token",
                OpenBaoAuthenticationError("HTTP 400"),
            ]
            with (
                patch.object(deploy_atlas, "OPENBAO_IDENTITY_FILE", deployer),
                patch.object(
                    deploy_atlas,
                    "OPENBAO_BACKEND_IDENTITY_FILE",
                    backend,
                ),
                patch.object(deploy_atlas, "OpenBaoClient", return_value=client),
                patch.object(deploy_atlas, "header"),
            ):
                with self.assertRaisesRegex(
                    RuntimeError,
                    "Reconcile Atlas backend roles",
                ):
                    deploy_atlas.verify_openbao_ready()

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
                "scripts.deploy_atlas.deploy_zabbix",
                side_effect=lambda _: order.append("zabbix"),
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
            ["openbao", "netbox", "oxidized", "zabbix", "backend", "proxy"],
        )


class BackendImageDefinitionTests(unittest.TestCase):
    def test_image_runs_as_non_root_and_has_healthcheck(self):
        dockerfile = (ROOT / "deployment/backend/Dockerfile").read_text(
            encoding="utf-8"
        )

        self.assertIn("USER 10001:10001", dockerfile)
        self.assertIn("HEALTHCHECK", dockerfile)
        self.assertIn("ansible-galaxy collection install", dockerfile)
        self.assertIn("COPY --chown=atlas:atlas automation", dockerfile)
        self.assertIn("COPY --chown=atlas:atlas scripts /app/scripts", dockerfile)
        self.assertIn("--no-access-log", dockerfile)


class InstallerContractTests(unittest.TestCase):
    def test_installer_requires_python_312_and_supported_ubuntu_releases(self):
        installer = (ROOT / "install.sh").read_text(encoding="utf-8")

        self.assertIn("sys.version_info >= (3, 12)", installer)
        self.assertIn("24.04|26.04", installer)
        self.assertNotIn("22.04|24.04", installer)


if __name__ == "__main__":
    unittest.main()
