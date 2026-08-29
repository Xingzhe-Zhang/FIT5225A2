from __future__ import annotations

import hashlib
import json
from typing import Protocol

from fastapi import APIRouter, Depends, Request
from pydantic import StringConstraints
from typing_extensions import Annotated

from backend.common.auth.dependencies import require_auth
from backend.common.auth.models import AuthContext
from backend.common.contracts.models import StrictModel
from backend.common.errors.models import ApiError

Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]


class UserProfile(StrictModel):
    given_name: str | None = None
    family_name: str | None = None
    complete: bool


class ProfileUpdate(StrictModel):
    given_name: Name
    family_name: Name


class ProfileClient(Protocol):
    def get_profile(self, access_token: str, subject: str) -> UserProfile: ...

    def update_profile(self, access_token: str, subject: str, profile: ProfileUpdate) -> UserProfile: ...


def _complete(given_name: str | None, family_name: str | None) -> bool:
    return bool(given_name and given_name.strip() and family_name and family_name.strip())


def create_profile_router(client: ProfileClient) -> APIRouter:
    router = APIRouter(tags=["profile"])

    @router.get("/profile", response_model=UserProfile)
    def get_profile(request: Request, auth: AuthContext = Depends(require_auth)) -> UserProfile:
        return _call(lambda: client.get_profile(_access_token(request), auth.sub))

    @router.put("/profile", response_model=UserProfile)
    def update_profile(
        request: Request,
        payload: ProfileUpdate,
        auth: AuthContext = Depends(require_auth),
    ) -> UserProfile:
        return _call(lambda: client.update_profile(_access_token(request), auth.sub, payload))

    return router


def _access_token(request: Request) -> str:
    token = getattr(request.state, "access_token", "")
    if not token:
        raise ApiError("AUTH_TOKEN_MISSING", "Bearer access token is required", 401)
    return token


def _call(operation):
    try:
        return operation()
    except ApiError:
        raise
    except Exception as error:
        raise ApiError("PROFILE_UNAVAILABLE", "User profile could not be loaded", 502) from error


class CognitoProfileClient:
    def __init__(self, client) -> None:
        self._client = client

    def get_profile(self, access_token: str, subject: str) -> UserProfile:
        del subject
        response = self._client.get_user(AccessToken=access_token)
        attributes = _attributes(response.get("UserAttributes", []))
        given_name = attributes.get("given_name")
        family_name = attributes.get("family_name")
        return UserProfile(
            given_name=given_name,
            family_name=family_name,
            complete=_complete(given_name, family_name),
        )

    def update_profile(self, access_token: str, subject: str, profile: ProfileUpdate) -> UserProfile:
        self._client.update_user_attributes(
            AccessToken=access_token,
            UserAttributes=[
                {"Name": "given_name", "Value": profile.given_name},
                {"Name": "family_name", "Value": profile.family_name},
            ],
        )
        return self.get_profile(access_token, subject)


class S3ProfileClient:
    """Persist application profile fields without changing an existing pool schema."""

    def __init__(self, client, bucket_name: str) -> None:
        self._client = client
        self._bucket_name = bucket_name

    def get_profile(self, access_token: str, subject: str) -> UserProfile:
        del access_token
        try:
            response = self._client.get_object(Bucket=self._bucket_name, Key=self._key(subject))
            raw = response["Body"].read()
            payload = json.loads(raw.decode("utf-8"))
            if not isinstance(payload, dict):
                return UserProfile(complete=False)
            given_name = payload.get("given_name") if isinstance(payload.get("given_name"), str) else None
            family_name = payload.get("family_name") if isinstance(payload.get("family_name"), str) else None
            return UserProfile(
                given_name=given_name,
                family_name=family_name,
                complete=_complete(given_name, family_name),
            )
        except Exception as error:
            error_code = getattr(error, "response", {}).get("Error", {}).get("Code")
            if error_code in {"NoSuchKey", "404"}:
                return UserProfile(complete=False)
            raise

    def update_profile(self, access_token: str, subject: str, profile: ProfileUpdate) -> UserProfile:
        del access_token
        self._client.put_object(
            Bucket=self._bucket_name,
            Key=self._key(subject),
            Body=json.dumps(
                {"given_name": profile.given_name, "family_name": profile.family_name},
                separators=(",", ":"),
            ).encode("utf-8"),
            ContentType="application/json",
            ServerSideEncryption="AES256",
        )
        return UserProfile(given_name=profile.given_name, family_name=profile.family_name, complete=True)

    @staticmethod
    def _key(subject: str) -> str:
        return f"profiles/{hashlib.sha256(subject.encode('utf-8')).hexdigest()}.json"


class InMemoryProfileClient:
    """Small local/test adapter keyed by the verified Cognito subject."""

    def __init__(self) -> None:
        self._profiles: dict[str, UserProfile] = {}

    def get_profile(self, access_token: str, subject: str) -> UserProfile:
        del access_token
        return self._profiles.get(subject, UserProfile(complete=False))

    def update_profile(self, access_token: str, subject: str, profile: ProfileUpdate) -> UserProfile:
        del access_token
        value = UserProfile(
            given_name=profile.given_name,
            family_name=profile.family_name,
            complete=True,
        )
        self._profiles[subject] = value
        return value


def _attributes(raw_attributes: object) -> dict[str, str]:
    if not isinstance(raw_attributes, list):
        return {}
    values: dict[str, str] = {}
    for attribute in raw_attributes:
        if isinstance(attribute, dict):
            name = attribute.get("Name")
            value = attribute.get("Value")
            if isinstance(name, str) and isinstance(value, str):
                values[name] = value
    return values
