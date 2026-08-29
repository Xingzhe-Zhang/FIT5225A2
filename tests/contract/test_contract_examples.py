from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml
from jsonschema import Draft202012Validator, ValidationError


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SCHEMA_DIR = PROJECT_ROOT / "contracts" / "schemas"
EXAMPLE_DIR = PROJECT_ROOT / "contracts" / "examples"
OPENAPI_PATH = PROJECT_ROOT / "contracts" / "openapi.yaml"

CONTRACT_NAMES = (
    "error-response",
    "upload-reservation-request",
    "upload-reservation-response",
    "media-prepared-event",
    "media-record",
    "tagging-completed-event",
    "tag-query",
    "species-query",
    "thumbnail-query",
    "query-response",
    "bulk-tag-operation",
    "delete-request",
    "subscription",
)

REQUIRED_ROUTES = {
    "/uploads/reservations": {"post"},
    "/uploads/reservations/{media_id}": {"delete"},
    "/queries/tags": {"post"},
    "/queries/species": {"post"},
    "/queries/thumbnail": {"post"},
    "/queries/by-file": {"post"},
    "/media/tags": {"post"},
    "/media": {"get", "delete"},
    "/subscriptions": {"get", "post"},
    "/subscriptions/{subscription_id}": {"put", "delete"},
}

TASK4_RESPONSE_SCHEMAS = (
    "delete-response",
    "subscription-list-response",
    "subscription-response",
    "subscription-update",
    "tag-update-response",
)


def load_json(path: Path) -> object:
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.mark.parametrize("contract_name", CONTRACT_NAMES)
def test_schema_is_valid_and_examples_enforce_it(contract_name: str) -> None:
    schema_path = SCHEMA_DIR / f"{contract_name}.schema.json"
    valid_path = EXAMPLE_DIR / f"{contract_name}.valid.json"
    invalid_path = EXAMPLE_DIR / f"{contract_name}.invalid.json"

    assert schema_path.exists(), f"missing schema: {schema_path}"
    assert valid_path.exists(), f"missing valid example: {valid_path}"
    assert invalid_path.exists(), f"missing invalid example: {invalid_path}"

    schema = load_json(schema_path)
    Draft202012Validator.check_schema(schema)
    validator = Draft202012Validator(schema)

    validator.validate(load_json(valid_path))
    with pytest.raises(ValidationError):
        validator.validate(load_json(invalid_path))


def test_openapi_declares_all_shared_routes_and_security() -> None:
    assert OPENAPI_PATH.exists(), f"missing OpenAPI document: {OPENAPI_PATH}"
    document = yaml.safe_load(OPENAPI_PATH.read_text(encoding="utf-8"))

    assert document["openapi"].startswith("3.1")
    assert document["components"]["securitySchemes"]["CognitoBearer"]["scheme"] == "bearer"

    for route, methods in REQUIRED_ROUTES.items():
        assert route in document["paths"]
        for method in methods:
            operation = document["paths"][route][method]
            assert operation["security"] == [{"CognitoBearer": []}]
            assert operation["responses"]
            assert "501" not in operation["responses"]

    assert "NotImplemented" not in document["components"]["responses"]


def test_task4_response_schemas_are_valid_and_openapi_matches_router_payloads() -> None:
    document = yaml.safe_load(OPENAPI_PATH.read_text(encoding="utf-8"))

    for name in TASK4_RESPONSE_SCHEMAS:
        schema_path = SCHEMA_DIR / f"{name}.schema.json"
        assert schema_path.exists(), name
        Draft202012Validator.check_schema(load_json(schema_path))

    operations = {
        ("/media/tags", "post"): ("200", "tag-update-response"),
        ("/media", "get"): ("200", "query-response"),
        ("/media", "delete"): ("200", "delete-response"),
        ("/subscriptions", "get"): ("200", "subscription-list-response"),
        ("/subscriptions", "post"): ("201", "subscription-response"),
        ("/subscriptions/{subscription_id}", "put"): ("200", "subscription-response"),
    }
    for (path, method), (status, schema_name) in operations.items():
        responses = document["paths"][path][method]["responses"]
        assert "501" not in responses
        assert responses[status]["content"]["application/json"]["schema"] == {
            "$ref": f"./schemas/{schema_name}.schema.json"
        }

    assert document["paths"]["/subscriptions/{subscription_id}"]["put"]["requestBody"]["content"]["application/json"]["schema"] == {
        "$ref": "./schemas/subscription-update.schema.json"
    }
    assert document["paths"]["/subscriptions/{subscription_id}"]["put"]["responses"]["404"] == {
        "$ref": "#/components/responses/NotFound"
    }
    assert document["paths"]["/subscriptions/{subscription_id}"]["put"]["responses"]["409"] == {
        "$ref": "#/components/responses/Conflict"
    }
    assert document["paths"]["/subscriptions/{subscription_id}"]["delete"]["responses"]["404"] == {
        "$ref": "#/components/responses/NotFound"
    }
    for path, method in (("/media/tags", "post"), ("/media", "delete"), ("/subscriptions", "post"), ("/subscriptions/{subscription_id}", "put")):
        assert "422" in document["paths"][path][method]["responses"]
