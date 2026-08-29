from __future__ import annotations

import json

from backend.notification_bridge.subscriptions import SnsEmailSubscriptionManager


TOPIC = "arn:aws:sns:ap-southeast-2:123456789012:notifications"
SUBSCRIPTION = f"{TOPIC}:11111111-1111-4111-8111-111111111111"


class SnsClient:
    def __init__(self) -> None:
        self.confirmed = False
        self.attributes: list[dict[str, object]] = []

    def subscribe(self, **kwargs: object) -> dict[str, object]:
        assert kwargs["Endpoint"] == "watcher@example.test"
        assert kwargs["ReturnSubscriptionArn"] is False
        return {"SubscriptionArn": "pending confirmation"}

    def list_subscriptions_by_topic(self, **kwargs: object) -> dict[str, object]:
        return {"Subscriptions": ([{
            "Endpoint": "watcher@example.test",
            "SubscriptionArn": SUBSCRIPTION,
        }] if self.confirmed else [])}

    def get_subscription_attributes(self, **kwargs: object) -> dict[str, object]:
        return {"Attributes": {"SubscriptionArn": SUBSCRIPTION}}

    def set_subscription_attributes(self, **kwargs: object) -> object:
        self.attributes.append(kwargs)
        return {}

    def unsubscribe(self, **kwargs: object) -> object:
        return {}


def test_email_subscription_moves_from_pending_to_active_and_gets_recipient_filter() -> None:
    client = SnsClient()
    manager = SnsEmailSubscriptionManager(client=client, topic_arn=TOPIC)

    arn, status = manager.subscribe("watcher@example.test")
    assert (arn, status) == (None, "pending_confirmation")
    assert manager.status(arn, "watcher@example.test") == ("pending_confirmation", None)

    client.confirmed = True
    assert manager.status(arn, "watcher@example.test") == ("active", SUBSCRIPTION)
    assert json.loads(str(client.attributes[0]["AttributeValue"])) == {
        "recipient": ["watcher@example.test"]
    }
