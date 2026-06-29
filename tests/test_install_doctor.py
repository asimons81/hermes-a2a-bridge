from __future__ import annotations

import argparse
import json
from types import SimpleNamespace

import yaml

from hermes_a2a_bridge import install_doctor


class FakeEntryPoint:
    def __init__(self, name: str, value: str):
        self.name = name
        self.value = value


def _mock_entry_points(monkeypatch, entries):
    monkeypatch.setattr(install_doctor, "_entry_points", lambda: entries)


def _mock_package(monkeypatch, *, importable=True, version="0.4.7"):
    monkeypatch.setattr(
        install_doctor,
        "check_package",
        lambda: {"importable": importable, "version": version},
    )


def _mock_hermes(monkeypatch, *, found=False, path=None, version=None, version_error=None):
    payload = {"found": found, "path": path, "version": version}
    if version_error:
        payload["version_error"] = version_error
    monkeypatch.setattr(install_doctor, "check_hermes_executable", lambda: payload)


def test_package_entry_point_found(monkeypatch):
    _mock_entry_points(monkeypatch, [FakeEntryPoint("a2a-bridge", "hermes_a2a_bridge")])

    result = install_doctor.check_entry_point()

    assert result == {
        "found": True,
        "group": "hermes_agent.plugins",
        "name": "a2a-bridge",
        "value": "hermes_a2a_bridge",
    }


def test_package_entry_point_missing(monkeypatch):
    _mock_entry_points(monkeypatch, [])

    result = install_doctor.check_entry_point()

    assert result["found"] is False
    assert result["name"] == "a2a-bridge"


def test_hermes_executable_found_and_version_success(monkeypatch):
    monkeypatch.setattr(install_doctor.shutil, "which", lambda name: "/usr/bin/hermes")
    monkeypatch.setattr(
        install_doctor.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(returncode=0, stdout="Hermes Agent v0.17.0\n", stderr=""),
    )

    result = install_doctor.check_hermes_executable()

    assert result["found"] is True
    assert result["path"] == "/usr/bin/hermes"
    assert result["version"] == "Hermes Agent v0.17.0"


def test_hermes_executable_not_found(monkeypatch):
    monkeypatch.setattr(install_doctor.shutil, "which", lambda name: None)

    assert install_doctor.check_hermes_executable() == {"found": False, "path": None, "version": None}


def test_hermes_version_command_failure(monkeypatch):
    monkeypatch.setattr(install_doctor.shutil, "which", lambda name: "/usr/bin/hermes")
    monkeypatch.setattr(
        install_doctor.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(returncode=2, stdout="", stderr="not today\n"),
    )

    result = install_doctor.check_hermes_executable()

    assert result["found"] is True
    assert result["version"] is None
    assert result["version_error"] == "not today"


def test_config_activation_enabled(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump({"plugins": {"enabled": ["a2a-bridge"]}}), encoding="utf-8")

    result = install_doctor.check_activation(path)

    assert result["checked"] is True
    assert result["enabled"] is True
    assert result["method"] == "plugins.enabled"


def test_config_activation_not_enabled(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump({"plugins": {"enabled": ["other"]}}), encoding="utf-8")

    result = install_doctor.check_activation(path)

    assert result["checked"] is True
    assert result["enabled"] is False


def test_config_unknown(tmp_path):
    result = install_doctor.check_activation(tmp_path / "missing.yaml")

    assert result["checked"] is False
    assert result["enabled"] is False
    assert result["reason"] == "config_not_found"


