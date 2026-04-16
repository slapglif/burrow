from __future__ import annotations

import json
from typing import Any


_REQUIRED_FIELDS = {
    "click": {"x", "y"},
    "double_click": {"x", "y"},
    "move": {"x", "y"},
    "drag": {"x", "y", "to_x", "to_y"},
    "scroll": {"dx", "dy"},
    "key": {"key"},
    "hotkey": {"keys"},
    "type_text": {"text"},
    "clipboard_copy": set(),
    "clipboard_cut": set(),
    "clipboard_paste": set(),
    "clipboard_paste_text": {"text"},
    "select_all": set(),
    "snapshot_request": set(),
}


_BUTTONS = {"left", "middle", "right"}


def _normalize_modifiers(modifiers: Any) -> list[str]:
    return [str(modifier) for modifier in list(modifiers or [])]


def _with_optional_target_fields(payload: dict[str, Any], action: dict[str, Any]) -> dict[str, Any]:
    for field in ("display", "display_id", "target"):
        if field in payload:
            action[field] = payload[field]
    return action


def normalize_action(payload: dict[str, Any]) -> dict[str, Any]:
    action_type = payload.get("type")
    if action_type not in _REQUIRED_FIELDS:
        raise ValueError(f"Unsupported action type: {action_type}")

    missing = [field for field in _REQUIRED_FIELDS[action_type] if field not in payload]
    if missing:
        raise ValueError(f"Missing required fields for {action_type}: {', '.join(missing)}")

    if action_type == "click":
        button = str(payload.get("button", "left"))
        if button not in _BUTTONS:
            raise ValueError(f"Unsupported mouse button: {button}")
        return {
            "type": "click",
            "x": int(payload["x"]),
            "y": int(payload["y"]),
            "button": button,
            "count": max(1, int(payload.get("count", 1))),
        }
    if action_type == "double_click":
        return normalize_action({**payload, "type": "click", "count": 2})
    if action_type == "move":
        return {"type": "move", "x": int(payload["x"]), "y": int(payload["y"])}
    if action_type == "drag":
        button = str(payload.get("button", "left"))
        if button not in _BUTTONS:
            raise ValueError(f"Unsupported mouse button: {button}")
        return {
            "type": "drag",
            "x": int(payload["x"]),
            "y": int(payload["y"]),
            "to_x": int(payload["to_x"]),
            "to_y": int(payload["to_y"]),
            "button": button,
        }
    if action_type == "scroll":
        return {"type": "scroll", "dx": int(payload["dx"]), "dy": int(payload["dy"])}
    if action_type == "key":
        return {
            "type": "key",
            "key": str(payload["key"]),
            "modifiers": _normalize_modifiers(payload.get("modifiers", [])),
        }
    if action_type == "hotkey":
        keys = [str(key) for key in payload["keys"]]
        if not keys:
            raise ValueError("hotkey requires at least one key")
        return _with_optional_target_fields(payload, {"type": "hotkey", "keys": keys})
    if action_type == "type_text":
        return _with_optional_target_fields(payload, {"type": "type_text", "text": str(payload["text"] )})
    if action_type == "clipboard_copy":
        return _with_optional_target_fields(payload, {"type": "hotkey", "keys": ["ctrl", "c"], "clipboard_intent": "copy"})
    if action_type == "clipboard_cut":
        return _with_optional_target_fields(payload, {"type": "hotkey", "keys": ["ctrl", "x"], "clipboard_intent": "cut"})
    if action_type == "clipboard_paste":
        return _with_optional_target_fields(payload, {"type": "hotkey", "keys": ["ctrl", "v"], "clipboard_intent": "paste"})
    if action_type == "clipboard_paste_text":
        return _with_optional_target_fields(payload, {"type": "type_text", "text": str(payload["text"]), "clipboard_intent": "paste_text"})
    if action_type == "select_all":
        return _with_optional_target_fields(payload, {"type": "hotkey", "keys": ["ctrl", "a"], "clipboard_intent": "select_all"})
    return _with_optional_target_fields(payload, {"type": "snapshot_request"})


def normalize_action_json(action_json: str) -> dict[str, Any]:
    try:
        payload = json.loads(action_json)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid action JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError("Action JSON must decode to an object")
    return normalize_action(payload)
