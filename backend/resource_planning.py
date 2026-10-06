"""Live feature-flag lookup shared by the app and resource-planning domains."""
from __future__ import annotations

import json
import os
from pathlib import Path
import sys
from typing import Mapping


def _env_value() -> bool | None:
    value = os.environ.get("IDEVGEN_RESOURCE_PLANNING_ENABLED")
    if value is None:
        return None
    return value.strip().lower() not in ("0", "false", "no", "off")


def _config_value(config: Mapping[str, object]) -> bool:
    return bool(config.get("resource_planning_enabled", True))


def _standalone_config_value() -> bool:
    path = Path(
        os.environ.get("IDEVGEN_CONFIG")
        or Path(__file__).resolve().parents[1] / "config.json"
    )
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return True
    except (OSError, UnicodeError, json.JSONDecodeError):
        return False
    return _config_value(value) if isinstance(value, dict) else False


def is_enabled(config: Mapping[str, object] | None = None) -> bool:
    """Return the current flag without creating or mutating configuration."""
    override = _env_value()
    if override is not None:
        return override
    if config is not None:
        return _config_value(config)

    for module_name in ("main", "backend.main"):
        app_module = sys.modules.get(module_name)
        if app_module is None:
            continue
        gate = getattr(app_module, "is_resource_planning_enabled", None)
        if callable(gate):
            return bool(gate())
        current_config = getattr(app_module, "CONFIG", None)
        if isinstance(current_config, Mapping):
            return _config_value(current_config)
    return _standalone_config_value()


if __name__ == "backend.resource_planning" and "resource_planning" not in sys.modules:
    sys.modules["resource_planning"] = sys.modules[__name__]
elif __name__ == "resource_planning" and "backend.resource_planning" not in sys.modules:
    sys.modules["backend.resource_planning"] = sys.modules[__name__]
