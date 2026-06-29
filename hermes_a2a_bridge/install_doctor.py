"""Read-only installation and Hermes activation diagnostics."""

from __future__ import annotations

import argparse
import importlib
import importlib.metadata as metadata
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import yaml

PACKAGE_NAME = "hermes-a2a-bridge"
MODULE_NAME = "hermes_a2a_bridge"
ENTRY_POINT_GROUP = "hermes_agent.plugins"
ENTRY_POINT_NAME = "a2a-bridge"
ENTRY_POINT_VALUE = "hermes_a2a_bridge"
MANUAL_SNIPPET = "plugins:\n  enabled:\n    - a2a-bridge"


def default_hermes_config_path() -> Path:
    home = os.environ.get("HERMES_HOME")
    if home:
        return Path(home).expanduser() / "config.yaml"
    return Path.home() / ".hermes" / "config.yaml"


def _entry_points() -> list[metadata.EntryPoint]:
    entry_points = metadata.entry_points()
    if hasattr(entry_points, "select"):
        return list(entry_points.select(group=ENTRY_POINT_GROUP))
    return list(entry_points.get(ENTRY_POINT_GROUP, []))


def check_package() -> dict[str, Any]:
    try:
        module = importlib.import_module(MODULE_NAME)
    except Exception as exc:
        return {
            "importable": False,
            "version": None,
            "error": f"{type(exc).__name__}: {exc}",
        }

    version = getattr(module, "__version__", None)
    if version is None:
        try:
            version = metadata.version(PACKAGE_NAME)
        except metadata.PackageNotFoundError:
            version = None
    return {"importable": True, "version": version}


def check_entry_point() -> dict[str, Any]:
    matches = [ep for ep in _entry_points() if ep.name == ENTRY_POINT_NAME]
    selected = next((ep for ep in matches if ep.value == ENTRY_POINT_VALUE), matches[0] if matches else None)
    result = {
        "found": selected is not None,
        "group": ENTRY_POINT_GROUP,
        "name": ENTRY_POINT_NAME,
        "value": selected.value if selected else None,
    }
    if selected and selected.value != ENTRY_POINT_VALUE:
        result["warning"] = f"Expected {ENTRY_POINT_VALUE}, found {selected.value}"
    return result


