import importlib.util
import os
import sys
import types
from pathlib import Path

import yaml


def load_module():
    deployment = types.ModuleType("atlas.deployment")
    deployment.OXIDIZED_NETWORK = "atlas-oxidized-web"
    deployment.compose = lambda *args, **kwargs: None
    deployment.ensure_network = lambda *args, **kwargs: None
    deployment.wait_healthy = lambda *args, **kwargs: None

    def write_yaml(path, data):
        Path(path).write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")

    deployment.write_yaml = write_yaml

    atlas = types.ModuleType("atlas")
    atlas.deployment = deployment
    sys.modules["atlas"] = atlas
    sys.modules["atlas.deployment"] = deployment

    path = Path(__file__).resolve().parents[1] / "scripts" / "deploy_oxidized.py"
    spec = importlib.util.spec_from_file_location("deploy_oxidized_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def config():
    return {
        "runtime": {
            "directory": ".runtime/oxidized",
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


def test_canonical_config_has_strict_ssh_and_complete_csv_mapping():
    module = load_module()
    result = module.canonical_oxidized_config(config()["oxidized"])

    assert result["input"]["ssh"]["secure"] is True
    csv = result["source"]["csv"]
    assert csv["map"] == {
        "name": 0,
        "model": 1,
        "username": 2,
        "password": 3,
    }
    assert csv["vars_map"] == {"enable": 4}


def test_prepare_runtime_creates_persistent_known_hosts_and_waiting_entrypoint(tmp_path):
    module = load_module()
    compose_file = module.prepare_runtime(config(), root=tmp_path)
    runtime = tmp_path / ".runtime" / "oxidized"

    assert oct(runtime.stat().st_mode & 0o777) == "0o700"
    assert (runtime / "router.db").exists()
    assert oct((runtime / "router.db").stat().st_mode & 0o777) == "0o644"
    assert (runtime / "ssh" / "known_hosts").exists()
    assert oct((runtime / "ssh" / "known_hosts").stat().st_mode & 0o777) == "0o644"
    assert os.access(runtime / "entrypoint.sh", os.X_OK)

    compose_data = yaml.safe_load(compose_file.read_text(encoding="utf-8"))
    service = compose_data["services"]["oxidized"]
    volumes = service["volumes"]

    assert any(":/home/oxidized/.ssh:ro" in volume for volume in volumes)
    assert service["entrypoint"] == [
        "/bin/sh",
        "/etc/atlas-oxidized/entrypoint.sh",
    ]


def test_empty_inventory_is_detected(tmp_path):
    module = load_module()
    source = tmp_path / "router.db"
    source.write_text("\n# no devices yet\n", encoding="utf-8")
    assert module.has_inventory(source) is False

    source.write_text("10.0.0.1:ios:user:pass:enable\n", encoding="utf-8")
    assert module.has_inventory(source) is True


def test_waiting_server_exposes_nodes_json_and_rejects_reload():
    module = load_module()
    assert 'path.start_with?("/nodes.json")' in module.WAIT_SERVER
    assert 'body = "[]\\n"' in module.WAIT_SERVER
    assert 'path.start_with?("/reload")' in module.WAIT_SERVER
    assert '503 Service Unavailable' in module.WAIT_SERVER


def test_wait_entrypoint_transitions_to_official_init():
    module = load_module()
    assert 'ruby "$WAIT_SERVER" &' in module.WAIT_ENTRYPOINT
    assert 'while ! has_inventory' in module.WAIT_ENTRYPOINT
    assert 'exec /sbin/my_init' in module.WAIT_ENTRYPOINT
