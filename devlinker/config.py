import os
import json

try:
    import yaml
except ImportError:  # pragma: no cover - optional dependency fallback
    yaml = None


def _normalize_api_prefix(value: object) -> str:
    if not isinstance(value, str):
        return "/api"
    prefix = value.strip()
    if not prefix:
        return "/api"
    if not prefix.startswith("/"):
        prefix = f"/{prefix}"
    if len(prefix) > 1 and prefix.endswith("/"):
        prefix = prefix.rstrip("/")
    return prefix or "/api"


def _normalize_config(data: dict) -> dict:
    normalized = dict(data)
    backend_entry = normalized.get("backend_entry")
    if not backend_entry:
        backend_entry = normalized.get("entry_point")
    if isinstance(backend_entry, str) and backend_entry.strip():
        normalized["backend_entry"] = backend_entry.strip()

    if "api_prefix" in normalized:
        normalized["api_prefix"] = _normalize_api_prefix(normalized.get("api_prefix"))

    if "strip_prefix" in normalized:
        normalized["strip_prefix"] = bool(normalized.get("strip_prefix"))

    return normalized


def load_config(config_path: str = "devlinker.yaml") -> dict:
    candidates = [config_path, "devlinker.yml", "devlinker.json"]
    if yaml is None:
        candidates = [path for path in candidates if path.endswith(".json")]
    selected = next((path for path in candidates if os.path.exists(path)), None)
    if not selected:
        return {}

    with open(selected, "r", encoding="utf-8") as handle:
        if selected.endswith(".json"):
            data = json.load(handle)
        else:
            if yaml is None:
                return {}
            data = yaml.safe_load(handle)
    return _normalize_config(data or {})