def check_hermes_executable() -> dict[str, Any]:
    path = shutil.which("hermes")
    if not path:
        return {"found": False, "path": None, "version": None}

    result: dict[str, Any] = {"found": True, "path": path, "version": None}
    try:
        completed = subprocess.run(
            [path, "--version"],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except Exception as exc:
        result["version_error"] = f"{type(exc).__name__}: {exc}"
        return result

    version = (completed.stdout or completed.stderr).strip()
    if completed.returncode == 0 and version:
        result["version"] = version.splitlines()[0]
    elif version:
        result["version_error"] = version.splitlines()[0]
    else:
        result["version_error"] = f"hermes --version exited {completed.returncode}"
    return result


def check_activation(config_path: Path | None = None) -> dict[str, Any]:
    path = config_path or default_hermes_config_path()
    result: dict[str, Any] = {
        "checked": False,
        "enabled": False,
        "method": "plugins.enabled",
        "config_path": str(path),
        "manual_snippet": MANUAL_SNIPPET,
    }
    if not path.exists():
        result["reason"] = "config_not_found"
        return result

    try:
        loaded = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception as exc:
        result["reason"] = "config_unreadable"
        result["error"] = f"{type(exc).__name__}: {exc}"
        return result

    result["checked"] = True
    if not isinstance(loaded, dict):
        result["reason"] = "config_root_not_mapping"
        return result

    plugins = loaded.get("plugins", {})
    enabled = plugins.get("enabled") if isinstance(plugins, dict) else None
    if isinstance(enabled, list):
        result["enabled"] = ENTRY_POINT_NAME in {str(item) for item in enabled}
    else:
        result["reason"] = "plugins_enabled_not_list"
    return result


def build_install_doctor_report(config_path: Path | None = None) -> dict[str, Any]:
    package = check_package()
    entry_point = check_entry_point()
    hermes = check_hermes_executable()
    activation = check_activation(config_path)

    warnings: list[str] = []
    errors: list[str] = []
    next_steps: list[str] = []

    if not package["importable"]:
        errors.append("The hermes_a2a_bridge package is not importable in this Python environment.")
        next_steps.append("Install hermes-a2a-bridge into the same Python environment that runs Hermes.")
    if package["importable"] and not entry_point["found"]:
        errors.append("The hermes_agent.plugins entry point a2a-bridge was not found.")
        next_steps.append("Reinstall hermes-a2a-bridge in the same Python environment that runs Hermes.")
    if entry_point.get("warning"):
        warnings.append(entry_point["warning"])
    if not hermes["found"]:
        warnings.append("Hermes executable was not found on PATH; host smoke checks cannot run from this shell.")
    elif hermes.get("version_error"):
        warnings.append(f"hermes --version did not return a version: {hermes['version_error']}")
    if not activation["checked"]:
        warnings.append("Hermes config activation could not be checked; use the manual plugins.enabled snippet.")
    elif not activation["enabled"]:
        warnings.append("a2a-bridge is not currently listed in plugins.enabled.")

    if package["importable"] and entry_point["found"] and activation["enabled"]:
        next_steps.append("Ready: start a new Hermes process and run: hermes a2a doctor <agent-or-url> --json")
    elif package["importable"] and entry_point["found"]:
        next_steps.append("Add a2a-bridge to plugins.enabled.")
        next_steps.append("Then start a new Hermes process and run: hermes a2a doctor <agent-or-url> --json")

    return {
        "ok": package["importable"] and entry_point["found"] and not errors,
        "package": package,
        "entry_point": entry_point,
        "hermes": hermes,
        "activation": activation,
        "next_steps": next_steps,
        "warnings": warnings,
        "errors": errors,
    }


def render_human(report: dict[str, Any]) -> str:
    lines = ["Hermes A2A Bridge install doctor"]
    package = report["package"]
    entry_point = report["entry_point"]
    hermes = report["hermes"]
    activation = report["activation"]

    lines.append(f"Package import: {'ok' if package['importable'] else 'missing'}")
    if package.get("version"):
        lines.append(f"Package version: {package['version']}")
    lines.append(
        "Entry point: "
        + (
            f"found ({entry_point['group']} -> {entry_point['name']} = {entry_point['value']})"
            if entry_point["found"]
            else f"missing ({entry_point['group']} -> {entry_point['name']})"
        )
    )
    if hermes["found"]:
        suffix = f" ({hermes['version']})" if hermes.get("version") else ""
        lines.append(f"Hermes executable: {hermes['path']}{suffix}")
    else:
        lines.append("Hermes executable: not found on PATH")
    if activation["checked"]:
        state = "enabled" if activation["enabled"] else "not enabled"
        lines.append(f"Hermes activation: {state} via plugins.enabled")
        lines.append(f"Hermes config: {activation['config_path']}")
    else:
        lines.append(f"Hermes activation: not checked ({activation.get('reason', 'unknown')})")

    if not activation["enabled"]:
        lines.append("")
        lines.append("Manual activation for Hermes Agent v0.17.0:")
        lines.append(MANUAL_SNIPPET)

    if report["warnings"]:
        lines.append("")
        for warning in report["warnings"]:
            lines.append(f"Warning: {warning}")
    if report["errors"]:
        lines.append("")
        for error in report["errors"]:
            lines.append(f"Error: {error}")
    if report["next_steps"]:
        lines.append("")
        for step in report["next_steps"]:
            lines.append(f"Next: {step}")
    return "\n".join(lines)


def doctor_install_command(args: argparse.Namespace) -> int:
    report = build_install_doctor_report(Path(args.config).expanduser() if args.config else None)
    if args.json:
        print(json.dumps(report, indent=2, ensure_ascii=False))
    else:
        print(render_human(report))
    return 0 if not report["errors"] else 1


def register_standalone_cli(parser: argparse.ArgumentParser) -> None:
    sub = parser.add_subparsers(dest="command", required=True)
    doctor = sub.add_parser("doctor-install", help="Check package installation and Hermes plugin activation")
    doctor.add_argument("--config", help="Hermes config path to inspect instead of ~/.hermes/config.yaml")
    doctor.add_argument("--json", action="store_true", help="Emit JSON only")
    doctor.set_defaults(func=doctor_install_command)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="hermes-a2a-bridge")
    register_standalone_cli(parser)
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
