"""Versioned, packaged JSON contracts for generation and final serialization."""

import json
import math
from decimal import Decimal
from functools import lru_cache
from importlib.resources import files
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker

EXTRACTION_SCHEMA_VERSION = "v2"


@lru_cache
def _schema(name: str, version: str) -> dict:
    schema = json.loads(files(__package__).joinpath(f"{name}-{version}.schema.json").read_text())
    Draft202012Validator.check_schema(schema)
    return schema


def extraction_schema(version: str = EXTRACTION_SCHEMA_VERSION) -> dict:
    # Callers/providers may transform schemas; never expose the cached instance.
    return json.loads(json.dumps(_schema("extraction", version)))


def request_extraction_schema(line_ids: list[str]) -> dict:
    schema = extraction_schema()
    schema["$defs"]["source_line_id"] = {"type": "string", "enum": line_ids}
    for definition in schema["$defs"].values():
        fields = definition.get("properties", {})
        if "source_line_ids" in fields:
            fields["source_line_ids"]["items"] = {"$ref": "#/$defs/source_line_id"}
    return schema


def validate_contract(value: Any, name: str, *, line_ids: list[str] | None = None) -> None:
    def finite(item):
        if isinstance(item, dict):
            for child in item.values():
                finite(child)
        elif isinstance(item, list):
            for child in item:
                finite(child)
        elif isinstance(item, Decimal) and not item.is_finite():
            raise ValueError("non-finite decimal in contract")
        elif isinstance(item, float) and not math.isfinite(item):
            raise ValueError("non-finite float in contract")

    finite(value)
    schema = (
        request_extraction_schema(line_ids)
        if name == "extraction" and line_ids is not None
        else _schema(name, EXTRACTION_SCHEMA_VERSION if name == "extraction" else "v1")
    )
    Draft202012Validator(schema, format_checker=FormatChecker()).validate(value)


def parse_extraction(content: str) -> dict:
    def invalid_constant(value: str):
        raise ValueError(f"non-finite JSON number: {value}")

    value = json.loads(content, parse_float=Decimal, parse_constant=invalid_constant)
    validate_contract(value, "extraction")
    return value
