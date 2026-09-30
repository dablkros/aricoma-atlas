import importlib.util
import os
from pathlib import Path

import yaml


def load_module():
    path = Path(__file__).resolve().parents[1] / "scripts" / "import_site_config.py"
    spec = importlib.util.spec_from_file_location("import_site_config_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_import_rewrites_tls_paths_and_modes(tmp_path, monkeypatch):
    module = load_module()

    source_runtime = tmp_path / "source" / ".runtime"
    source_tls = source_runtime / "tls"
    source_tls.mkdir(parents=True)

    (source_tls / "ca.crt").write_text("CA", encoding="utf-8")
    (source_tls / "server.crt").write_text("CERT", encoding="utf-8")
    (source_tls / "server.key").write_text("KEY", encoding="utf-8")

    config = {
        "proxy": {
            "listen_address": "10.0.0.10",
            "https_port": 443,
            "allowed_networks": ["10.0.0.0/24"],
        },
        "tls": {
            "ca_certificate": str(source_tls / "ca.crt"),
        },
        "services": {
            "netbox": {
                "hostname": "netbox.atlas.test",
                "certificate": str(source_tls / "server.crt"),
                "private_key": str(source_tls / "server.key"),
            },
            "oxidized": {
                "hostname": "oxidized.atlas.test",
                "certificate": str(source_tls / "server.crt"),
                "private_key": str(source_tls / "server.key"),
            },
        },
    }

    source_config = source_runtime / "proxy.yaml"
    source_config.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")

    monkeypatch.setattr(module.os, "chown", lambda *args, **kwargs: None)
    monkeypatch.setattr(module.pwd, "getpwnam", lambda name: type("P", (), {"pw_uid": os.getuid()})())
    monkeypatch.setattr(module.grp, "getgrnam", lambda name: type("G", (), {"gr_gid": os.getgid()})())

    target_root = tmp_path / "target"
    target = module.import_config(source_config, target_root, "atlas", "atlas")
    installed = yaml.safe_load(target.read_text(encoding="utf-8"))

    assert installed["tls"]["ca_certificate"] == str(target_root / ".runtime/tls/ca.crt")
    assert installed["services"]["netbox"]["certificate"] == str(target_root / ".runtime/tls/netbox.crt")
    assert installed["services"]["netbox"]["private_key"] == str(target_root / ".runtime/tls/netbox.key")
    assert oct((target_root / ".runtime/tls/netbox.key").stat().st_mode & 0o777) == "0o600"
    assert oct(target.stat().st_mode & 0o777) == "0o600"