def test_config_unreadable_yaml(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text("plugins: [", encoding="utf-8")

    result = install_doctor.check_activation(path)

    assert result["checked"] is False
    assert result["reason"] == "config_unreadable"


def test_json_output_stable(monkeypatch, tmp_path, capsys):
    _mock_package(monkeypatch)
    _mock_entry_points(monkeypatch, [FakeEntryPoint("a2a-bridge", "hermes_a2a_bridge")])
    _mock_hermes(monkeypatch, found=True, path="/usr/bin/hermes", version="Hermes Agent v0.17.0")
    config = tmp_path / "config.yaml"
    config.write_text(yaml.safe_dump({"plugins": {"enabled": ["a2a-bridge"]}}), encoding="utf-8")

    code = install_doctor.doctor_install_command(argparse.Namespace(config=str(config), json=True))
    payload = json.loads(capsys.readouterr().out)

    assert code == 0
    assert payload["ok"] is True
    assert payload["package"] == {"importable": True, "version": "0.4.7"}
    assert payload["entry_point"]["name"] == "a2a-bridge"
    assert payload["activation"]["enabled"] is True
    assert payload["next_steps"] == [
        "Ready: start a new Hermes process and run: hermes a2a doctor <agent-or-url> --json"
    ]


def test_human_output_concise(monkeypatch, tmp_path, capsys):
    _mock_package(monkeypatch)
    _mock_entry_points(monkeypatch, [FakeEntryPoint("a2a-bridge", "hermes_a2a_bridge")])
    _mock_hermes(monkeypatch)
    config = tmp_path / "config.yaml"
    config.write_text(yaml.safe_dump({"plugins": {"enabled": []}}), encoding="utf-8")

    code = install_doctor.doctor_install_command(argparse.Namespace(config=str(config), json=False))
    output = capsys.readouterr().out

    assert code == 0
    assert "Hermes A2A Bridge install doctor" in output
    assert "Manual activation for Hermes Agent v0.17.0" in output
    assert "plugins:\n  enabled:\n    - a2a-bridge" in output
    assert len(output.splitlines()) < 25


def test_no_config_mutation_by_default(monkeypatch, tmp_path):
    _mock_package(monkeypatch)
    _mock_entry_points(monkeypatch, [FakeEntryPoint("a2a-bridge", "hermes_a2a_bridge")])
    _mock_hermes(monkeypatch)
    config = tmp_path / "config.yaml"
    original = yaml.safe_dump({"plugins": {"enabled": []}, "token": "secret-token"})
    config.write_text(original, encoding="utf-8")

    install_doctor.build_install_doctor_report(config)

    assert config.read_text(encoding="utf-8") == original


def test_no_secrets_leaked(monkeypatch, tmp_path):
    _mock_package(monkeypatch)
    _mock_entry_points(monkeypatch, [FakeEntryPoint("a2a-bridge", "hermes_a2a_bridge")])
    _mock_hermes(monkeypatch)
    config = tmp_path / "config.yaml"
    config.write_text(
        yaml.safe_dump({"plugins": {"enabled": []}, "auth_token": "super-secret-token"}),
        encoding="utf-8",
    )

    report = install_doctor.build_install_doctor_report(config)
    output = json.dumps(report) + install_doctor.render_human(report)

    assert "super-secret-token" not in output


def test_activation_missing_warns_but_exits_success(monkeypatch, tmp_path):
    _mock_package(monkeypatch)
    _mock_entry_points(monkeypatch, [FakeEntryPoint("a2a-bridge", "hermes_a2a_bridge")])
    _mock_hermes(monkeypatch)
    config = tmp_path / "config.yaml"
    config.write_text(yaml.safe_dump({"plugins": {"enabled": []}}), encoding="utf-8")

    report = install_doctor.build_install_doctor_report(config)

    assert report["ok"] is True
    assert report["errors"] == []
    assert "a2a-bridge is not currently listed in plugins.enabled." in report["warnings"]


def test_real_error_returns_nonzero(monkeypatch, tmp_path, capsys):
    _mock_package(monkeypatch)
    _mock_entry_points(monkeypatch, [])
    _mock_hermes(monkeypatch)

    code = install_doctor.doctor_install_command(
        argparse.Namespace(config=str(tmp_path / "missing.yaml"), json=True)
    )
    payload = json.loads(capsys.readouterr().out)

    assert code == 1
    assert payload["ok"] is False
    assert payload["errors"] == ["The hermes_agent.plugins entry point a2a-bridge was not found."]


def test_standalone_cli_parses_doctor_install(monkeypatch, tmp_path, capsys):
    _mock_package(monkeypatch)
    _mock_entry_points(monkeypatch, [FakeEntryPoint("a2a-bridge", "hermes_a2a_bridge")])
    _mock_hermes(monkeypatch)
    config = tmp_path / "config.yaml"
    config.write_text(yaml.safe_dump({"plugins": {"enabled": ["a2a-bridge"]}}), encoding="utf-8")

    code = install_doctor.main(["doctor-install", "--config", str(config), "--json"])
    payload = json.loads(capsys.readouterr().out)

    assert code == 0
    assert payload["activation"]["enabled"] is True
