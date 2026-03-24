from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import List

from .detector import check_port, is_vite_port


def _read_package_json(package_path: Path) -> dict:
    try:
        return json.loads(package_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _frontend_command(frontend_dir: Path) -> List[str]:
    package_json = frontend_dir / "package.json"
    data = _read_package_json(package_json)
    scripts = data.get("scripts", {}) if isinstance(data, dict) else {}

    if "dev" in scripts:
        return ["npm", "run", "dev"]
    if "start" in scripts:
        return ["npm", "start"]
    return ["npm", "run", "dev"]


def _resolve_command(binary: str) -> str:
    if sys.platform.startswith("win") and not binary.lower().endswith(".cmd"):
        win_binary = f"{binary}.cmd"
        resolved = shutil.which(win_binary)
        if resolved:
            return resolved

    resolved = shutil.which(binary)
    return resolved or binary


def start_servers(frontend_dir: str = "frontend", backend_dir: str = "backend") -> None:
    """Launch frontend/backend when their expected directories exist."""
    frontend_path = Path(frontend_dir)
    backend_path = Path(backend_dir)

    if frontend_path.exists() and frontend_path.is_dir():
        if any(is_vite_port(port, timeout=0.5) for port in (5173, 5174, 5175, 5176, 5177, 3000, 8080)):
            print("[onelink] Frontend appears to already be running. Skipping launch.")
        else:
            cmd = _frontend_command(frontend_path)
            cmd[0] = _resolve_command(cmd[0])
            env = os.environ.copy()
            env["ONELINK"] = "1"
            subprocess.Popen(cmd, cwd=frontend_path, env=env)  # noqa: S603
    else:
        print("[onelink] Skipping frontend launch (frontend/ not found).")

    app_py = backend_path / "app.py"
    if app_py.exists() and backend_path.is_dir():
        if check_port(5000, timeout=0.5):
            print("[onelink] Backend appears to already be running. Skipping launch.")
        else:
            subprocess.Popen([sys.executable, "app.py"], cwd=backend_path)  # noqa: S603
    else:
        print("[onelink] Skipping backend launch (backend/app.py not found).")
