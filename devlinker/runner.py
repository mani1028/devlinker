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
from typing import Any, List

try:
    import docker  # type: ignore
except ImportError:  # pragma: no cover - optional fallback path
    docker = None

from .detector import check_port, is_vite_port

try:
    from rich.console import Console
    from rich.prompt import Prompt
except ImportError:  # pragma: no cover - fallback when rich is unavailable
    Console = None
    Prompt = None

_RICH_AVAILABLE = Console is not None
_CONSOLE = Console() if Console is not None else None


def _log(level: str, message: str) -> None:
    if _CONSOLE:
        icon_map = {
            "info": "ℹ",
            "ok": "✔",
            "warn": "⚠",
            "error": "✖",
        }
        style_map = {
            "info": "cyan",
            "ok": "green",
            "warn": "yellow",
            "error": "red",
        }
        icon = icon_map.get(level, "ℹ")
        style = style_map.get(level, "cyan")
        _CONSOLE.print(f"{icon} {message}", style=style)
        return

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


def _extract_port_mappings(ports_text: str) -> list[tuple[int, int]]:
    """
    Extracts host and container ports from docker ps output.
    Handles:
      - 0.0.0.0:8000->8000/tcp
      - [::]:8000->8000/tcp
      - :::8000->8000/tcp
      - 8000->8000/tcp
    
    Skips unmapped ports (e.g., "8000/tcp" without a host binding).
    """
    # Skip if no host mappings present (unmapped ports are not useful externally).
    if "->" not in ports_text:
        return []
    
    # This regex specifically captures the host port (Group 1) and container port (Group 2)
    # while optionally ignoring any IP binding prefix.
    pattern = re.compile(
        r"(?:(?:\d{1,3}(?:\.\d{1,3}){3}|\[[a-fA-F0-9:]+\]|:::):)?(\d+)->(\d+)/(?:tcp|udp)"
    )
    
    mappings: list[tuple[int, int]] = []
    for match in pattern.finditer(ports_text):
        host_port = int(match.group(1))
        container_port = int(match.group(2))
        mappings.append((host_port, container_port))
    return mappings


def _container_priority(name: str, container_port: int, default_container_port: int) -> int:
    score = 0
    lowered_name = name.lower()
    if "backend" in lowered_name:
        score += 6
    if any(token in lowered_name for token in ("api", "server", "svc", "service")):
        score += 3
    if container_port == default_container_port:
        score += 2

    project_hint = Path.cwd().name.lower()
    if len(project_hint) >= 3 and project_hint in lowered_name:
        score += 5

    return score


def _normalize_label_port(value: str | None) -> int | None:
    if not value:
        return None
    try:
        parsed = int(value)
    except ValueError:
        return None
    if 1 <= parsed <= 65535:
        return parsed
    return None


def _extract_port_mappings_from_docker_sdk(container: Any) -> list[tuple[int, int]]:
    mappings: list[tuple[int, int]] = []
    ports = (container.attrs or {}).get("NetworkSettings", {}).get("Ports", {})
    if not isinstance(ports, dict):
        return mappings

    for container_port_proto, bindings in ports.items():
        if not isinstance(container_port_proto, str):
            continue
        try:
            container_port = int(container_port_proto.split("/", 1)[0])
        except (ValueError, IndexError):
            continue
        if not bindings:
            continue
        if not isinstance(bindings, list):
            continue

        for binding in bindings:
            if not isinstance(binding, dict):
                continue
            host_port_str = binding.get("HostPort")
            if not host_port_str:
                continue
            try:
                host_port = int(host_port_str)
            except (TypeError, ValueError):
                continue
            mappings.append((host_port, container_port))
    return mappings


