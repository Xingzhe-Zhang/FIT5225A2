from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from backend.common.contracts.models import ErrorDetail, ErrorResponse


@dataclass(slots=True)
class ApiError(Exception):
    code: str
    message: str
    status_code: int

    def to_response(self, request_id: UUID) -> ErrorResponse:
        return ErrorResponse(
            error=ErrorDetail(
                code=self.code,
                message=self.message,
                request_id=request_id,
            )
        )
