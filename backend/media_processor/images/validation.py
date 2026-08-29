from __future__ import annotations

import json
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker


class JsonSchemaEventValidator:
    def __init__(self, schema: dict[str, object]) -> None:
        Draft202012Validator.check_schema(schema)
        self._validator = Draft202012Validator(
            schema,
            format_checker=FormatChecker(),
        )

    @classmethod
    def from_project_contract(cls) -> "JsonSchemaEventValidator":
        project_root = Path(__file__).resolve().parents[3]
        schema_path = project_root / "contracts" / "schemas" / "media-prepared-event.schema.json"
        return cls(json.loads(schema_path.read_text(encoding="utf-8")))

    def validate(self, event: dict[str, object]) -> None:
        self._validator.validate(event)
