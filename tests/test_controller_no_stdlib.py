"""FL 21 has no Python stdlib: the controller must load and talk without base64/json."""
from __future__ import annotations

import base64 as real_base64
import importlib.util
import json as real_json
import random
import sys
import types
from pathlib import Path

import pytest

_CONTROLLER_PATH = (Path(__file__).resolve().parents[1] / "fl_controller"
                    / "FLStudioMCP" / "device_FLStudioMCP.py")
_FL_MODULES = ("channels", "device", "general", "midi", "mixer", "patterns",
               "playlist", "plugins", "transport", "ui", "arrangement", "utils")


@pytest.fixture(scope="module")
def controller():
    names = _FL_MODULES + ("base64", "json")
    saved = {n: sys.modules.get(n) for n in names}
    for name in _FL_MODULES:
        sys.modules[name] = types.ModuleType(name)
    sys.modules["base64"] = None   # None makes `import` raise ImportError
    sys.modules["json"] = None
    try:
        spec = importlib.util.spec_from_file_location("controller_no_stdlib", _CONTROLLER_PATH)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
    finally:
        for name, prev in saved.items():
            if prev is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = prev
    return mod


def test_uses_stand_ins(controller):
    assert controller.base64 is controller._MiniBase64
    assert controller.json is controller._MiniJson


def test_base64_matches_stdlib(controller):
    rng = random.Random(21)
    for size in list(range(0, 70)) + [1000, 1001, 1002]:
        data = bytes(rng.randrange(256) for _ in range(size))
        encoded = controller.base64.b64encode(data)
        assert encoded == real_base64.b64encode(data)
        assert controller.base64.b64decode(encoded) == data


SAMPLES = [
    None, True, False, 0, -7, 1.5, -0.25, 1e-07, 120000, "",
    "Bębny \"główne\" \\ \n\t\r\b\f \x01 \x7f",
    "😀 emoji poza BMP",
    [], {}, [1, [2, [3]]], {"a": {"b": [None, True, 1.25, "ż"]}},
    {"v": 2, "ok": True, "data": {"tracks": [{"i": 0, "name": "Master", "pan": 0.0}]}},
]


@pytest.mark.parametrize("value", SAMPLES)
def test_dumps_matches_stdlib(controller, value):
    expected = real_json.dumps(value, ensure_ascii=True, separators=(",", ":"), allow_nan=False)
    assert controller.json.dumps(value, ensure_ascii=True, separators=(",", ":"),
                                 allow_nan=False) == expected


@pytest.mark.parametrize("value", SAMPLES)
def test_loads_reads_stdlib_output(controller, value):
    assert controller.json.loads(real_json.dumps(value)) == value
    assert controller.json.loads(real_json.dumps(value, ensure_ascii=False, indent=2)) == value


def test_tuple_and_int_key_match_stdlib(controller):
    value = {1: (1, 2)}
    assert controller.json.dumps(value) == real_json.dumps(value, separators=(",", ":"))


@pytest.mark.parametrize("bad", ['{"a":1', "[1,2", "tru", '{"a" 1}', "[1 2]", "1 2", ""])
def test_loads_rejects_malformed(controller, bad):
    with pytest.raises((ValueError, IndexError)):
        controller.json.loads(bad)


def test_dumps_rejects_nan_and_objects(controller):
    with pytest.raises(ValueError):
        controller.json.dumps(float("nan"))
    with pytest.raises(TypeError):
        controller.json.dumps(object())


def test_message_round_trip(controller):
    payload = {"v": 2, "ok": True, "data": {"name": "Wokal główny"}}
    raw = controller._encode_message(controller.DIR_REQUEST, "abcd1234", payload)
    assert controller._decode_message(raw) == (controller.DIR_REQUEST, "abcd1234", payload)


def _pattern_stubs(controller, monkeypatch, find):
    state = {"selected": 3, "names": {}}
    p = controller.patterns
    monkeypatch.setattr(controller.midi, "FFNEP_DontPromptName", 2, raising=False)
    monkeypatch.setattr(p, "findFirstNextEmptyPat", find(state), raising=False)
    monkeypatch.setattr(p, "patternNumber", lambda: state["selected"], raising=False)
    monkeypatch.setattr(p, "patternCount", lambda: 2, raising=False)
    monkeypatch.setattr(p, "jumpToPattern", lambda i: state.update(selected=i), raising=False)
    monkeypatch.setattr(p, "setPatternName", lambda i, n: state["names"].update({i: n}), raising=False)
    monkeypatch.setattr(p, "getPatternName", lambda i: state["names"][i], raising=False)
    return state


def test_new_pattern_uses_pattern_fl_selected(controller, monkeypatch):
    def find(state):
        def fn(flags):               # FL 21: flags required, returns nothing
            assert flags == 2
            state["selected"] = 7
        return fn
    _pattern_stubs(controller, monkeypatch, find)
    assert controller._h_arrange_new_pattern({"name": "Verse"})["index"] == 7


def test_new_pattern_falls_back_when_call_fails(controller, monkeypatch):
    def find(state):
        def fn(flags):
            raise TypeError("wrong parameters")
        return fn
    _pattern_stubs(controller, monkeypatch, find)
    assert controller._h_arrange_new_pattern({"name": "Verse"})["index"] == 3
