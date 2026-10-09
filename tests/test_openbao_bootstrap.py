import importlib.util
import subprocess
import unittest
from pathlib import Path
from unittest.mock import DEFAULT, Mock, call, patch

from scripts import openbao_access


ROOT = Path(__file__).resolve().parents[1]

spec = importlib.util.spec_from_file_location(
    "deploy_openbao",
    ROOT / "scripts/deploy_openbao.py",
)
deploy_openbao = importlib.util.module_from_spec(spec)
spec.loader.exec_module(deploy_openbao)


class OpenBaoBootstrapTests(unittest.TestCase):
    def test_backend_policy_is_limited_to_required_read_paths(self):
        policy_file = (
            ROOT
            / "deployment"
            / "openbao"
            / "policies"
            / "atlas-backend.hcl"
        )

        self.assertEqual(
            policy_file.read_text(encoding="utf-8").strip(),
            (
                'path "atlas/data/netbox/api" {\n'
                '  capabilities = ["read"]\n'
                '}\n\n'
                'path "atlas/data/devices/credentials/*" {\n'
                '  capabilities = ["read"]\n'
                '}\n\n'
                'path "atlas/data/zabbix/api" {\n'
                '  capabilities = ["read"]\n'
                '}'
            ),
        )

        policy = policy_file.read_text(encoding="utf-8")
        for forbidden in (
            "list",
            "create",
            "update",
            "delete",
            "netbox/admin",
            "postgres",
            "redis",
            "atlas-deployer",
        ):
            self.assertNotIn(forbidden, policy.lower())

    def test_backend_runtime_role_uses_its_own_policy_and_ignored_identity(self):
        backend = deploy_openbao.RUNTIME_ROLES["atlas-backend"]

        self.assertEqual(
            backend["policy_file"],
            ROOT / "deployment/openbao/policies/atlas-backend.hcl",
        )
        self.assertEqual(
            backend["identity_file"],
            ROOT / ".runtime/openbao-backend.json",
        )
        self.assertEqual(backend["additional_policies"], [])

        tracked = subprocess.run(
            [
                "git",
                "ls-files",
                "--",
                ".runtime/openbao-backend.json",
            ],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
        self.assertEqual(tracked.stdout, "")
        self.assertIn(
            ".runtime/",
            (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines(),
        )

    def test_backend_approle_has_only_its_policy_and_short_lived_tokens(self):
        with patch.object(deploy_openbao, "api_request") as api_request:
            deploy_openbao.ensure_runtime_role(
                "test-root-token",
                "atlas-backend",
                [],
            )

        api_request.assert_called_once_with(
            "POST",
            "auth/approle/role/atlas-backend",
            token="test-root-token",
            payload={
                "bind_secret_id": True,
                "token_policies": ["atlas-backend"],
                "secret_id_ttl": "0",
                "secret_id_num_uses": 0,
                "token_ttl": "15m",
                "token_max_ttl": "1h",
                "token_num_uses": 0,
            },
        )

        payload = api_request.call_args.kwargs["payload"]
        self.assertNotIn("role_id", payload)
        self.assertNotIn("secret_id", payload)

    def test_bootstrap_reconciles_backend_and_existing_runtime_roles(self):
        replacements = {
            "ensure_atlas_kv": DEFAULT,
            "ensure_atlas_policy": DEFAULT,
            "ensure_operator_policy": DEFAULT,
            "ensure_runtime_policy": DEFAULT,
            "ensure_approle_auth": DEFAULT,
            "ensure_atlas_deployer_role": DEFAULT,
            "ensure_machine_identity": DEFAULT,
            "ensure_operator_role": DEFAULT,
            "create_operator_identity": DEFAULT,
            "reconcile_runtime_role": DEFAULT,
        }

        with patch.multiple(deploy_openbao, **replacements) as mocked:
            mocked["ensure_machine_identity"].return_value = "test-app-token"

            result = deploy_openbao.bootstrap_openbao("test-root-token")

        self.assertEqual(result, "test-app-token")

        for role_name in deploy_openbao.RUNTIME_ROLES:
            mocked["reconcile_runtime_role"].assert_any_call(
                "test-root-token",
                role_name,
            )

        self.assertEqual(
            set(deploy_openbao.RUNTIME_ROLES),
            {
                "atlas-backend",
                "netbox-runtime",
                "oxidized-runtime",
            },
        )

    def test_runtime_role_reconcile_updates_policy_role_and_identity(self):
        with patch.object(
            deploy_openbao,
            "ensure_runtime_policy",
        ) as policy, patch.object(
            deploy_openbao,
            "ensure_runtime_role",
        ) as role, patch.object(
            deploy_openbao,
            "ensure_runtime_identity",
        ) as identity:
            deploy_openbao.reconcile_runtime_role(
                "temporary-root",
                "atlas-backend",
            )

        spec = deploy_openbao.RUNTIME_ROLES["atlas-backend"]
        policy.assert_called_once_with(
            "temporary-root",
            "atlas-backend",
            spec["policy_file"],
        )
        role.assert_called_once_with("temporary-root", "atlas-backend", [])
        identity.assert_called_once_with(
            "temporary-root",
            "atlas-backend",
            spec["identity_file"],
        )

    def test_existing_install_reconcile_uses_controlled_temporary_root(self):
        client = object()
        with patch.object(
            openbao_access,
            "generate_temporary_root",
        ) as generate:
            generate.return_value = True
            result = openbao_access.reconcile_backend_policy(client)

        self.assertTrue(result)
        generate.assert_called_once()
        self.assertIs(generate.call_args.args[0], client)
        self.assertEqual(generate.call_args.kwargs["requested_ttl"], "15m")

        with patch.object(
            openbao_access.openbao_deployment,
            "reconcile_runtime_role",
        ) as reconcile:
            generate.call_args.kwargs["action"]("temporary-root")

        reconcile.assert_called_once_with("temporary-root", "atlas-backend")

        with patch.object(
            openbao_access,
            "generate_temporary_root",
            return_value=False,
        ):
            self.assertFalse(openbao_access.reconcile_backend_policy(client))

    def test_controlled_root_action_revokes_token_after_failure(self):
        client = Mock()

        def fail(_token):
            raise RuntimeError("policy update failed")

        with self.assertRaisesRegex(RuntimeError, "policy update failed"):
            openbao_access.run_controlled_root_action(
                client,
                "temporary-root",
                fail,
            )

        client.revoke_self.assert_called_once_with("temporary-root")

    def test_deployer_and_operator_approles_remain_separate(self):
        with patch.object(deploy_openbao, "api_request") as api_request:
            deploy_openbao.ensure_atlas_deployer_role("test-root-token")
            deploy_openbao.ensure_operator_role("test-root-token")

        self.assertEqual(
            api_request.call_args_list,
            [
                call(
                    "POST",
                    "auth/approle/role/atlas-deployer",
                    token="test-root-token",
                    payload={
                        "bind_secret_id": True,
                        "token_policies": ["atlas-deployer"],
                        "secret_id_ttl": "0",
                        "secret_id_num_uses": 0,
                        "token_ttl": "15m",
                        "token_max_ttl": "1h",
                        "token_num_uses": 0,
                    },
                ),
                call(
                    "POST",
                    "auth/approle/role/atlas-operator",
                    token="test-root-token",
                    payload={
                        "bind_secret_id": True,
                        "token_policies": ["atlas-operator"],
                        "secret_id_ttl": "0",
                        "secret_id_num_uses": 0,
                        "token_ttl": "15m",
                        "token_max_ttl": "1h",
                        "token_num_uses": 0,
                    },
                ),
            ],
        )

    def test_existing_install_may_lack_only_the_new_backend_identity(self):
        identities = {
            "atlas-backend": None,
            "netbox-runtime": {"role_id": "netbox", "secret_id": "secret"},
            "oxidized-runtime": {"role_id": "oxidized", "secret_id": "secret"},
        }

        with patch.object(
            deploy_openbao,
            "load_identity_file",
            side_effect=[identities[name] for name in deploy_openbao.RUNTIME_ROLES],
        ), patch.object(deploy_openbao, "login_approle") as login_approle:
            deploy_openbao.validate_runtime_identities(
                optional_roles={"atlas-backend"},
            )

        self.assertEqual(login_approle.call_count, 2)


if __name__ == "__main__":
    unittest.main()
