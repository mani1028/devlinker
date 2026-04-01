import os
import json

import yaml

def load_config(config_path: str = "devlinker.yaml") -> dict:
    candidates = [config_path, "devlinker.yml", "devlinker.json"]
    selected = next((path for path in candidates if os.path.exists(path)), None)
    if not selected:
        return {}

    with open(selected, "r", encoding="utf-8") as handle:
        if selected.endswith(".json"):
            data = json.load(handle)
        else:
            data = yaml.safe_load(handle)
    return data or {}
