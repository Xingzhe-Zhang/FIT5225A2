from __future__ import annotations

import httpx
from pydantic import TypeAdapter, ValidationError

from backend.azure_api.queries.service import MediaNotFoundError
from backend.common.contracts.models import MediaRecord
from backend.common.errors.models import ApiError


_RECORDS = TypeAdapter(list[MediaRecord])


class AzureDataApiClient:
    """Authenticated HTTP client for the Azure owner-scoped query API."""

    def __init__(
        self,
        base_url: str,
        *,
        client: httpx.Client | None = None,
        timeout_seconds: float = 10.0,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        if not self._base_url.startswith("https://"):
            raise ValueError("Azure data API base URL must use HTTPS")
        self._client = client or httpx.Client(timeout=timeout_seconds)

    def query_tags(self, access_token: str, payload: object) -> list[MediaRecord]:
        return self._records("/internal/query/tags", access_token, payload)

    def query_species(self, access_token: str, payload: object) -> list[MediaRecord]:
        return self._records("/internal/query/species", access_token, payload)

    def query_thumbnail(self, access_token: str, payload: object) -> MediaRecord:
        response = self._request("/internal/query/thumbnail", access_token, payload)
        try:
            return MediaRecord.model_validate(response.json())
        except (ValueError, ValidationError) as error:
            raise ApiError("AZURE_DATA_INVALID", "Azure data API returned an invalid response", 502) from error

    def _records(self, path: str, access_token: str, payload: object) -> list[MediaRecord]:
        response = self._request(path, access_token, payload)
        try:
            return _RECORDS.validate_python(response.json())
        except (ValueError, ValidationError) as error:
            raise ApiError("AZURE_DATA_INVALID", "Azure data API returned an invalid response", 502) from error

    def _request(self, path: str, access_token: str, payload: object) -> httpx.Response:
        try:
            response = self._client.post(
                f"{self._base_url}{path}",
                headers={"Authorization": f"Bearer {access_token}"},
                json=payload,
            )
        except httpx.HTTPError as error:
            raise ApiError("AZURE_DATA_UNAVAILABLE", "Azure data API is unavailable", 502) from error
        if response.status_code == 404 and path.endswith("/thumbnail"):
            raise MediaNotFoundError("thumbnail was not found for the authenticated owner")
        if response.status_code in {401, 403}:
            raise ApiError("AUTH_TOKEN_INVALID", "Azure data API rejected the access token", 401)
        if not response.is_success:
            raise ApiError("AZURE_DATA_UNAVAILABLE", "Azure data API request failed", 502)
        return response
