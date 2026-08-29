from __future__ import annotations

import pytest

from backend.notification_bridge import sns


class RecordingSnsClient:
    def __init__(self) -> None:
        self.requests: list[dict[str, object]] = []

    def publish(self, **kwargs):
        self.requests.append(kwargs)
        return {"MessageId": "message-1"}


def test_sns_notifier_publishes_only_to_configured_topic_with_recipient_filter() -> None:
    notifier_type = getattr(sns, "SnsNotifier", None)
    assert notifier_type is not None, "SnsNotifier has not been implemented"
    client = RecordingSnsClient()
    notifier = notifier_type(
        client=client,
        topic_arn="arn:aws:sns:ap-southeast-2:123456789012:pba-notifications",
    )

    notifier.send("researcher@example.com", "Dingo detected", "Open the application")

    assert client.requests == [
        {
            "TopicArn": "arn:aws:sns:ap-southeast-2:123456789012:pba-notifications",
            "Subject": "Dingo detected",
            "Message": "Open the application",
            "MessageAttributes": {
                "recipient": {"DataType": "String", "StringValue": "researcher@example.com"}
            },
        }
    ]


@pytest.mark.parametrize("subject", ["line one\nline two", "x" * 101])
def test_sns_notifier_rejects_unsafe_subjects(subject: str) -> None:
    notifier_type = getattr(sns, "SnsNotifier", None)
    assert notifier_type is not None, "SnsNotifier has not been implemented"
    notifier = notifier_type(client=RecordingSnsClient(), topic_arn="arn:aws:sns:region:account:topic")

    with pytest.raises(ValueError):
        notifier.send("researcher@example.com", subject, "body")
