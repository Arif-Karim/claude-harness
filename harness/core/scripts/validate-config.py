#!/usr/bin/env python3
"""Validates harness.config.json against harness.config.schema.json (stdlib only).

Usage: validate-config.py [--schema PATH] [CONFIG]
  CONFIG defaults to harness.config.json; --schema defaults to the schema next to
  this script's parent (harness/core/harness.config.schema.json).

Implements the JSON Schema 2020-12 keywords the harness schema uses: type, enum, const,
required, properties, additionalProperties (bool or schema), items, minItems,
uniqueItems, minLength, minimum, maximum and local $ref (#/$defs/...). An unknown
keyword is an error, so the schema cannot silently outgrow this validator.

Exit 0 valid, 2 invalid or unreadable (errors on stderr, one per line).
"""
import argparse
import json
import os
import sys

KNOWN = {"$schema", "$id", "$defs", "$ref", "title", "description", "type", "enum", "const",
         "required", "properties", "additionalProperties", "items", "minItems", "uniqueItems",
         "minLength", "minimum", "maximum"}
TYPES = {
    "object": lambda v: isinstance(v, dict),
    "array": lambda v: isinstance(v, list),
    "string": lambda v: isinstance(v, str),
    "number": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
    "integer": lambda v: isinstance(v, int) and not isinstance(v, bool),
    "boolean": lambda v: isinstance(v, bool),
    "null": lambda v: v is None,
}


def _resolve(ref, root):
    if not ref.startswith("#/"):
        raise ValueError(f"unsupported $ref {ref!r}")
    node = root
    for part in ref[2:].split("/"):
        node = node[part]
    return node


def _check(value, schema, root, path, errors):
    unknown = set(schema) - KNOWN
    if unknown:
        raise ValueError(f"schema keyword(s) not supported: {', '.join(sorted(unknown))}")
    if "$ref" in schema:
        _check(value, _resolve(schema["$ref"], root), root, path, errors)
    where = path or "(root)"
    if "type" in schema and not TYPES[schema["type"]](value):
        errors.append(f"{where}: expected {schema['type']}, got {type(value).__name__}")
        return
    if "const" in schema and value != schema["const"]:
        errors.append(f"{where}: must be {schema['const']!r}")
    if "enum" in schema and value not in schema["enum"]:
        errors.append(f"{where}: {value!r} is not one of {schema['enum']}")
    if isinstance(value, str) and len(value) < schema.get("minLength", 0):
        errors.append(f"{where}: shorter than {schema['minLength']}")
    if TYPES["number"](value):
        if "minimum" in schema and value < schema["minimum"]:
            errors.append(f"{where}: below minimum {schema['minimum']}")
        if "maximum" in schema and value > schema["maximum"]:
            errors.append(f"{where}: above maximum {schema['maximum']}")
    if isinstance(value, list):
        if len(value) < schema.get("minItems", 0):
            errors.append(f"{where}: needs at least {schema['minItems']} item(s)")
        if schema.get("uniqueItems"):
            seen = [json.dumps(v, sort_keys=True) for v in value]
            if len(seen) != len(set(seen)):
                errors.append(f"{where}: items must be unique")
        if "items" in schema:
            for i, item in enumerate(value):
                _check(item, schema["items"], root, f"{path}[{i}]", errors)
    if isinstance(value, dict):
        for key in schema.get("required", []):
            if key not in value:
                errors.append(f"{where}: missing required property {key!r}")
        props = schema.get("properties", {})
        extra = schema.get("additionalProperties", True)
        for key, item in value.items():
            sub = f"{path}.{key}" if path else key
            if key in props:
                _check(item, props[key], root, sub, errors)
            elif extra is False:
                errors.append(f"{where}: unexpected property {key!r}")
            elif isinstance(extra, dict):
                _check(item, extra, root, sub, errors)


def validate(instance, schema):
    """Returns a list of error strings; empty means valid."""
    errors = []
    _check(instance, schema, schema, "", errors)
    return errors


def validate_file(config_path, schema_path):
    try:
        with open(schema_path, encoding="utf-8") as f:
            schema = json.load(f)
        with open(config_path, encoding="utf-8") as f:
            config = json.load(f)
    except (OSError, ValueError) as e:
        return [f"{config_path}: {e}"]
    return validate(config, schema)


def main(argv):
    default_schema = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                  "harness.config.schema.json")
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--schema", default=default_schema)
    ap.add_argument("config", nargs="?", default="harness.config.json")
    args = ap.parse_args(argv)
    try:
        errors = validate_file(args.config, args.schema)
    except (ValueError, KeyError, TypeError) as e:
        errors = [f"validate-config: {e}"]
    for e in errors:
        print(f"validate-config: {e}", file=sys.stderr)
    return 2 if errors else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
