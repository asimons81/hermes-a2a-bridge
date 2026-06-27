from __future__ import annotations

import tomllib
from pathlib import Path

import yaml

import hermes_a2a_bridge


ROOT = Path(__file__).parents[1]


def test_package_plugin_and_runtime_versions_match():
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    plugin = yaml.safe_load((ROOT / "plugin.yaml").read_text(encoding="utf-8"))

    assert project["project"]["version"] == "0.4.6"
    assert plugin["version"] == "0.4.6"
    assert hermes_a2a_bridge.__version__ == "0.4.6"

def test_package_metadata_keeps_expected_entrypoints_dependencies_and_skill_data():
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    plugin = yaml.safe_load((ROOT / "plugin.yaml").read_text(encoding="utf-8"))

    assert project["project"]["entry-points"]["hermes_agent.plugins"]["a2a-bridge"] == "hermes_a2a_bridge"
    assert plugin["provides_cli"] == ["a2a"]
    assert "a2a_send_message" in plugin["provides_tools"]
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
