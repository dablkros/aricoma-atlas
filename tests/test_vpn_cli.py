"""Exercise the installed CLI entry point without depending on the caller cwd."""

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parent.parent


class VPNCLIEntrypointTests(unittest.TestCase):
    def test_show_loads_atlas_from_install_root_when_run_as_a_script(self):
        with tempfile.TemporaryDirectory() as directory:
            install = Path(directory) / 'install'
            (install / 'scripts').mkdir(parents=True)
            (install / 'atlas/services').mkdir(parents=True)
            (install / 'atlas/vpn').mkdir(parents=True)
            shutil.copy(ROOT / 'scripts/vpn_cli.py', install / 'scripts/vpn_cli.py')
            for name in ('atlas/__init__.py', 'atlas/services/__init__.py', 'atlas/vpn/__init__.py'):
                (install / name).write_text('', encoding='utf-8')
            (install / 'atlas/config.py').write_text('def get_settings(): return None\n', encoding='utf-8')
            (install / 'atlas/vpn/service.py').write_text('class VPNRegistryError(Exception): pass\n', encoding='utf-8')
            (install / 'atlas/services/factory.py').write_text(
                'from types import SimpleNamespace\n'
                'class Record:\n'
                '    def model_dump(self, **kw): return {"connection_id": "minedu-vpn"}\n'
                'class Service:\n'
                '    def get(self, connection_id): return Record()\n'
                'def build_dependency_services(settings): return SimpleNamespace(vpn=Service())\n',
                encoding='utf-8')
            env = {**os.environ, 'PYTHONDONTWRITEBYTECODE': '1'}
            env.pop('PYTHONPATH', None)
            completed = subprocess.run(
                [sys.executable, str(install / 'scripts/vpn_cli.py'), 'show', 'minedu-vpn'],
                cwd=directory, env=env, capture_output=True, text=True, check=True)
            self.assertEqual(json.loads(completed.stdout), {'connection_id': 'minedu-vpn'})


if __name__ == '__main__':
    unittest.main()
