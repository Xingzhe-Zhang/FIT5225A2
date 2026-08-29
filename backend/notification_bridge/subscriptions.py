"""SNS email subscription lifecycle and per-recipient filtering."""

from __future__ import annotations

import json
from typing import Protocol


class SnsSubscriptionClient(Protocol):
    def subscribe(self, **kwargs: object) -> dict[str, object]: ...
    def unsubscribe(self, **kwargs: object) -> object: ...
    def get_subscription_attributes(self, **kwargs: object) -> dict[str, object]: ...
    def set_subscription_attributes(self, **kwargs: object) -> object: ...
    def list_subscriptions_by_topic(self, **kwargs: object) -> dict[str, object]: ...


class SnsEmailSubscriptionManager:
    """Maintains one SNS email subscription per application subscription.

    SNS topics broadcast by default. A recipient filter policy is attached to
    each subscription so the evaluator can publish with a recipient attribute
    without leaking notifications to other addresses.
    """

    def __init__(self, *, client: SnsSubscriptionClient, topic_arn: str) -> None:
        if not topic_arn.startswith("arn:aws:sns:"):
            raise ValueError("topic_arn must identify an SNS topic")
        self._client = client
        self._topic_arn = topic_arn

    def subscribe(self, email: str) -> tuple[str | None, str]:
        response = self._client.subscribe(
            TopicArn=self._topic_arn,
            Protocol="email",
            Endpoint=email,
            # Do not expose an unconfirmed ARN: subscription attributes can
            # only be managed after the recipient confirms the email.
            ReturnSubscriptionArn=False,
        )
        arn = response.get("SubscriptionArn")
        subscription_arn = str(arn) if arn and arn != "pending confirmation" else None
        if subscription_arn:
            self._ensure_filter(subscription_arn, email)
        return subscription_arn, "pending_confirmation"

    def unsubscribe(self, subscription_arn: str | None) -> None:
        if subscription_arn and subscription_arn != "PendingConfirmation":
            self._client.unsubscribe(SubscriptionArn=subscription_arn)

    def status(self, subscription_arn: str | None, email: str) -> tuple[str, str | None]:
        try:
            if not subscription_arn:
                subscription_arn = self._find_subscription_arn(email)
                if not subscription_arn:
                    return "pending_confirmation", None
            response = self._client.get_subscription_attributes(
                SubscriptionArn=subscription_arn,
            )
            attributes = response.get("Attributes", {})
            if not isinstance(attributes, dict):
                return "pending_confirmation", subscription_arn
            effective_arn = str(attributes.get("SubscriptionArn") or subscription_arn)
            if effective_arn == "PendingConfirmation":
                return "pending_confirmation", subscription_arn
            self._ensure_filter(subscription_arn, email, attributes)
            return "active", subscription_arn
        except Exception:
            # A just-created email subscription can remain pending and may not
            # yet be queryable. Keep it visible without blocking the API.
            return "pending_confirmation", subscription_arn

    def _find_subscription_arn(self, email: str) -> str | None:
        token: str | None = None
        while True:
            arguments: dict[str, object] = {"TopicArn": self._topic_arn}
            if token:
                arguments["NextToken"] = token
            response = self._client.list_subscriptions_by_topic(**arguments)
            items = response.get("Subscriptions", [])
            if isinstance(items, list):
                for item in items:
                    if not isinstance(item, dict) or item.get("Endpoint") != email:
                        continue
                    arn = str(item.get("SubscriptionArn") or "")
                    if arn and arn != "PendingConfirmation":
                        return arn
            next_token = response.get("NextToken")
            token = str(next_token) if next_token else None
            if not token:
                return None

    def status_check(self, subscription) -> bool:
        status, _ = self.status(subscription.sns_subscription_arn, subscription.email)
        return status == "active"

    def _ensure_filter(
        self,
        subscription_arn: str,
        email: str,
        attributes: dict[str, object] | None = None,
    ) -> None:
        current = ""
        if attributes is not None:
            current = str(attributes.get("FilterPolicy", ""))
        expected = json.dumps({"recipient": [email]}, separators=(",", ":"))
        if current == expected:
            return
        self._client.set_subscription_attributes(
            SubscriptionArn=subscription_arn,
            AttributeName="FilterPolicy",
            AttributeValue=expected,
        )
