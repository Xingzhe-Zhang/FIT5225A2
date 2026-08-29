from __future__ import annotations

import re
from pathlib import Path

import hcl2


ROOT = Path(__file__).resolve().parents[2]


def read_stack(cloud: str) -> str:
    files = sorted((ROOT / "infra" / cloud).glob("*.tf"))
    assert files, f"missing {cloud} Terraform stack"
    return "\n".join(path.read_text(encoding="utf-8") for path in files)


def test_aws_stack_contains_required_serverless_resources() -> None:
    stack = read_stack("aws")
    required = (
        "aws_cognito_user_pool",
        "aws_cognito_user_pool_client",
        "aws_apigatewayv2_api",
        "aws_apigatewayv2_authorizer",
        "aws_s3_bucket_public_access_block",
        "aws_s3_bucket_server_side_encryption_configuration",
        "aws_lambda_function",
        "aws_sqs_queue",
        "aws_cloudwatch_event_bus",
        "aws_sns_topic",
        "aws_secretsmanager_secret",
        "aws_cloudwatch_log_group",
    )
    for resource in required:
        assert resource in stack, resource
    assert "block_public_acls       = true" in stack
    assert 'authorization_type = "JWT"' in stack


def test_azure_stack_contains_required_data_and_function_resources() -> None:
    stack = read_stack("azure")
    required = (
        "azurerm_resource_group",
        "azurerm_linux_function_app",
        "azurerm_cosmosdb_account",
        "azurerm_cosmosdb_sql_database",
        "azurerm_cosmosdb_sql_container",
        "azurerm_key_vault",
        "azurerm_application_insights",
        "azurerm_role_assignment",
    )
    for resource in required:
        assert resource in stack, resource
    assert re.search(r'partition_key_paths\s*=\s*\["/owner_sub"\]', stack)


def test_cloud_stacks_do_not_embed_credentials() -> None:
    combined = read_stack("aws") + read_stack("azure")
    forbidden = ("AKIA", "client_secret = \"", "access_key = \"", "password = \"")
    for marker in forbidden:
        assert marker not in combined


def test_every_terraform_file_parses_as_hcl() -> None:
    for path in sorted((ROOT / "infra").glob("**/*.tf")):
        with path.open(encoding="utf-8") as stream:
            parsed = hcl2.load(stream)
        assert isinstance(parsed, dict), path


def test_aws_api_package_uses_the_real_asgi_entrypoint_and_exposes_required_outputs() -> None:
    stack = read_stack("aws")
    outputs = (ROOT / "infra" / "aws" / "outputs.tf").read_text(encoding="utf-8")
    build_script = (ROOT / "scripts" / "project_tasks.py").read_text(encoding="utf-8")

    assert "lambda_stub" not in stack
    assert 'handler          = "lambda_adapter.handler"' in stack
    assert "api_package_path" in stack
    assert "backend.aws_api.app:app" in build_script
    assert "requirements-lambda.lock" in build_script
    assert "--require-hashes" in build_script
    for output in ("api_base_url", "cognito_user_pool_id", "cognito_app_client_id", "notification_topic_arn"):
        assert f'output "{output}"' in outputs
    for route in (
        "GET /media",
        "GET /subscriptions",
        "PUT /subscriptions/{subscription_id}",
        "DELETE /subscriptions/{subscription_id}",
    ):
        assert f'"{route}"' in stack
    assert '"GET /media"' in stack


def test_aws_environment_and_cors_match_application_runtime_values() -> None:
    main = (ROOT / "infra" / "aws" / "main.tf").read_text(encoding="utf-8")
    variables = (ROOT / "infra" / "aws" / "variables.tf").read_text(encoding="utf-8")

    assert re.search(r'variable\s+"environment"\s*\{.*?default\s*=\s*"development"', variables, re.DOTALL)
    for value in ("local", "test", "development", "production"):
        assert f'"{value}"' in variables
    assert 'allow_methods = ["GET", "POST", "PUT", "DELETE", "OPTIONS"]' in main


def test_api_gateway_exposes_only_minimal_public_routes_and_protects_every_business_route() -> None:
    main = (ROOT / "infra" / "aws" / "main.tf").read_text(encoding="utf-8")

    public_routes = re.search(r'public_routes\s*=\s*toset\(\[(.*?)\]\)', main, re.DOTALL)
    protected_routes = re.search(r'protected_routes\s*=\s*toset\(\[(.*?)\]\)', main, re.DOTALL)
    assert public_routes is not None
    assert protected_routes is not None
    assert set(re.findall(r'"([A-Z]+ [^\"]+)"', public_routes.group(1))) == {
        "GET /health",
        "GET /auth/config",
    }
    expected_protected = {
        "POST /uploads/reservations",
        "DELETE /uploads/reservations/{media_id}",
        "POST /queries/tags",
        "POST /queries/species",
        "POST /queries/thumbnail",
        "POST /queries/by-file",
        "POST /media/tags",
        "GET /media",
        "DELETE /media",
        "DELETE /media/{media_id}",
        "GET /subscriptions",
        "POST /subscriptions",
        "PUT /subscriptions/{subscription_id}",
        "DELETE /subscriptions/{subscription_id}",
        "GET /profile",
        "PUT /profile",
    }
    assert set(re.findall(r'"([A-Z]+ [^\"]+)"', protected_routes.group(1))) == expected_protected


