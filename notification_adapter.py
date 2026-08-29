"""AWS Lambda adapter for EventBridge tagging-completion notifications."""

from __future__ import annotations

import os
from functools import lru_cache

import boto3
from azure.cosmos import CosmosClient

from backend.azure_api.operations.cosmos import CosmosDeliveryLedger
from backend.azure_api.subscriptions.cosmos_repository import CosmosSubscriptionRepository
from backend.common.azure_cosmos_credential import load_cosmos_credential
from backend.common.contracts.models import TaggingCompletedEvent
from backend.notification_bridge.evaluator import NotificationEvaluator
from backend.notification_bridge.sns import SnsNotifier
from backend.notification_bridge.subscriptions import SnsEmailSubscriptionManager


@lru_cache(maxsize=1)
def _cosmos_credential() -> object:
    secret_arn = os.environ.get("AZURE_COSMOS_SECRET_ARN") or os.environ["AZURE_WORKER_SECRET_ARN"]
    return load_cosmos_credential(
        boto3.client("secretsmanager", region_name=os.environ.get("AWS_REGION")),
        secret_arn,
    )


@lru_cache(maxsize=1)
def _evaluator() -> NotificationEvaluator:
    region = os.environ.get("AWS_REGION", "ap-southeast-2")
    cosmos = CosmosClient(os.environ["COSMOS_ENDPOINT"], credential=_cosmos_credential())
    database = cosmos.get_database_client(os.environ.get("COSMOS_DATABASE", "bioarchive"))
    subscriptions = CosmosSubscriptionRepository(
        database.get_container_client(os.environ.get("COSMOS_SUBSCRIPTIONS_CONTAINER", "subscriptions"))
    )
    ledger = CosmosDeliveryLedger(
        database.get_container_client(os.environ.get("COSMOS_DELIVERY_LEDGER_CONTAINER", "delivery-ledger"))
    )
    sns = boto3.client("sns", region_name=region)
    topic = os.environ["NOTIFICATION_TOPIC"]
    manager = SnsEmailSubscriptionManager(client=sns, topic_arn=topic)
    app_base_url = os.environ.get("FRONTEND_BASE_URL") or os.environ.get("API_BASE_URL")
    if not app_base_url:
        raise RuntimeError("FRONTEND_BASE_URL or API_BASE_URL must be configured")
    return NotificationEvaluator(
        repository=subscriptions,
        notifier=SnsNotifier(client=sns, topic_arn=topic),
        ledger=ledger,
        app_base_url=app_base_url,
        subscription_is_active=manager.status_check,
    )


def handler(event: dict[str, object], context: object) -> dict[str, object]:
    del context
    detail = event.get("detail", event)
    if not isinstance(detail, dict):
        raise ValueError("EventBridge detail must be an object")
    completed = TaggingCompletedEvent.model_validate(detail)
    outcomes = _evaluator().evaluate(completed)
    failed = sum(outcome.status == "failed" for outcome in outcomes)
    if failed:
        raise RuntimeError(f"{failed} notification deliveries failed")
    return {
        "status": "ok",
        "sent": sum(outcome.status == "sent" for outcome in outcomes),
        "failed": 0,
    }
