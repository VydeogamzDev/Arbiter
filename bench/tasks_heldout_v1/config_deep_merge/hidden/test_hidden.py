import copy
import json

from config.loader import load
from config.merge import merge


def test_override_wins():
    assert merge({"a": 1, "b": 2}, {"b": 3}) == {"a": 1, "b": 3}


def test_recursive():
    assert merge({"db": {"host": "x", "port": 1}}, {"db": {"port": 2}}) == {"db": {"host": "x", "port": 2}}


def test_list_replace():
    assert merge({"p": [1, 2]}, {"p": [3]}) == {"p": [3]}


def test_append_plus():
    assert merge({"plugins": ["a"]}, {"plugins+": ["b", "c"]}) == {"plugins": ["a", "b", "c"]}
    assert merge({}, {"plugins+": ["b"]}) == {"plugins": ["b"]}
    assert merge({"x": {"l": [1]}}, {"x": {"l+": [2]}}) == {"x": {"l": [1, 2]}}


def test_none_deletes():
    assert merge({"a": 1, "b": {"c": 1, "d": 2}}, {"a": None, "b": {"d": None}}) == {"b": {"c": 1}}


def test_inputs_unchanged():
    base = {"a": {"b": [1]}, "plugins": ["x"]}
    over = {"a": {"c": 2}, "plugins+": ["y"], "z": None}
    b0, o0 = copy.deepcopy(base), copy.deepcopy(over)
    merge(base, over)
    assert base == b0 and over == o0


def test_no_shared_structure():
    base = {"a": {"b": [1]}, "keep": {"k": [1]}}
    over = {"n": {"m": [2]}}
    out = merge(base, over)
    out["a"]["b"].append(9)
    out["keep"]["k"].append(9)
    out["n"]["m"].append(9)
    assert base == {"a": {"b": [1]}, "keep": {"k": [1]}} and over == {"n": {"m": [2]}}


def test_loader_uses_merge(tmp_path):
    a, b = tmp_path / "a.json", tmp_path / "b.json"
    a.write_text(json.dumps({"db": {"host": "x", "port": 1}, "plugins": ["p"]}))
    b.write_text(json.dumps({"db": {"port": 2}, "plugins+": ["q"]}))
    assert load([a, b]) == {"db": {"host": "x", "port": 2}, "plugins": ["p", "q"]}