def test_temporary_query_objects_are_available_to_api_and_worker() -> None:
    main = (ROOT / "infra" / "aws" / "main.tf").read_text(encoding="utf-8")

    # The API stages the reference file and the worker reads it (and may write
    # extracted video frames) before the temporary prefix is cleaned up.
    assert main.count('"${aws_s3_bucket.media.arn}/temporary-query/*"') >= 2


def test_sns_attribute_permissions_cover_topic_and_subscription_arns() -> None:
    main = (ROOT / "infra" / "aws" / "main.tf").read_text(encoding="utf-8")
    resource_sets = re.findall(
        r'resources\s*=\s*\[\s*aws_sns_topic\.notifications\.arn,\s*'
        r'"\$\{aws_sns_topic\.notifications\.arn\}:\*",\s*\]',
        main,
    )

    assert len(resource_sets) >= 2


def test_existing_cognito_pool_schema_is_not_modified() -> None:
    main = (ROOT / "infra" / "aws" / "main.tf").read_text(encoding="utf-8")
    user_pool = re.search(r'resource\s+"aws_cognito_user_pool"\s+"main"\s*\{(.*?)\n\}', main, re.DOTALL)
    assert user_pool is not None
    assert "ignore_changes = [schema]" in user_pool.group(1)


def test_external_identity_providers_are_real_opt_in_cognito_integrations() -> None:
    main = (ROOT / "infra" / "aws" / "main.tf").read_text(encoding="utf-8")
    variables = (ROOT / "infra" / "aws" / "variables.tf").read_text(encoding="utf-8")

    assert 'provider_type = "Google"' in main
    assert 'provider_name = "Google"' in main
    assert 'provider_type = "OIDC"' in main
    assert 'provider_name = "Microsoft"' in main
    for attribute in ("email", "given_name", "family_name"):
        assert re.search(rf"{attribute}\s*=\s*\"{attribute}\"", main)
    assert re.search(r'allowed_oauth_flows\s*=\s*\["code"\]', main)
    assert "supported_identity_providers         = local.identity_providers" in main
    assert 'EXTERNAL_PROVIDERS                   = join(",", concat(local.google_enabled' in main
    for variable in ("enable_google_provider", "enable_microsoft_provider"):
        block = re.search(rf'variable\s+"{variable}"\s*\{{(.*?)\n\}}', variables, re.DOTALL)
        assert block is not None
        assert "default = false" in block.group(1)


def test_obsolete_lambda_stub_is_rejected() -> None:
    assert not (ROOT / "infra" / "aws" / "lambda_stub").exists()


def test_infrastructure_validation_builds_the_api_package_before_static_or_terraform_checks() -> None:
    tasks = (ROOT / "scripts" / "project_tasks.py").read_text(encoding="utf-8")
    validator = tasks.split("def validate_infra", 1)[1].split("def lock_terraform_providers", 1)[0]

    assert "build_aws_api_package()" in validator
    assert validator.index("build_aws_api_package()") < validator.index("test_terraform_static.py")


def test_aws_api_package_build_targets_lambda_linux_python() -> None:
    build_script = (ROOT / "scripts" / "project_tasks.py").read_text(encoding="utf-8")

    assert 'LAMBDA_WHEEL_PLATFORM = "manylinux2014_x86_64"' in build_script
    assert 'LAMBDA_PYTHON_VERSION = "312"' in build_script
    assert "--only-binary=:all:" in build_script
    assert "--ignore-installed" in build_script


def test_lambda_requirements_cover_imports_reached_by_the_runtime_entrypoint() -> None:
    requirements = (ROOT / "backend" / "aws_api" / "requirements-lambda.txt").read_text(encoding="utf-8")

    for distribution in (
        "fastapi==",
        "mangum==",
        "pydantic==",
        "pydantic-settings==",
        "PyJWT[crypto]==",
        "httpx==",
        "Pillow==",
        "python-multipart==",
    ):
        assert distribution in requirements


def test_lambda_lock_is_fully_resolved_and_hash_checked() -> None:
    lock_path = ROOT / "backend" / "aws_api" / "requirements-lambda.lock"
    assert lock_path.exists()
    logical_lines = lock_path.read_text(encoding="utf-8").replace("\\\n", " ").splitlines()
    locked: set[str] = set()
    for line in logical_lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        requirement = stripped.split()[0]
        assert "==" in requirement, requirement
        assert "--hash=sha256:" in stripped, requirement
        locked.add(requirement.split("==", 1)[0].split("[", 1)[0].casefold())

    expected = {
        "annotated-types",
        "anyio",
        "certifi",
        "cffi",
        "cryptography",
        "fastapi",
        "h11",
        "httpcore",
        "httpx",
        "idna",
        "mangum",
        "pillow",
        "pydantic",
        "pydantic-core",
        "pydantic-settings",
        "pycparser",
        "pyjwt",
        "python-dotenv",
        "python-multipart",
        "starlette",
        "typing-extensions",
    }
    assert locked == expected
