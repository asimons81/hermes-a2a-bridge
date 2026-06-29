from __future__ import annotations

import tomllib
from pathlib import Path

import yaml

import hermes_a2a_bridge


ROOT = Path(__file__).parents[1]


def test_package_plugin_and_runtime_versions_match():
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    plugin = yaml.safe_load((ROOT / "plugin.yaml").read_text(encoding="utf-8"))

    assert project["project"]["version"] == "0.4.7"
    assert plugin["version"] == "0.4.7"
    assert hermes_a2a_bridge.__version__ == "0.4.7"


def test_python_support_metadata_matches_ci_matrix():
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    ci = yaml.safe_load((ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8"))

    ci_versions = {
        str(entry["python-version"])
        for entry in ci["jobs"]["tests"]["strategy"]["matrix"]["include"]
    }
    classifiers = set(project["project"]["classifiers"])

    assert project["project"]["requires-python"] == ">=3.11,<4.0"
    assert "Programming Language :: Python :: 3.11" in classifiers
    assert "Programming Language :: Python :: 3.12" in classifiers
    assert "Programming Language :: Python :: 3.13" in classifiers
    assert "Programming Language :: Python :: 3.9" not in classifiers
    assert "Programming Language :: Python :: 3.10" not in classifiers
    assert {"3.11", "3.12", "3.13"} <= ci_versions
    assert not ({"3.9", "3.10"} & ci_versions)


def test_package_metadata_keeps_expected_entrypoints_dependencies_and_skill_data():
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    plugin = yaml.safe_load((ROOT / "plugin.yaml").read_text(encoding="utf-8"))

    expected_tools = [
        "a2a_discover_agent",
        "a2a_doctor_peer",
        "a2a_send_message",
        "a2a_get_task",
        "a2a_list_tasks",
        "a2a_cancel_task",
        "a2a_registry_add",
        "a2a_registry_list",
        "a2a_registry_remove",
    ]

    assert project["project"]["entry-points"]["hermes_agent.plugins"]["a2a-bridge"] == "hermes_a2a_bridge"
    assert project["project"]["scripts"]["hermes-a2a-bridge"] == "hermes_a2a_bridge.install_doctor:main"
    assert plugin["provides_cli"] == ["a2a"]
    assert plugin["provides_tools"] == expected_tools
    assert "skills/*/SKILL.md" in project["tool"]["setuptools"]["package-data"]["hermes_a2a_bridge"]

    deps = set(project["project"]["dependencies"])
    assert deps == {"aiohttp>=3.9,<4", "pydantic>=2.6,<3", "pyyaml>=6,<7"}
    assert not any("a2a-sdk" in dep.lower() for dep in deps)


def test_sdist_manifest_keeps_release_assets_and_prunes_incomplete_test_tree():
    manifest = (ROOT / "MANIFEST.in").read_text(encoding="utf-8")

    assert "include plugin.yaml" in manifest
    assert "recursive-include docs *.md" in manifest
    assert "recursive-include hermes_a2a_bridge/skills */SKILL.md" in manifest
    assert "prune tests" in manifest
    assert "prune build" in manifest
    assert "prune dist" in manifest