def _docker_sdk_backend_candidates(
    default_container_port: int = 5000,
    debug: bool = False,
) -> list[tuple[str, int, int]]:
    if docker is None:
        _debug_log(debug, "Docker SDK not installed; falling back to docker CLI parsing")
        return []

    try:
        client = docker.from_env()
        containers = client.containers.list()
    except Exception as exc:
        _debug_log(debug, f"Docker SDK unavailable ({exc}); falling back to docker CLI parsing")
        return []

    candidates: list[tuple[int, int, str, int, int, int]] = []
    for index, container in enumerate(containers):
        container_name = str(getattr(container, "name", "unknown"))
        labels = getattr(container, "labels", {}) or {}
        role_label = str(labels.get("devlinker.role", "")).strip().lower()
        preferred_container_port = _normalize_label_port(labels.get("devlinker.port"))
        if preferred_container_port is None:
            preferred_container_port = _normalize_label_port(labels.get("devlinker.backend.port"))

        mappings = _extract_port_mappings_from_docker_sdk(container)
        if not mappings:
            continue

        for host_port, container_port in mappings:
            priority = _container_priority(container_name, container_port, default_container_port)
            if role_label == "backend":
                priority += 20
            elif role_label:
                priority -= 2

            if preferred_container_port is not None:
                if container_port == preferred_container_port:
                    priority += 12
                else:
                    priority -= 6

            candidates.append((priority, index, container_name, host_port, container_port, preferred_container_port or 0))
            _debug_log(
                debug,
                (
                    f"SDK candidate: container='{container_name}', host={host_port}, "
                    f"container={container_port}, role={role_label or '-'}, "
                    f"label_port={preferred_container_port if preferred_container_port is not None else '-'}, "
                    f"score={priority}"
                ),
            )

    if not candidates:
        return []

    ranked = sorted(candidates, key=lambda item: (-item[0], item[1]))
    ordered: list[tuple[str, int, int]] = []
    seen: set[tuple[str, int, int]] = set()
    for _score, _index, name, host_port, container_port, _label_port in ranked:
        key = (name, host_port, container_port)
        if key in seen:
            continue
        seen.add(key)
        ordered.append(key)

    if ordered:
        first_name, first_host_port, first_container_port = ordered[0]
        _debug_log(
            debug,
            (
                f"Selected Docker SDK container '{first_name}' with host port {first_host_port} "
                f"(container port {first_container_port})"
            ),
        )

    return ordered


def _docker_cli_backend_candidates(
    default_container_port: int = 5000,
    debug: bool = False,
) -> list[tuple[str, int, int]]:
    try:
        output = subprocess.check_output(  # noqa: S603
            ["docker", "ps", "--format", "{{.Names}}\t{{.Ports}}"],
            stderr=subprocess.DEVNULL,
        ).decode("utf-8", errors="ignore")
    except Exception:
        return []

    _debug_log(debug, "docker ps port map output:")
    if debug:
        for raw_line in output.splitlines():
            _debug_log(True, raw_line)

    candidates: list[tuple[str, int, int, int]] = []
    for line_index, line in enumerate(output.splitlines()):
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

        mappings = _extract_port_mappings(ports)
        if not mappings:
            continue

        for host_port, container_port in mappings:
            candidates.append((name, host_port, container_port, line_index))
            _debug_log(
                debug,
                (
                    f"CLI candidate Docker mapping: container='{name}', "
                    f"host={host_port}, container={container_port}"
                ),
            )

    if not candidates:
        return []

    ranked = sorted(
        candidates,
        key=lambda item: (-_container_priority(item[0], item[2], default_container_port), item[3]),
    )
    ordered: list[tuple[str, int, int]] = []
    seen: set[tuple[str, int, int]] = set()
    for name, host_port, container_port, _line_index in ranked:
        key = (name, host_port, container_port)
        if key in seen:
            continue
        seen.add(key)
        ordered.append(key)

    return ordered


def get_docker_backend_candidates(
    default_container_port: int = 5000,
    debug: bool = False,
) -> list[tuple[str, int, int]]:
    sdk_candidates = _docker_sdk_backend_candidates(
        default_container_port=default_container_port,
        debug=debug,
    )
    if sdk_candidates:
        return sdk_candidates

    return _docker_cli_backend_candidates(
        default_container_port=default_container_port,
        debug=debug,
    )


