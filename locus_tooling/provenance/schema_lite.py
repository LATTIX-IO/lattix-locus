"""A small, strict JSON Schema validator for the provenance records (D-29).

Locus validates attestations at runtime (the model gate) and in CI (the dependency
gate) without adding a dependency, so this module implements exactly the subset of
JSON Schema 2020-12 that ``provenance/attestation.schema.json`` uses:

``type`` (one or a list), ``enum``, ``const``, ``required``, ``properties``,
``additionalProperties`` (boolean or schema), ``items``, ``minItems``,
``minLength``, ``pattern``, ``minimum``, ``format: date`` and local ``$ref``
(``#/$defs/<name>``). Any other keyword in the schema is rejected, so a schema
edit that relies on an unsupported keyword fails loudly instead of being ignored.
The unit tests cross-check the schema with the reference ``jsonschema`` package
when it is installed. Pure.
"""

from __future__ import annotations

import datetime as _dt
import re
from collections.abc import Mapping
from typing import Any

SUPPORTED_KEYWORDS = frozenset(
    {
        "$schema",
        "$id",
        "$defs",
        "$ref",
        "title",
        "description",
        "type",
        "enum",
        "const",
        "required",
        "properties",
        "additionalProperties",
        "items",
        "minItems",
        "minLength",
        "pattern",
        "minimum",
        "format",
    }
)

_TYPES: dict[str, tuple[type, ...]] = {
    "object": (dict,),
    "array": (list,),
    "string": (str,),
    "integer": (int,),
    "number": (int, float),
    "boolean": (bool,),
    "null": (type(None),),
}


class SchemaError(ValueError):
    """The schema itself uses something this validator does not implement."""


def _is_type(value: Any, name: str) -> bool:
    if name in {"integer", "number"} and isinstance(value, bool):
        return False
    return isinstance(value, _TYPES[name])


def _valid_date(value: str) -> bool:
    try:
        _dt.date.fromisoformat(value)
    except ValueError:
        return False
    return bool(re.fullmatch(r"\d{4}-\d{2}-\d{2}", value))


def check_schema(schema: Any, *, path: str = "#") -> None:
    """Raise :class:`SchemaError` when ``schema`` uses an unsupported keyword."""
    if not isinstance(schema, Mapping):
        raise SchemaError(f"{path}: a schema must be an object")
    unknown = set(schema) - SUPPORTED_KEYWORDS
    if unknown:
        raise SchemaError(f"{path}: unsupported keywords {sorted(unknown)}")
    for name in schema.get("type", []) if isinstance(schema.get("type"), list) else []:
        if name not in _TYPES:
            raise SchemaError(f"{path}: unknown type {name!r}")
    if isinstance(schema.get("type"), str) and schema["type"] not in _TYPES:
        raise SchemaError(f"{path}: unknown type {schema['type']!r}")
    if "format" in schema and schema["format"] != "date":
        raise SchemaError(f"{path}: unsupported format {schema['format']!r}")
    for key in ("$defs", "properties"):
        for name, sub in dict(schema.get(key) or {}).items():
            check_schema(sub, path=f"{path}/{key}/{name}")
    for key in ("items",):
        if key in schema:
            check_schema(schema[key], path=f"{path}/{key}")
    extra = schema.get("additionalProperties")
    if isinstance(extra, Mapping):
        check_schema(extra, path=f"{path}/additionalProperties")


def validate(instance: Any, schema: Mapping[str, Any]) -> list[str]:
    """Every violation of ``schema`` in ``instance`` as ``"<json path>: <message>"``."""
    check_schema(schema)
    errors: list[str] = []
    _validate(instance, schema, schema, "$", errors)
    return errors


def _resolve(ref: str, root: Mapping[str, Any]) -> Mapping[str, Any]:
    prefix = "#/$defs/"
    if not ref.startswith(prefix):
        raise SchemaError(f"only local $defs references are supported: {ref!r}")
    defs = root.get("$defs") or {}
    name = ref[len(prefix) :]
    if name not in defs:
        raise SchemaError(f"unresolved reference {ref!r}")
    target = defs[name]
    if not isinstance(target, Mapping):
        raise SchemaError(f"reference {ref!r} is not a schema")
    return target


def _validate(
    value: Any,
    schema: Mapping[str, Any],
    root: Mapping[str, Any],
    path: str,
    errors: list[str],
) -> None:
    if "$ref" in schema:
        _validate(value, _resolve(str(schema["$ref"]), root), root, path, errors)
        return
    wanted = schema.get("type")
    if wanted is not None:
        names = wanted if isinstance(wanted, list) else [wanted]
        if not any(_is_type(value, str(name)) for name in names):
            errors.append(f"{path}: expected {' or '.join(map(str, names))}")
            return
    if "const" in schema and value != schema["const"]:
        errors.append(f"{path}: must be {schema['const']!r}")
    if "enum" in schema and value not in schema["enum"]:
        errors.append(f"{path}: must be one of {schema['enum']!r}")
    if isinstance(value, str):
        if len(value) < int(schema.get("minLength", 0)):
            errors.append(f"{path}: shorter than {schema['minLength']}")
        pattern = schema.get("pattern")
        if pattern is not None and not re.search(str(pattern), value):
            errors.append(f"{path}: does not match {pattern!r}")
        if schema.get("format") == "date" and not _valid_date(value):
            errors.append(f"{path}: not an ISO date (YYYY-MM-DD)")
    if isinstance(value, (int, float)) and not isinstance(value, bool) and "minimum" in schema:
        if value < schema["minimum"]:
            errors.append(f"{path}: below minimum {schema['minimum']}")
    if isinstance(value, list):
        if len(value) < int(schema.get("minItems", 0)):
            errors.append(f"{path}: fewer than {schema['minItems']} items")
        items = schema.get("items")
        if isinstance(items, Mapping):
            for index, item in enumerate(value):
                _validate(item, items, root, f"{path}[{index}]", errors)
    if isinstance(value, dict):
        for name in schema.get("required", []):
            if name not in value:
                errors.append(f"{path}: missing required property {name!r}")
        properties: Mapping[str, Any] = schema.get("properties") or {}
        extra = schema.get("additionalProperties", True)
        for key, item in value.items():
            if key in properties:
                _validate(item, properties[key], root, f"{path}.{key}", errors)
            elif extra is False:
                errors.append(f"{path}: unexpected property {key!r}")
            elif isinstance(extra, Mapping):
                _validate(item, extra, root, f"{path}.{key}", errors)
