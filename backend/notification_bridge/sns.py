"""SNS-compatible implementation of the baseline notifier boundary."""

from __future__ import annotations

from typing import Protocol


class SnsPublishClient(Protocol):
    def publish(self, **kwargs: object) -> object: ...


class SnsNotifier:
    """Publishes through a fixed topic using boto3-compatible keyword arguments."""

    def __init__(self, *, client: SnsPublishClient, topic_arn: str) -> None:
        if not topic_arn.startswith("arn:aws:sns:"):
            raise ValueError("topic_arn must identify an SNS topic")
        self._client = client
        self._topic_arn = topic_arn

    def send(self, recipient: str, subject: str, body: str) -> None:
        if "@" not in recipient or any(character in recipient for character in "\r\n"):
            raise ValueError("recipient must be an email address")
        if not subject or len(subject) > 100 or any(character in subject for character in "\r\n"):
            raise ValueError("SNS subject must be one line and at most 100 characters")
        if not body:
            raise ValueError("notification body is required")
        self._client.publish(
            TopicArn=self._topic_arn,
            Subject=subject,
            Message=body,
            MessageAttributes={
                "recipient": {"DataType": "String", "StringValue": recipient}
            },
        )