def _choose_backend_candidate(
    local_port: int,
    docker_candidates: list[tuple[str, int, int]],
    debug: bool = False,
) -> tuple[str, int, str | None, int | None]:
    _log("info", "Multiple backends detected. Choose one:")
    print(f"  1) Local  (localhost:{local_port})")
    for index, (container_name, host_port, container_port) in enumerate(docker_candidates, start=2):
        print(f"  {index}) Docker ({container_name}) localhost:{host_port} -> {container_port}")
    max_opt = len(docker_candidates) + 1
    prompt_text = f"Select backend (1-{max_opt})"

    try:
        if _CONSOLE and Prompt:
            raw = Prompt.ask(f"{prompt_text} [Enter=1]", default="1")
        else:
            print(f"[INFO] {prompt_text} [Enter=1]: ", end="", flush=True)
            raw = input().strip()
    except EOFError:
        _debug_log(debug, "Interactive prompt unavailable (EOF); using local backend fallback")
        return "local", local_port, None, None

    if raw == "":
        return "local", local_port, None, None

    try:
        selection = int(raw)
    except ValueError:
        _log("warn", "Invalid selection; using local backend")
        return "local", local_port, None, None

    if selection == 1:
        return "local", local_port, None, None

    docker_index = selection - 2
    if 0 <= docker_index < len(docker_candidates):
        container_name, host_port, container_port = docker_candidates[docker_index]
        return "docker", host_port, container_name, container_port

    _log("warn", "Selection out of range; using local backend")
    return "local", local_port, None, None


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
    interactive: bool = True,
    debug: bool = False,
) -> int | None:
    started = time.perf_counter()

    if override_port is not None:
        _log("info", f"Using backend port override: {override_port}")
        _log("info", f"Backend detected in {time.perf_counter() - started:.1f}s")
        return override_port

    _log("info", "Checking backend...")
    _debug_log(debug, f"Scanned local port: {default_port}")

    local_open = is_port_open(default_port)
    if not local_open:
        _log("warn", f"Backend not found on port {default_port}")

    _log("info", "Checking Docker containers...")
    _debug_log(debug, f"Scanned Docker container target port: {default_port}")

    docker_candidates = get_docker_backend_candidates(default_container_port=default_port, debug=debug)

    if local_open and docker_candidates:
        if interactive:
            choice_kind, chosen_port, container_name, container_port = _choose_backend_candidate(
                local_port=default_port,
                docker_candidates=docker_candidates,
                debug=debug,
            )
            if choice_kind == "docker":
                _log("ok", f"Backend detected (Docker: {container_name}) -> port {chosen_port}")
                if container_port is not None:
                    _debug_log(debug, f"Selected Docker container port: {container_port}")
                _log("info", f"Backend detected in {time.perf_counter() - started:.1f}s")
                return chosen_port

            _log("ok", f"Backend detected (Local) -> port {chosen_port}")
            _log("info", f"Backend detected in {time.perf_counter() - started:.1f}s")
            return chosen_port

        primary_name, primary_host_port, _primary_container_port = docker_candidates[0]
        _log(
            "info",
            (
                f"Multiple backends detected; auto-selecting Local (localhost:{default_port}). "
                f"Docker candidate: {primary_name} -> localhost:{primary_host_port}"
            ),
        )
        _debug_log(debug, "Using local-first priority (interactive selection disabled)")
        _log("ok", f"Backend detected (Local) -> port {default_port}")
        _log("info", f"Backend detected in {time.perf_counter() - started:.1f}s")
        return default_port

    if local_open:
        _log("ok", f"Backend detected (Local) -> port {default_port}")
        _log("info", f"Backend detected in {time.perf_counter() - started:.1f}s")
        return default_port

    if docker_candidates:
        container_name, docker_port, container_port = docker_candidates[0]
        if len(docker_candidates) > 1:
            _log("warn", f"Multiple Docker containers published ports ({len(docker_candidates)} candidates)")
            _log("info", f"Using: {container_name}")
        if _wait_for_port(docker_port, retries=5, delay_seconds=1.0, debug=debug):
            _log("ok", f"Backend detected (Docker: {container_name}) -> port {docker_port}")
            _debug_log(debug, f"Selected Docker container port: {container_port}")
            _log("info", f"Backend detected in {time.perf_counter() - started:.1f}s")
            return docker_port
        _log("warn", f"Docker mapped port {docker_port} found but backend is not ready yet")

    _log("error", "No backend found")
    print("Checked:")
    print(f"- localhost:{default_port}")
    print("- Docker containers")
    print("Tip:")
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

    frontend_running = any(is_vite_port(port, timeout=0.5) for port in (5173, 5174, 5175, 5176, 5177, 3000, 8080))
    backend_running = check_port(5000, timeout=0.5)

    if frontend_path.exists() and frontend_path.is_dir():
        if frontend_running:
            _log("ok", "Frontend already running; skipping launch.")
        else:
            cmd = _frontend_command(frontend_path)
            cmd[0] = _resolve_command(cmd[0])
            env = os.environ.copy()
            env["ONELINK"] = "1"
            subprocess.Popen(cmd, cwd=frontend_path, env=env)  # noqa: S603
            _log("ok", f"Frontend launch requested: {' '.join(cmd)}")
    else:
        if frontend_running:
            _log("ok", "Frontend already running; skipping launch.")
        else:
            _log("warn", "frontend/ not found; skipping frontend launch.")
            _log("info", "Tip: run your frontend manually (example: npm run dev).")

    if backend_path.exists() and backend_path.is_dir():
        if backend_running:
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
        if backend_running:
            _log("ok", "Backend already running on port 5000; skipping launch.")
        else:
            _log("warn", "backend/ not found; skipping backend launch.")
            _log("info", "Tip: run your backend manually (example: python app.py).")
