from __future__ import annotations

from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Response
from pydantic import Field

from backend.aws_api.dependencies import FeatureDependencies
from backend.azure_api.subscriptions.repository import Subscription
from backend.azure_api.subscriptions.service import SubscriptionConflict, SubscriptionNotFound
from backend.common.auth.dependencies import require_auth
from backend.common.auth.models import AuthContext
from backend.common.contracts.models import NotificationSubscription, StrictModel
from backend.common.errors.models import ApiError


class SubscriptionUpdateRequest(NotificationSubscription):
    expected_version: Annotated[int, Field(ge=1)]


class SubscriptionResponse(StrictModel):
    subscription_id: UUID
    email: str
    tags: list[str]
    status: str
    version: int
    created_at: datetime
    updated_at: datetime


def create_subscription_router(dependencies: FeatureDependencies) -> APIRouter:
    router = APIRouter(tags=["notifications"])

    @router.get("/subscriptions")
    def list_subscriptions(
        auth: AuthContext = Depends(require_auth),
    ) -> dict[str, list[SubscriptionResponse]]:
        return {"results": [_response(subscription) for subscription in dependencies.subscriptions.list(owner_sub=auth.sub)]}

    @router.post("/subscriptions", status_code=201, response_model=SubscriptionResponse)
    def create_subscription(
        request: NotificationSubscription,
        auth: AuthContext = Depends(require_auth),
    ) -> SubscriptionResponse:
        try:
            subscription = dependencies.subscriptions.create(
                owner_sub=auth.sub,
                email=request.email,
                tags=request.tags,
            )
        except ValueError as error:
            raise ApiError("SUBSCRIPTION_REQUEST_INVALID", str(error), 422) from error
        return _response(subscription)

    @router.put("/subscriptions/{subscription_id}", response_model=SubscriptionResponse)
    def update_subscription(
        subscription_id: UUID,
        request: SubscriptionUpdateRequest,
        auth: AuthContext = Depends(require_auth),
    ) -> SubscriptionResponse:
        try:
            subscription = dependencies.subscriptions.update(
                owner_sub=auth.sub,
                subscription_id=subscription_id,
                email=request.email,
                tags=request.tags,
                expected_version=request.expected_version,
            )
        except SubscriptionNotFound as error:
            raise ApiError("SUBSCRIPTION_NOT_FOUND", str(error), 404) from error
        except SubscriptionConflict as error:
            raise ApiError("SUBSCRIPTION_VERSION_CONFLICT", str(error), 409) from error
        except ValueError as error:
            raise ApiError("SUBSCRIPTION_REQUEST_INVALID", str(error), 422) from error
        return _response(subscription)

    @router.delete("/subscriptions/{subscription_id}", status_code=204)
    def delete_subscription(
        subscription_id: UUID,
        auth: AuthContext = Depends(require_auth),
    ) -> Response:
        if not dependencies.subscriptions.delete(owner_sub=auth.sub, subscription_id=subscription_id):
            raise ApiError("SUBSCRIPTION_NOT_FOUND", "subscription was not found", 404)
        return Response(status_code=204)

    return router


def _response(subscription: Subscription) -> SubscriptionResponse:
    return SubscriptionResponse(
        subscription_id=subscription.subscription_id,
        email=subscription.email,
        tags=list(subscription.tags),
        status=subscription.status,
        version=subscription.version,
        created_at=subscription.created_at,
        updated_at=subscription.updated_at,
    )
