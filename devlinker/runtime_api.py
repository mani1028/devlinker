from __future__ import annotations

import os
from typing import Any

import requests

DEFAULT_PROXY_URL = "http://127.0.0.1:8000"
REQUEST_TIMEOUT_SECONDS = 2.5
PROXY_DISCOVERY_TIMEOUT_SECONDS = 0.25
PROXY_CANDIDATE_PORTS = tuple(list(range(8000, 8011)) + [18000])


def _is_devlinker_proxy(base_url: str) -> bool:
    try:
        response = requests.get(
            f"{base_url}/__devlinker/api/status",
            timeout=PROXY_DISCOVERY_TIMEOUT_SECONDS,
        )
        if response.status_code != 200:
            return False
        payload = response.json()
    except Exception:
        return False
    return isinstance(payload, dict) and "proxy_port" in payload


def proxy_base_url() -> str:
    configured = os.getenv("DEVLINKER_PROXY_URL", "").strip()
    if configured:
        return configured.rstrip("/")

    configured_port = os.getenv("DEVLINKER_PROXY_PORT", "").strip()
    if configured_port.isdigit():
        return f"http://127.0.0.1:{configured_port}"

    for port in PROXY_CANDIDATE_PORTS:
        candidate = f"http://127.0.0.1:{port}"
        if _is_devlinker_proxy(candidate):
            return candidate

    return DEFAULT_PROXY_URL


def fetch_proxy_json(endpoint: str) -> dict[str, Any]:
    base_url = proxy_base_url()
    url = f"{base_url}{endpoint}"
    response = requests.get(url, timeout=REQUEST_TIMEOUT_SECONDS)
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict):
        raise ValueError("Unexpected API response shape")
    return payload


def fetch_logs(limit: int = 50) -> dict[str, Any]:
    payload = fetch_proxy_json("/__devlinker/logs")
    items = payload.get("items")
    if isinstance(items, list):
        payload["items"] = items[-max(1, limit) :]
    return payload


def fetch_issues() -> dict[str, Any]:
    return fetch_proxy_json("/__devlinker/api/issues")


def fetch_status() -> dict[str, Any]:
    return fetch_proxy_json("/__devlinker/api/status")
