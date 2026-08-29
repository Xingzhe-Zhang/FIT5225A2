from pathlib import Path
import re

import hcl2


ROOT = Path(__file__).resolve().parents[2]
AWS_MONITORING = ROOT / "infra" / "aws" / "monitoring.tf"
AZURE_MONITORING = ROOT / "infra" / "azure" / "monitoring.tf"
VERIFY_SCRIPT = ROOT / "scripts" / "verify-deployment.ps1"


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_monitoring_terraform_is_valid_hcl() -> None:
    for path in (AWS_MONITORING, AZURE_MONITORING):
        with path.open(encoding="utf-8") as source:
            hcl2.load(source)


def test_aws_monitoring_covers_api_lambda_and_dead_letter_queue() -> None:
    stack = read(AWS_MONITORING)

    assert 'resource "aws_sns_topic" "operations"' in stack
    assert stack.count('resource "aws_cloudwatch_metric_alarm"') >= 4
    for metric_name in ('"Errors"', '"Duration"', '"5xx"', '"ApproximateNumberOfMessagesVisible"'):
        assert metric_name in stack
    assert "aws_lambda_function.api.function_name" in stack
    assert "aws_apigatewayv2_api.main.id" in stack
    assert "aws_sqs_queue.media_dlq.name" in stack
    assert stack.count("aws_sns_topic.operations.arn") >= 4
    assert len(re.findall(r'treat_missing_data\s*=\s*"notBreaching"', stack)) >= 4


def test_aws_monitoring_exposes_dashboard_and_operational_outputs() -> None:
    stack = read(AWS_MONITORING)

    assert 'resource "aws_cloudwatch_dashboard" "operations"' in stack
    assert "aws_cloudwatch_log_group.api.name" in stack
    assert 'output "operations_alarm_topic_arn"' in stack
    assert 'output "operations_dashboard_name"' in stack
    assert 'output "media_dlq_url"' in stack


def test_azure_monitoring_covers_function_and_cosmos_failures() -> None:
    stack = read(AZURE_MONITORING)

    assert 'resource "azurerm_monitor_action_group" "operations"' in stack
    assert stack.count('resource "azurerm_monitor_metric_alert"') >= 2
    assert 'metric_name      = "Http5xx"' in stack
    assert 'metric_name      = "TotalRequests"' in stack
    assert 'name     = "StatusCode"' in stack
    assert 'values   = ["429"]' in stack
    assert "azurerm_linux_function_app.data_api.id" in stack
    assert "azurerm_cosmosdb_account.main.id" in stack
    assert stack.count("azurerm_monitor_action_group.operations.id") >= 2


def test_azure_monitoring_exposes_dashboard_and_operational_outputs() -> None:
    stack = read(AZURE_MONITORING)

    assert 'resource "azurerm_portal_dashboard" "operations"' in stack
    assert "azurerm_application_insights.functions.id" in stack
    assert "azurerm_log_analytics_workspace.main.id" in stack
    assert 'output "operations_action_group_id"' in stack
    assert 'output "operations_dashboard_id"' in stack
    assert 'output "application_insights_app_id"' in stack


def test_deployment_verifier_is_explicit_and_secret_safe() -> None:
    script = read(VERIFY_SCRIPT)

    for check in ("Health", "Auth", "Upload", "Query", "Tag", "Delete", "Notification", "Logs"):
        assert f'"{check}"' in script
    assert "PBA_API_BASE_URL" in script
    assert "PBA_ACCESS_TOKEN" in script
    assert "AllowMutation" in script
    assert "Authorization" in script
    assert "terraform apply" not in script.casefold()
    assert "terraform destroy" not in script.casefold()
    assert not re.search(r"Write-(Host|Output).*ACCESS_TOKEN", script, re.IGNORECASE)


def test_repository_contains_only_approved_markdown_documents() -> None:
    markdown_files = sorted(
        path.relative_to(ROOT).as_posix()
        for path in ROOT.rglob("*.md")
        if not any(
            part in {".git", ".pytest_cache", ".terraform", ".venv", "build", "node_modules"}
            for part in path.parts
        )
    )

    assert markdown_files == ["README.md", "TEAM_FRONTEND_RUNBOOK.md", "models/README.md"]


def test_deployment_verifier_preserves_human_evidence_boundaries() -> None:
    script = read(VERIFY_SCRIPT)

    assert "HUMAN REQUIRED" in script
    assert "CloudWatch" in script
    assert "request ID" in script
    assert "SNS confirmation" in script
