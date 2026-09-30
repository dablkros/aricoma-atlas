import importlib.util
import os
import stat
import tempfile
import unittest
from pathlib import Path


spec = importlib.util.spec_from_file_location(
    "deploy_netbox", Path(__file__).resolve().parents[1] / "scripts/deploy_netbox.py"
)
deploy_netbox = importlib.util.module_from_spec(spec)
spec.loader.exec_module(deploy_netbox)


class NetBoxPermissionTests(unittest.TestCase):
    def test_restrictive_umask_does_not_block_container_configuration(self):
        with tempfile.TemporaryDirectory() as temporary:
            runtime = Path(temporary)
            previous = os.umask(0o077)
            try:
                directory = runtime / "configuration"
                directory.mkdir()
                nested = directory / "plugins"
                nested.mkdir()
                config = nested / "settings.py"
                config.write_text("# non-secret configuration\n")
                secret = runtime / "identity.json"
                secret.write_text("private fixture")
                self.assertEqual(stat.S_IMODE(directory.stat().st_mode), 0o700)
                self.assertEqual(stat.S_IMODE(config.stat().st_mode), 0o600)
                deploy_netbox.prepare_configuration_permissions(runtime)
                deploy_netbox.prepare_configuration_permissions(runtime)
                for path in (directory, nested):
                    self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o755)
                self.assertEqual(stat.S_IMODE(config.stat().st_mode), 0o644)
                for path in (runtime, secret):
                    self.assertEqual(stat.S_IMODE(path.stat().st_mode),
                                     0o700 if path.is_dir() else 0o600)
            finally:
                os.umask(previous)

    def test_symlink_cannot_change_permissions_of_a_private_file(self):
        with tempfile.TemporaryDirectory() as temporary:
            runtime = Path(temporary)
            directory = runtime / "configuration"
            directory.mkdir(mode=0o700)
            secret = runtime / "identity.json"
            secret.write_text("private fixture")
            secret.chmod(0o600)
            (directory / "external.py").symlink_to(secret)
            with self.assertRaisesRegex(RuntimeError, "symbolic links"):
                deploy_netbox.prepare_configuration_permissions(runtime)
            self.assertEqual(stat.S_IMODE(secret.stat().st_mode), 0o600)
            self.assertEqual(stat.S_IMODE(directory.stat().st_mode), 0o700)
