from __future__ import annotations

import importlib
import json
import logging


def logging_module():
    return importlib.import_module("backend.common.observability.logging")


def test_recursive_redaction_preserves_safe_fields() -> None:
    module = logging_module()
    payload = {
        "request_id": "req-1",
        "Authorization": "Bearer secret-token",
        "nested": {"password": "secret", "count": 2},
        "items": [{"refresh_token": "secret"}, {"species": "dingo"}],
    }

    redacted = module.redact_sensitive(payload)

    assert redacted["request_id"] == "req-1"
    assert redacted["Authorization"] == "[REDACTED]"
    assert redacted["nested"] == {"password": "[REDACTED]", "count": 2}
    assert redacted["items"][0]["refresh_token"] == "[REDACTED]"
    assert redacted["items"][1]["species"] == "dingo"


def test_json_formatter_redacts_structured_extra() -> None:
    module = logging_module()
    record = logging.LogRecord(
        name="test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="request",
        args=(),
        exc_info=None,
    )
    record.request_id = "req-1"
    record.context = {"access_token": "secret", "species": "dingo"}

    rendered = json.loads(module.RedactingJsonFormatter().format(record))

    assert rendered["request_id"] == "req-1"
    assert rendered["context"]["access_token"] == "[REDACTED]"
    assert rendered["context"]["species"] == "dingo"
