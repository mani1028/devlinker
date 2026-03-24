from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import List

from .detector import check_port, is_vite_port


def _log(level: str, message: str) -> None:
    prefix_map = {
        "info": "[INFO]",
        "ok": "[OK]",
        "warn": "[WARN]",
        "error": "[ERROR]",
    }
    prefix = prefix_map.get(level, "[INFO]")
    print(f"{prefix} {message}")


def _detect_backend_mode(backend_path: Path) -> str | None:
    compose_files = (
        "docker-compose.yml",
        "docker-compose.yaml",
        "compose.yml",
        "compose.yaml",
    )

    if any((backend_path / filename).exists() for filename in compose_files):
        return "docker-compose"
    if (backend_path / "Dockerfile").exists():
        return "docker"
    if (backend_path / "package.json").exists():
        return "node"
    if (backend_path / "requirements.txt").exists() or (backend_path / "app.py").exists():
        return "python"
    return None


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


def _start_backend_docker_compose(backend_path: Path) -> None:
    docker = _resolve_command("docker")
    subprocess.Popen([docker, "compose", "up", "--build"], cwd=backend_path)  # noqa: S603


def _start_backend_docker(backend_path: Path) -> None:
    docker = _resolve_command("docker")
    image_name = "devlinker-backend"
    subprocess.run(  # noqa: S603
        [docker, "build", "-t", image_name, "."],
        cwd=backend_path,
        check=True,
    )
    subprocess.Popen([docker, "run", "--rm", "-p", "5000:5000", image_name])  # noqa: S603


def _start_backend_node(backend_path: Path) -> None:
    cmd = ["npm", "run", "dev"]
    package_json = _read_package_json(backend_path / "package.json")
    scripts = package_json.get("scripts", {}) if isinstance(package_json, dict) else {}
    if "dev" not in scripts and "start" in scripts:
        cmd = ["npm", "start"]

    cmd[0] = _resolve_command(cmd[0])
    subprocess.Popen(cmd, cwd=backend_path)  # noqa: S603


def _start_backend_python(backend_path: Path) -> None:
    app_py = backend_path / "app.py"
    if not app_py.exists():
        _log("warn", "Python backend detected, but backend/app.py is missing.")
        return

    subprocess.Popen([sys.executable, "app.py"], cwd=backend_path)  # noqa: S603


def start_servers(
    frontend_dir: str = "frontend",
    backend_dir: str = "backend",
    auto_start_docker: bool = False,
) -> None:
    """Launch frontend/backend when their expected directories exist."""
    frontend_path = Path(frontend_dir)
    backend_path = Path(backend_dir)

    if frontend_path.exists() and frontend_path.is_dir():
        if any(is_vite_port(port, timeout=0.5) for port in (5173, 5174, 5175, 5176, 5177, 3000, 8080)):
            _log("ok", "Frontend already running; skipping launch.")
        else:
            cmd = _frontend_command(frontend_path)
            cmd[0] = _resolve_command(cmd[0])
            env = os.environ.copy()
            env["ONELINK"] = "1"
            subprocess.Popen(cmd, cwd=frontend_path, env=env)  # noqa: S603
            _log("ok", "Frontend launch requested.")
    else:
        _log("warn", "frontend/ not found; skipping frontend launch.")

    if backend_path.exists() and backend_path.is_dir():
        if check_port(5000, timeout=0.5):
            _log("ok", "Backend already running on port 5000; skipping launch.")
            return

        backend_mode = _detect_backend_mode(backend_path)
        if backend_mode == "docker-compose":
            if not auto_start_docker:
                _log("warn", "Docker Compose backend detected.")
                _log("info", "Next step: run 'docker compose up --build' in backend/.")
                _log("info", "Tip: use 'devlinker --docker' to auto-start Docker.")
                return
            if shutil.which("docker") is None:
                _log("error", "Docker Compose backend detected, but Docker is not on PATH.")
                return
            _log("info", "Starting backend with Docker Compose (--docker enabled)...")
            _start_backend_docker_compose(backend_path)
            _log("ok", "Docker Compose launch requested.")
        elif backend_mode == "docker":
            if not auto_start_docker:
                _log("warn", "Docker backend detected.")
                _log(
                    "info",
                    "Next step: run 'docker build -t devlinker-backend .' then 'docker run --rm -p 5000:5000 devlinker-backend' in backend/.",
                )
                _log("info", "Tip: use 'devlinker --docker' to auto-start Docker.")
                return
            if shutil.which("docker") is None:
                _log("error", "Docker backend detected, but Docker is not on PATH.")
                return
            _log("info", "Starting backend with Docker (--docker enabled)...")
            try:
                _start_backend_docker(backend_path)
                _log("ok", "Docker launch requested.")
            except subprocess.CalledProcessError as exc:
                _log("error", f"Docker build failed: {exc}")
        elif backend_mode == "node":
            _log("info", "Node backend detected; starting...")
            _start_backend_node(backend_path)
            _log("ok", "Node backend launch requested.")
        elif backend_mode == "python":
            _log("info", "Python backend detected; starting...")
            _start_backend_python(backend_path)
            _log("ok", "Python backend launch requested.")
        else:
            _log("warn", "No supported backend runtime detected; skipping backend launch.")
    else:
        _log("warn", "backend/ not found; skipping backend launch.")
