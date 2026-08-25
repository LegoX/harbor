import importlib.util
import json
from pathlib import Path


def _load_generator_module():
    module_path = (
        Path(__file__).resolve().parents[2]
        / "scripts"
        / "misc"
        / "generate_swebench_verified_nohack.py"
    )
    spec = importlib.util.spec_from_file_location(
        "generate_swebench_verified_nohack", module_path
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_harden_task_does_not_grant_net_admin_to_main(tmp_path: Path) -> None:
    module = _load_generator_module()
    task_dir = tmp_path / "task"
    environment_dir = task_dir / "environment"
    environment_dir.mkdir(parents=True)
    (environment_dir / "Dockerfile").write_text("FROM ubuntu:22.04\n")
    (task_dir / "task.toml").write_text("[task]\nname = 'demo'\n")

    module.harden_task(task_dir)

    compose = (environment_dir / "docker-compose.yaml").read_text()
    assert "NET_ADMIN" not in compose
    assert "cap_add" not in compose

    task_toml = (task_dir / "task.toml").read_text()
    assert 'network_mode = "allowlist"' in task_toml
    assert 'network_mode = "public"' in task_toml

    dockerfile = (environment_dir / "Dockerfile").read_text()
    assert "command -v iptables" in dockerfile
    assert "command -v ip6tables" not in dockerfile
    assert "command -v apk" in dockerfile
    assert "Unsupported package manager" in dockerfile
    assert module.HARDEN_END_MARKER in dockerfile

    module.harden_task(task_dir)
    assert (environment_dir / "Dockerfile").read_text() == dockerfile


def test_harden_task_upgrades_legacy_ip6tables_block(tmp_path: Path) -> None:
    module = _load_generator_module()
    task_dir = tmp_path / "task"
    environment_dir = task_dir / "environment"
    environment_dir.mkdir(parents=True)
    (environment_dir / "Dockerfile").write_text(
        "FROM alpine:3.20\n\n"
        "# Harbor nohack network controls.\n"
        "RUN command -v iptables && command -v ip6tables\n"
    )
    (task_dir / "task.toml").write_text("[task]\nname = 'demo'\n")

    module.harden_task(task_dir)

    dockerfile = (environment_dir / "Dockerfile").read_text()
    assert dockerfile.startswith("FROM alpine:3.20\n")
    assert dockerfile.count(module.HARDEN_MARKER) == 1
    assert "command -v ip6tables" not in dockerfile
    assert module.HARDEN_END_MARKER in dockerfile


def test_write_registry_accepts_custom_dataset_metadata(
    tmp_path: Path, monkeypatch
) -> None:
    module = _load_generator_module()
    monkeypatch.setattr(module, "REPO_ROOT", tmp_path)

    task_dir = tmp_path / "datasets" / "swebenchpro-nohack" / "instance_demo"
    task_dir.mkdir(parents=True)
    registry_path = tmp_path / "registry.swebenchpro_nohack.json"

    module.write_registry(
        registry_path,
        [task_dir],
        dataset_name="swebenchpro-nohack",
        description="Hardened SWE-bench Pro tasks.",
    )

    registry = json.loads(registry_path.read_text())
    assert registry == [
        {
            "name": "swebenchpro-nohack",
            "version": "1.0",
            "description": "Hardened SWE-bench Pro tasks.",
            "tasks": [
                {
                    "name": "instance_demo",
                    "path": "datasets/swebenchpro-nohack/instance_demo",
                }
            ],
        }
    ]
