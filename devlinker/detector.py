from __future__ import annotations

import time
from typing import Iterable, Optional, Tuple

import requests


def check_port(port: int, timeout: float = 1.0) -> bool:
    """Return True when an HTTP service responds on localhost:port."""
    try:
        response = requests.get(f"http://127.0.0.1:{port}", timeout=timeout)
        return response.status_code < 500
    except requests.RequestException:
        return False


def is_vite_port(port: int, timeout: float = 1.0) -> bool:
    """Return True when port looks like a Vite dev server."""
    try:
        response = requests.get(f"http://127.0.0.1:{port}/@vite/client", timeout=timeout)
        if response.status_code != 200:
            return False

        content_type = response.headers.get("content-type", "").lower()
        return "javascript" in content_type or "vite" in response.text[:400].lower()
    except requests.RequestException:
        return False


def _pick_open_port(
    candidates: Iterable[int],
    excluded: Optional[int] = None,
    checker=check_port,
) -> Optional[int]:
    for port in candidates:
        if excluded is not None and port == excluded:
            continue
        if checker(port):
            return port
    return None


def _ordered_unique_ports(*port_groups: Iterable[int]) -> list[int]:
    ordered: list[int] = []
    seen: set[int] = set()
    for group in port_groups:
        for port in group:
            if port in seen:
                continue
            seen.add(port)
            ordered.append(port)
    return ordered


def detect_ports(
    frontend: Optional[int] = None,
    backend: Optional[int] = None,
    retries: int = 12,
    delay_seconds: float = 1.0,
) -> Tuple[Optional[int], Optional[int]]:
    """Detect frontend and backend ports with retry support for slow startups."""
    frontend_ports = _ordered_unique_ports(
        range(5173, 5191),
        (3000, 4173, 8080),
    )
    backend_ports = _ordered_unique_ports(
        (5000, 8000, 8001, 8080, 8081, 3001),
    )

    selected_frontend = frontend
    selected_backend = backend

    for _ in range(max(retries, 1)):
        if selected_frontend is None:
            selected_frontend = _pick_open_port(frontend_ports, checker=is_vite_port)
        if selected_backend is None:
            selected_backend = _pick_open_port(backend_ports, excluded=selected_frontend)

        if selected_frontend is not None and selected_backend is not None:
            break

        time.sleep(delay_seconds)

    return selected_frontend, selected_backend
