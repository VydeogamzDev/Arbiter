"""Argument validation against a tool's canonical JSON Schema (spec §10.2), stdlib only.

Covers the subset MCP tool schemas use: ``type`` (incl. lists of types), ``properties``,
``required``, ``additionalProperties``, ``enum``, ``const``, ``items``, ``minimum``/``maximum``,
``minLength``/``maxLength``, ``minItems``/``maxItems``, ``anyOf``/``oneOf``. Unknown keywords are
ignored (never a reason to reject). Errors are structured: ``$.path: message``.
"""

from __future__ import annotations

from typing import Any

_TYPES = {"object": dict, "array": list, "string": str, "boolean": bool, "null": type(None)}


def _type_ok(value: Any, t: str) -> bool:
    if t == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if t == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    py = _TYPES.get(t)
    return py is None or isinstance(value, py)


def validate(value: Any, schema: dict[str, Any], path: str = "$") -> list[str]:
    errs: list[str] = []
    if not isinstance(schema, dict) or not schema:
        return errs
    for key in ("anyOf", "oneOf"):
        if key in schema:
            branches = [validate(value, s, path) for s in schema[key] if isinstance(s, dict)]
            ok = sum(1 for b in branches if not b)
            if (key == "anyOf" and ok == 0) or (key == "oneOf" and ok != 1):
                errs.append(f"{path}: doesn't match {'any' if key == 'anyOf' else 'exactly one'} allowed shape")
    t = schema.get("type")
    if t is not None:
        types = t if isinstance(t, list) else [t]
        if not any(_type_ok(value, str(x)) for x in types):
            return errs + [f"{path}: expected {'/'.join(map(str, types))}, got {type(value).__name__}"]
    if "enum" in schema and value not in schema["enum"]:
        errs.append(f"{path}: must be one of {schema['enum']}")
    if "const" in schema and value != schema["const"]:
        errs.append(f"{path}: must be {schema['const']!r}")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            errs.append(f"{path}: must be >= {schema['minimum']}")
        if "maximum" in schema and value > schema["maximum"]:
            errs.append(f"{path}: must be <= {schema['maximum']}")
    if isinstance(value, str):
        if "minLength" in schema and len(value) < schema["minLength"]:
            errs.append(f"{path}: shorter than {schema['minLength']}")
        if "maxLength" in schema and len(value) > schema["maxLength"]:
            errs.append(f"{path}: longer than {schema['maxLength']}")
    if isinstance(value, list):
        if "minItems" in schema and len(value) < schema["minItems"]:
            errs.append(f"{path}: fewer than {schema['minItems']} items")
        if "maxItems" in schema and len(value) > schema["maxItems"]:
            errs.append(f"{path}: more than {schema['maxItems']} items")
        if isinstance(schema.get("items"), dict):
            for i, item in enumerate(value):
                errs += validate(item, schema["items"], f"{path}[{i}]")
    if isinstance(value, dict):
        props = schema.get("properties") or {}
        for req in schema.get("required") or []:
            if req not in value:
                errs.append(f"{path}.{req}: required")
        for k, v in value.items():
            if k in props:
                errs += validate(v, props[k], f"{path}.{k}")
            elif schema.get("additionalProperties") is False:
                errs.append(f"{path}.{k}: not allowed")
            elif isinstance(schema.get("additionalProperties"), dict):
                errs += validate(v, schema["additionalProperties"], f"{path}.{k}")
    return errs
