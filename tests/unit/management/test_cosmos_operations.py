from __future__ import annotations

from uuid import UUID

from backend.aws_api.management.deletion import DeletionOperation
from backend.azure_api.operations.cosmos import CosmosDeletionOperationStore


class FakeCosmosContainer:
    """Query-aware double that keeps same-partition non-operation docs visible."""

    def __init__(self, documents: list[dict[str, object]]) -> None:
        self.documents = documents
        self.queries: list[str] = []

    def query_items(self, *, query: str, parameters, partition_key: str, **_kwargs):
        self.queries.append(query)
        media_id = next(parameter["value"] for parameter in parameters if parameter["name"] == "@media_id")
        operation_only = "STARTSWITH(c.id, 'operation:')" in query
        return [
            document
            for document in self.documents
            if document.get("owner_sub") == partition_key
            and document.get("media_id") == media_id
            and (not operation_only or str(document.get("id", "")).startswith("operation:"))
        ]


def test_get_by_media_id_filters_non_operation_docs_and_restores_sha() -> None:
    owner = "owner"
    media_id = UUID("11111111-1111-4111-8111-111111111111")
    operation_id = UUID("22222222-2222-4222-8222-222222222222")
    checksum = "e" * 64
    container = FakeCosmosContainer(
        [
            {
                "id": str(media_id),
                "kind": "media",
                "owner_sub": owner,
                "media_id": str(media_id),
            },
            {
                "id": "claim:111",
                "kind": "event_claim",
                "owner_sub": owner,
                "media_id": str(media_id),
            },
            {
                "id": "operation:durable-1",
                "owner_sub": owner,
                "operation_id": str(operation_id),
                "storage_uri": "s3://media/originals/one.jpg",
                "media_id": str(media_id),
                "object_keys": ["originals/one.jpg"],
                "sha256": checksum,
                "status": "marked",
                "error": None,
            },
        ]
    )

    operation = CosmosDeletionOperationStore(container).get_by_media_id(owner, media_id)

    assert operation == DeletionOperation(
        operation_id=operation_id,
        owner_sub=owner,
        storage_uri="s3://media/originals/one.jpg",
        media_id=media_id,
        object_keys=["originals/one.jpg"],
        sha256=checksum,
        status="marked",
        error=None,
    )
    assert len(container.queries) == 1
    assert "STARTSWITH(c.id, 'operation:')" in container.queries[0]

