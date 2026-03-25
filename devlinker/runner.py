from __future__ import annotations

import json
import os
import re
import shutil
import socket
import subprocess
import sys
import time
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


def _debug_log(enabled: bool, message: str) -> None:
    if enabled:
        print(f"[DEBUG] {message}")


def is_port_open(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(1)
        return sock.connect_ex(("localhost", port)) == 0


def _extract_host_port(ports_text: str, container_port: int) -> int | None:
    # Covers typical Docker mappings: 0.0.0.0:32768->5000/tcp, [::]:32768->5000/tcp
    pattern = rf"(?:0\.0\.0\.0|127\.0\.0\.1|\[::\]|::):(\d+)->{container_port}/tcp"
    match = re.search(pattern, ports_text)
    if match:
        return int(match.group(1))
    return None


def get_docker_backend_port(
    default_container_port: int = 5000,
    debug: bool = False,
) -> tuple[int, str, int] | None:
    try:
        output = subprocess.check_output(  # noqa: S603
            ["docker", "ps", "--format", "{{.Names}}\t{{.Ports}}"],
            stderr=subprocess.DEVNULL,
        ).decode("utf-8", errors="ignore")
    except Exception:
        return None

    _debug_log(debug, "docker ps port map output:")
    if debug:
        for raw_line in output.splitlines():
            _debug_log(True, raw_line)

    candidates: list[tuple[str, int]] = []
    for line in output.splitlines():
        stripped = line.strip()
        if not stripped:
            continue

        if "\t" in stripped:
            name, ports = stripped.split("\t", 1)
        else:
            parts = stripped.split(maxsplit=1)
            if len(parts) != 2:
                continue
            name, ports = parts[0], parts[1]

        host_port = _extract_host_port(ports, default_container_port)
        if host_port is not None:
            candidates.append((name, host_port))

    if not candidates:
        return None

    # Prefer container names that look like backend services.
    for name, port in candidates:
        if "backend" in name.lower():
            _debug_log(debug, f"Selected Docker backend container '{name}' on host port {port}")
            return port, name, len(candidates)

    # docker ps is already newest-first; fallback to first match.
    name, port = candidates[0]
    _debug_log(debug, f"Selected first Docker match '{name}' on host port {port}")
    return port, name, len(candidates)


def _wait_for_port(port: int, retries: int = 5, delay_seconds: float = 1.0, debug: bool = False) -> bool:
    for attempt in range(1, retries + 1):
        if is_port_open(port):
            return True
        _debug_log(debug, f"Port {port} not open yet (attempt {attempt}/{retries})")
        time.sleep(delay_seconds)
    return False


def detect_backend_port(
    default_port: int = 5000,
    override_port: int | None = None,
    debug: bool = False,
) -> int | None:
    started = time.perf_counter()

    if override_port is not None:
        _log("info", f"Using backend port override: {override_port}")
        _log("info", f"Backend detected in {time.perf_counter() - started:.1f}s")
        return override_port

    _log("info", "Checking backend...")
    _debug_log(debug, f"Scanned local port: {default_port}")
    if is_port_open(default_port):
        _log("ok", f"Backend detected (Local) -> port {default_port}")
        _log("info", f"Backend detected in {time.perf_counter() - started:.1f}s")
        return default_port

    _log("warn", f"Backend not found on port {default_port}")
    _log("info", "Checking Docker containers...")
    _debug_log(debug, f"Scanned Docker container target port: {default_port}")

    docker_match = get_docker_backend_port(default_container_port=default_port, debug=debug)
    if docker_match is not None:
        docker_port, container_name, match_count = docker_match
        if match_count > 1:
            _log("warn", "Multiple backend containers found")
            _log("info", f"Using: {container_name}")
        if _wait_for_port(docker_port, retries=5, delay_seconds=1.0, debug=debug):
            _log("ok", f"Backend detected (Docker) -> port {docker_port}")
            _log("info", f"Backend detected in {time.perf_counter() - started:.1f}s")
            return docker_port
        _log("warn", f"Docker mapped port {docker_port} found but backend is not ready yet")

    _log("error", "Backend not detected")
    print("Checked:")
    print(f"- localhost:{default_port}")
    print("- Docker containers")
    print("Next step:")
    print("  - Start Flask: python app.py")
    print("  - OR expose Docker port: -p 5000:5000")
    return None


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
