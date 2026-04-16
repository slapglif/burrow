import pytest

from burrow.computer_use import normalize_action, normalize_action_json


def test_normalize_click_action():
    action = normalize_action({"type": "click", "x": 10, "y": 20})

    assert action == {"type": "click", "x": 10, "y": 20, "button": "left", "count": 1}


def test_normalize_double_click_action():
    action = normalize_action({"type": "double_click", "x": 10, "y": 20, "button": "right"})

    assert action == {"type": "click", "x": 10, "y": 20, "button": "right", "count": 2}


def test_normalize_drag_action_adds_default_button():
    action = normalize_action({"type": "drag", "x": 1, "y": 2, "to_x": 3, "to_y": 4})

    assert action == {"type": "drag", "x": 1, "y": 2, "to_x": 3, "to_y": 4, "button": "left"}


def test_normalize_hotkey_action():
    action = normalize_action({"type": "hotkey", "keys": ["ctrl", "shift", "p"]})

    assert action == {"type": "hotkey", "keys": ["ctrl", "shift", "p"]}


def test_normalize_key_action_stringifies_modifiers():
    action = normalize_action({"type": "key", "key": "enter", "modifiers": ["ctrl", 1]})

    assert action == {"type": "key", "key": "enter", "modifiers": ["ctrl", "1"]}


def test_normalize_type_text_action():
    action = normalize_action({"type": "type_text", "text": "hello"})

    assert action == {"type": "type_text", "text": "hello"}


def test_normalize_clipboard_paste_action_to_hotkey():
    action = normalize_action({"type": "clipboard_paste"})

    assert action == {"type": "hotkey", "keys": ["ctrl", "v"], "clipboard_intent": "paste"}


def test_normalize_clipboard_paste_text_action_to_type_text():
    action = normalize_action({"type": "clipboard_paste_text", "text": "hello", "display": ":1"})

    assert action == {"type": "type_text", "text": "hello", "clipboard_intent": "paste_text", "display": ":1"}


def test_normalize_select_all_action_to_hotkey():
    action = normalize_action({"type": "select_all"})

    assert action == {"type": "hotkey", "keys": ["ctrl", "a"], "clipboard_intent": "select_all"}


def test_normalize_snapshot_request_action():
    action = normalize_action({"type": "snapshot_request"})

    assert action == {"type": "snapshot_request"}


def test_normalize_action_json():
    action = normalize_action_json('{"type":"click","x":3,"y":4}')

    assert action == {"type": "click", "x": 3, "y": 4, "button": "left", "count": 1}


def test_invalid_action_raises():
    with pytest.raises(ValueError):
        normalize_action({"type": "click", "x": 10})


def test_invalid_button_raises():
    with pytest.raises(ValueError, match="Unsupported mouse button"):
        normalize_action({"type": "click", "x": 10, "y": 20, "button": "side"})


def test_invalid_action_json_raises():
    with pytest.raises(ValueError, match="Invalid action JSON"):
        normalize_action_json("not-json")
