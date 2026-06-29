"""Standalone command entry point for ``python -m hermes_a2a_bridge``."""

from __future__ import annotations

from .install_doctor import main


if __name__ == "__main__":
    raise SystemExit(main())
