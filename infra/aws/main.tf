data "aws_caller_identity" "current" {}

locals {
  name              = "${var.project_name}-${var.environment}"
  google_enabled    = var.enable_google_provider
  microsoft_enabled = var.enable_microsoft_provider
  identity_providers = concat(
    ["COGNITO"],
    local.google_enabled ? ["Google"] : [],
    local.microsoft_enabled ? ["Microsoft"] : [],
  )
  api_cosmos_secret_arn          = var.enable_component_cosmos_identities ? aws_secretsmanager_secret.azure_api_identity.arn : aws_secretsmanager_secret.azure_worker_identity.arn
  notification_cosmos_secret_arn = var.enable_component_cosmos_identities ? aws_secretsmanager_secret.azure_notification_identity.arn : aws_secretsmanager_secret.azure_worker_identity.arn
  tags = {
    Project     = var.project_name
    Environment = var.environment
    ManagedBy   = "Terraform"
  }
}

resource "aws_cognito_user_pool" "main" {
  name                     = "${local.name}-users"
  username_attributes      = ["email"]
  auto_verified_attributes = ["email"]
  deletion_protection      = var.environment == "production" ? "ACTIVE" : "INACTIVE"

  password_policy {
    minimum_length                   = 12
    require_lowercase                = true
    require_numbers                  = true
    require_symbols                  = true
    require_uppercase                = true
    temporary_password_validity_days = 3
  }

  verification_message_template {
    default_email_option = "CONFIRM_WITH_CODE"
  }

  # Cognito schemas are immutable after pool creation. Keep any attributes
  # already recorded in the existing pool state without attempting removal.
  lifecycle {
    ignore_changes = [schema]
  }
}

resource "aws_cognito_identity_provider" "google" {
  count         = local.google_enabled ? 1 : 0
  user_pool_id  = aws_cognito_user_pool.main.id
  provider_name = "Google"
  provider_type = "Google"

  provider_details = {
    authorize_scopes = "openid email profile"
    client_id        = var.google_client_id
    client_secret    = var.google_client_secret
  }

  attribute_mapping = {
    email       = "email"
    username    = "sub"
    given_name  = "given_name"
    family_name = "family_name"
  }

  lifecycle {
    precondition {
      condition     = var.google_client_id != null && var.google_client_secret != null
      error_message = "Google federation requires both client ID and client secret variables."
    }
  }
}

resource "aws_cognito_identity_provider" "microsoft" {
  count         = local.microsoft_enabled ? 1 : 0
  user_pool_id  = aws_cognito_user_pool.main.id
  provider_name = "Microsoft"
  provider_type = "OIDC"

  provider_details = {
    attributes_request_method = "GET"
    authorize_scopes          = "openid email profile"
    client_id                 = var.microsoft_client_id
    client_secret             = var.microsoft_client_secret
    oidc_issuer               = "https://login.microsoftonline.com/${var.microsoft_tenant}/v2.0"
  }

  attribute_mapping = {
    email       = "email"
    username    = "sub"
    given_name  = "given_name"
    family_name = "family_name"
  }

  lifecycle {
    precondition {
      condition     = var.microsoft_client_id != null && var.microsoft_client_secret != null
      error_message = "Microsoft federation requires both client ID and client secret variables."
    }
  }
}

resource "aws_cognito_user_pool_client" "web" {
  name                                 = "${local.name}-web"
  user_pool_id                         = aws_cognito_user_pool.main.id
  generate_secret                      = false
  callback_urls                        = var.frontend_callback_urls
  logout_urls                          = var.frontend_logout_urls
  allowed_oauth_flows_user_pool_client = true
  allowed_oauth_flows                  = ["code"]
  allowed_oauth_scopes                 = ["openid", "email", "profile"]
  supported_identity_providers         = local.identity_providers
  prevent_user_existence_errors        = "ENABLED"
  enable_token_revocation              = true
  access_token_validity                = 60
  id_token_validity                    = 60
  refresh_token_validity               = 7

  token_validity_units {
    access_token  = "minutes"
    id_token      = "minutes"
    refresh_token = "days"
  }

  explicit_auth_flows = [
    "ALLOW_REFRESH_TOKEN_AUTH",
    "ALLOW_USER_SRP_AUTH",
  ]

  depends_on = [
    aws_cognito_identity_provider.google,
    aws_cognito_identity_provider.microsoft,
  ]
}

resource "aws_cognito_user_pool_domain" "main" {
  domain       = var.cognito_domain_prefix
  user_pool_id = aws_cognito_user_pool.main.id
}

resource "aws_s3_bucket" "media" {
  bucket = "${local.name}-media-${data.aws_caller_identity.current.account_id}"
}

resource "aws_s3_bucket_public_access_block" "media" {
  bucket                  = aws_s3_bucket.media.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_server_side_encryption_configuration" "media" {
  bucket = aws_s3_bucket.media.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_versioning" "media" {
  bucket = aws_s3_bucket.media.id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_cors_configuration" "media" {
  bucket = aws_s3_bucket.media.id

  cors_rule {
    allowed_methods = ["GET", "HEAD", "PUT"]
    allowed_origins = var.frontend_origins
    allowed_headers = ["*"]
    expose_headers  = ["ETag"]
    max_age_seconds = 3000
  }
}

resource "aws_sqs_queue" "media_dlq" {
  name                      = "${local.name}-media-dlq"
  message_retention_seconds = 1209600
  sqs_managed_sse_enabled   = true
}

resource "aws_sqs_queue" "media_events" {
  name                       = "${local.name}-media-events"
  visibility_timeout_seconds = 900
  sqs_managed_sse_enabled    = true
  redrive_policy = jsonencode({
    deadLetterTargetArn = aws_sqs_queue.media_dlq.arn
    maxReceiveCount     = 5
  })
}

resource "aws_cloudwatch_event_bus" "application" {
  name = "${local.name}-events"
}

resource "aws_sns_topic" "notifications" {
  name              = "${local.name}-notifications"
  kms_master_key_id = "alias/aws/sns"
}

resource "aws_secretsmanager_secret" "external_providers" {
  name                    = "${local.name}/external-identity-providers"
  description             = "Human-managed OAuth provider secrets; Terraform creates no secret value."
  recovery_window_in_days = 7
}

resource "aws_secretsmanager_secret" "azure_worker_identity" {
  name                    = "${local.name}/azure-worker-identity"
  description             = "Human-managed Azure credential used only by the media worker."
  recovery_window_in_days = 7
}

resource "aws_secretsmanager_secret" "azure_api_identity" {
  name                    = "${local.name}/azure-api-identity"
  description             = "Human-managed least-privilege Azure credential used only by the API Lambda."
  recovery_window_in_days = 7
}

resource "aws_secretsmanager_secret" "azure_notification_identity" {
  name                    = "${local.name}/azure-notification-identity"
  description             = "Human-managed least-privilege Azure credential used only by the notification bridge."
  recovery_window_in_days = 7
}

resource "aws_ecr_repository" "media_worker" {
  name                 = "${local.name}-media-worker"
  image_tag_mutability = "MUTABLE"

  image_scanning_configuration {
    scan_on_push = true
  }

  encryption_configuration {
    encryption_type = "AES256"
  }
}

resource "aws_ecr_lifecycle_policy" "media_worker" {
  repository = aws_ecr_repository.media_worker.name
  policy = jsonencode({
    rules = [{
      rulePriority = 1
      description  = "Retain the three newest worker images"
      selection = {
        tagStatus   = "any"
        countType   = "imageCountMoreThan"
        countNumber = 3
      }
      action = { type = "expire" }
    }]
  })
}

resource "aws_iam_role" "api_lambda" {
  name = "${local.name}-api-lambda"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "lambda.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

data "aws_iam_policy_document" "api_lambda" {
  statement {
    sid       = "WriteOwnLogs"
    actions   = ["logs:CreateLogStream", "logs:PutLogEvents"]
    resources = ["${aws_cloudwatch_log_group.api.arn}:*"]
  }

  statement {
    sid     = "MediaPrefixAccess"
    actions = ["s3:GetObject", "s3:PutObject", "s3:DeleteObject", "s3:DeleteObjectVersion"]
    resources = [
      "${aws_s3_bucket.media.arn}/originals/*",
      "${aws_s3_bucket.media.arn}/derived/*",
      "${aws_s3_bucket.media.arn}/quarantine/*",
      "${aws_s3_bucket.media.arn}/temporary-query/*",
      "${aws_s3_bucket.media.arn}/profiles/*",
    ]
  }

  statement {
    sid       = "ListMediaBucket"
    actions   = ["s3:ListBucket", "s3:ListBucketVersions"]
    resources = [aws_s3_bucket.media.arn]
  }

  statement {
    sid       = "PublishEvents"
    actions   = ["sqs:SendMessage", "events:PutEvents"]
    resources = [aws_sqs_queue.media_events.arn, aws_cloudwatch_event_bus.application.arn]
  }

  statement {
    sid       = "PublishAndCreateNotificationSubscriptions"
    actions   = ["sns:Publish", "sns:Subscribe", "sns:ListSubscriptionsByTopic"]
    resources = [aws_sns_topic.notifications.arn]
  }

  statement {
    sid     = "ManageExistingNotificationSubscriptions"
    actions = ["sns:Unsubscribe", "sns:GetSubscriptionAttributes", "sns:SetSubscriptionAttributes"]
    resources = [
      aws_sns_topic.notifications.arn,
      "${aws_sns_topic.notifications.arn}:*",
    ]
  }

  statement {
    sid       = "ReadIdentityProviderSecret"
    actions   = ["secretsmanager:GetSecretValue"]
    resources = [aws_secretsmanager_secret.external_providers.arn]
  }

  statement {
    sid       = "ReadCosmosCredentialSecret"
    actions   = ["secretsmanager:GetSecretValue"]
    resources = [local.api_cosmos_secret_arn]
  }

  statement {
    sid       = "InvokeMediaWorker"
    actions   = ["lambda:InvokeFunction"]
    resources = [aws_lambda_function.media_worker.arn]
  }

  statement {
    sid       = "ManageOwnCognitoProfile"
    actions   = ["cognito-idp:GetUser", "cognito-idp:UpdateUserAttributes"]
    resources = ["*"]
  }
}

resource "aws_iam_role_policy" "api_lambda" {
  name   = "${local.name}-api-runtime"
  role   = aws_iam_role.api_lambda.id
  policy = data.aws_iam_policy_document.api_lambda.json
}

resource "aws_cloudwatch_log_group" "api" {
  name              = "/aws/lambda/${local.name}-api"
  retention_in_days = var.log_retention_days
}

resource "aws_lambda_function" "api" {
  function_name    = "${local.name}-api"
  role             = aws_iam_role.api_lambda.arn
  runtime          = "python3.12"
  handler          = "lambda_adapter.handler"
  filename         = var.api_package_path
  source_code_hash = filebase64sha256(var.api_package_path)
  timeout          = 30
  memory_size      = 512

  environment {
    variables = {
      APP_ENV                              = var.environment
      MEDIA_BUCKET                         = aws_s3_bucket.media.id
      MEDIA_QUEUE_URL                      = aws_sqs_queue.media_events.url
      EVENT_BUS_NAME                       = aws_cloudwatch_event_bus.application.name
      NOTIFICATION_TOPIC                   = aws_sns_topic.notifications.arn
      COGNITO_USER_POOL_ID                 = aws_cognito_user_pool.main.id
      COGNITO_APP_CLIENT_ID                = aws_cognito_user_pool_client.web.id
      COGNITO_OAUTH_DOMAIN                 = "https://${aws_cognito_user_pool_domain.main.domain}.auth.${var.aws_region}.amazoncognito.com"
      COGNITO_REDIRECT_URI                 = var.frontend_callback_urls[0]
      API_BASE_URL                         = aws_apigatewayv2_api.main.api_endpoint
      FRONTEND_BASE_URL                    = var.frontend_base_url
      AZURE_DATA_API_BASE_URL              = var.azure_data_api_base_url
      COSMOS_ENDPOINT                      = var.worker_cosmos_endpoint
      COSMOS_DATABASE                      = var.worker_cosmos_database
      COSMOS_MEDIA_CONTAINER               = var.worker_cosmos_media_container
      COSMOS_SUBSCRIPTIONS_CONTAINER       = var.worker_cosmos_subscriptions_container
      COSMOS_DELIVERY_LEDGER_CONTAINER     = var.worker_cosmos_delivery_ledger_container
      COSMOS_DELETION_OPERATIONS_CONTAINER = var.worker_cosmos_deletion_operations_container
      AZURE_COSMOS_SECRET_ARN              = local.api_cosmos_secret_arn
      MEDIA_WORKER_FUNCTION_NAME           = aws_lambda_function.media_worker.function_name
      CORS_ORIGINS                         = join(",", var.frontend_origins)
      EXTERNAL_PROVIDERS                   = join(",", concat(local.google_enabled ? ["Google"] : [], local.microsoft_enabled ? ["Microsoft"] : []))
    }
  }

  depends_on = [aws_cloudwatch_log_group.api, aws_iam_role_policy.api_lambda]
}

resource "aws_iam_role" "notification_bridge" {
  name = "${local.name}-notification-bridge"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "lambda.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_cloudwatch_log_group" "notification_bridge" {
  name              = "/aws/lambda/${local.name}-notification-bridge"
  retention_in_days = var.log_retention_days
}

data "aws_iam_policy_document" "notification_bridge" {
  statement {
    sid       = "WriteLogs"
    actions   = ["logs:CreateLogStream", "logs:PutLogEvents"]
    resources = ["${aws_cloudwatch_log_group.notification_bridge.arn}:*"]
  }
  statement {
    sid       = "ReadCosmosSecret"
    actions   = ["secretsmanager:GetSecretValue"]
    resources = [local.notification_cosmos_secret_arn]
  }
  statement {
    sid       = "SnsTopicAccess"
    actions   = ["sns:Publish", "sns:ListSubscriptionsByTopic"]
    resources = [aws_sns_topic.notifications.arn]
  }
  statement {
    sid     = "SnsSubscriptionAccess"
    actions = ["sns:GetSubscriptionAttributes", "sns:SetSubscriptionAttributes"]
    resources = [
      aws_sns_topic.notifications.arn,
      "${aws_sns_topic.notifications.arn}:*",
    ]
  }
}

resource "aws_iam_role_policy" "notification_bridge" {
  name   = "${local.name}-notification-bridge-runtime"
  role   = aws_iam_role.notification_bridge.id
  policy = data.aws_iam_policy_document.notification_bridge.json
}

resource "aws_lambda_function" "notification_bridge" {
  function_name    = "${local.name}-notification-bridge"
  role             = aws_iam_role.notification_bridge.arn
  runtime          = "python3.12"
  handler          = "notification_adapter.handler"
  filename         = var.api_package_path
  source_code_hash = filebase64sha256(var.api_package_path)
  timeout          = 30
  memory_size      = 256

  environment {
    variables = {
      NOTIFICATION_TOPIC               = aws_sns_topic.notifications.arn
      AZURE_COSMOS_SECRET_ARN          = local.notification_cosmos_secret_arn
      COSMOS_ENDPOINT                  = var.worker_cosmos_endpoint
      COSMOS_DATABASE                  = var.worker_cosmos_database
      COSMOS_SUBSCRIPTIONS_CONTAINER   = var.worker_cosmos_subscriptions_container
      COSMOS_DELIVERY_LEDGER_CONTAINER = var.worker_cosmos_delivery_ledger_container
      FRONTEND_BASE_URL                = var.frontend_base_url
    }
  }

  depends_on = [aws_cloudwatch_log_group.notification_bridge, aws_iam_role_policy.notification_bridge]
}

resource "aws_cloudwatch_event_rule" "tagging_completed" {
  name           = "${local.name}-tagging-completed"
  event_bus_name = aws_cloudwatch_event_bus.application.name
  event_pattern  = jsonencode({ source = ["pacific-bioarchive.tagging"], "detail-type" = ["TaggingCompleted"] })
}

resource "aws_cloudwatch_event_target" "notification_bridge" {
  rule           = aws_cloudwatch_event_rule.tagging_completed.name
  event_bus_name = aws_cloudwatch_event_bus.application.name
  target_id      = "notification-bridge"
  arn            = aws_lambda_function.notification_bridge.arn
}

resource "aws_lambda_permission" "notification_bridge_events" {
  statement_id  = "AllowEventBridgeInvoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.notification_bridge.function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.tagging_completed.arn
}

resource "aws_apigatewayv2_api" "main" {
  name          = "${local.name}-http"
  protocol_type = "HTTP"

  cors_configuration {
    allow_headers = ["authorization", "content-type", "x-request-id"]
    allow_methods = ["GET", "POST", "PUT", "DELETE", "OPTIONS"]
    allow_origins = var.frontend_origins
  }
}

resource "aws_apigatewayv2_authorizer" "cognito" {
  api_id           = aws_apigatewayv2_api.main.id
  authorizer_type  = "JWT"
  identity_sources = ["$request.header.Authorization"]
  name             = "cognito-access-token"

  jwt_configuration {
    audience = [aws_cognito_user_pool_client.web.id]
    issuer   = "https://cognito-idp.${var.aws_region}.amazonaws.com/${aws_cognito_user_pool.main.id}"
  }
}

resource "aws_apigatewayv2_integration" "api" {
  api_id                 = aws_apigatewayv2_api.main.id
  integration_type       = "AWS_PROXY"
  integration_uri        = aws_lambda_function.api.invoke_arn
  integration_method     = "POST"
  payload_format_version = "2.0"
}

locals {
  public_routes = toset(["GET /health", "GET /auth/config"])
  protected_routes = toset([
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
  ])
}

resource "aws_apigatewayv2_route" "public" {
  for_each  = local.public_routes
  api_id    = aws_apigatewayv2_api.main.id
  route_key = each.value
  target    = "integrations/${aws_apigatewayv2_integration.api.id}"
}

resource "aws_apigatewayv2_route" "protected" {
  for_each           = local.protected_routes
  api_id             = aws_apigatewayv2_api.main.id
  route_key          = each.value
  target             = "integrations/${aws_apigatewayv2_integration.api.id}"
  authorization_type = "JWT"
  authorizer_id      = aws_apigatewayv2_authorizer.cognito.id
}

resource "aws_apigatewayv2_stage" "default" {
  api_id      = aws_apigatewayv2_api.main.id
  name        = "$default"
  auto_deploy = true

  default_route_settings {
    throttling_burst_limit = 50
    throttling_rate_limit  = 25
  }
}

resource "aws_lambda_permission" "api_gateway" {
  statement_id  = "AllowApiGatewayInvoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.api.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_apigatewayv2_api.main.execution_arn}/*/*"
}

resource "aws_s3_bucket_notification" "media_events" {
  bucket = aws_s3_bucket.media.id

  queue {
    queue_arn     = aws_sqs_queue.media_events.arn
    events        = ["s3:ObjectCreated:Put", "s3:ObjectCreated:CompleteMultipartUpload"]
    filter_prefix = "originals/"
  }

  depends_on = [aws_sqs_queue_policy.media_events]
}

data "aws_iam_policy_document" "media_events" {
  statement {
    effect = "Allow"
    principals {
      type        = "Service"
      identifiers = ["s3.amazonaws.com"]
    }
    actions   = ["sqs:SendMessage"]
    resources = [aws_sqs_queue.media_events.arn]
    condition {
      test     = "ArnEquals"
      variable = "aws:SourceArn"
      values   = [aws_s3_bucket.media.arn]
    }
    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [data.aws_caller_identity.current.account_id]
    }
  }
}

resource "aws_sqs_queue_policy" "media_events" {
  queue_url = aws_sqs_queue.media_events.id
  policy    = data.aws_iam_policy_document.media_events.json
}

resource "aws_iam_role" "media_worker" {
  name = "${local.name}-media-worker"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "lambda.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

data "aws_iam_policy_document" "media_worker" {
  statement {
    sid       = "WorkerLogs"
    actions   = ["logs:CreateLogStream", "logs:PutLogEvents"]
    resources = ["${aws_cloudwatch_log_group.media_worker.arn}:*"]
  }
  statement {
    sid     = "WorkerMediaAccess"
    actions = ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"]
    resources = [
      "${aws_s3_bucket.media.arn}/originals/*",
      "${aws_s3_bucket.media.arn}/derived/*",
      "${aws_s3_bucket.media.arn}/quarantine/*",
      "${aws_s3_bucket.media.arn}/temporary-query/*",
    ]
  }
  statement {
    sid       = "WorkerModelRead"
    actions   = ["s3:GetObject"]
    resources = ["${aws_s3_bucket.media.arn}/models/*"]
  }
  statement {
    sid       = "WorkerListMediaBucket"
    actions   = ["s3:ListBucket"]
    resources = [aws_s3_bucket.media.arn]
  }
  statement {
    sid       = "WorkerQueueAccess"
    actions   = ["sqs:ReceiveMessage", "sqs:DeleteMessage", "sqs:GetQueueAttributes", "sqs:ChangeMessageVisibility"]
    resources = [aws_sqs_queue.media_events.arn]
  }
  statement {
    sid       = "WorkerPublishEvents"
    actions   = ["sqs:SendMessage", "events:PutEvents"]
    resources = [aws_sqs_queue.media_events.arn, aws_cloudwatch_event_bus.application.arn]
  }
  statement {
    sid       = "ReadAzureWorkerIdentity"
    actions   = ["secretsmanager:GetSecretValue"]
    resources = [aws_secretsmanager_secret.azure_worker_identity.arn]
  }
}

resource "aws_iam_role_policy" "media_worker" {
  name   = "${local.name}-media-worker-runtime"
  role   = aws_iam_role.media_worker.id
  policy = data.aws_iam_policy_document.media_worker.json
}

resource "aws_cloudwatch_log_group" "media_worker" {
  name              = "/aws/lambda/${local.name}-media-worker"
  retention_in_days = var.log_retention_days
}

resource "aws_lambda_function" "media_worker" {
  function_name    = "${local.name}-media-worker"
  role             = aws_iam_role.media_worker.arn
  package_type     = var.worker_image_uri == null ? "Zip" : "Image"
  runtime          = var.worker_image_uri == null ? "python3.12" : null
  handler          = var.worker_image_uri == null ? "worker_adapter.handler" : null
  filename         = var.worker_image_uri == null ? var.worker_package_path : null
  source_code_hash = var.worker_image_uri == null ? filebase64sha256(var.worker_package_path) : null
  image_uri        = var.worker_image_uri
  architectures    = ["x86_64"]
  timeout          = 900
  memory_size      = 3008

  ephemeral_storage {
    size = 10240
  }

  environment {
    variables = {
      APP_ENV                 = var.environment
      MEDIA_BUCKET            = aws_s3_bucket.media.id
      MEDIA_QUEUE_URL         = aws_sqs_queue.media_events.url
      EVENT_BUS_NAME          = aws_cloudwatch_event_bus.application.name
      NOTIFICATION_TOPIC      = aws_sns_topic.notifications.arn
      COGNITO_USER_POOL_ID    = aws_cognito_user_pool.main.id
      COGNITO_APP_CLIENT_ID   = aws_cognito_user_pool_client.web.id
      COSMOS_ENDPOINT         = var.worker_cosmos_endpoint
      COSMOS_DATABASE         = var.worker_cosmos_database
      AZURE_WORKER_SECRET_ARN = aws_secretsmanager_secret.azure_worker_identity.arn
      ML_MODEL_DIR            = var.worker_ml_model_dir
      MODEL_MANIFEST_URI      = var.worker_model_manifest_uri
      MODEL_CACHE_DIR         = var.worker_model_cache_dir
      MODEL_DEVICE            = "cpu"
    }
  }

  layers = var.worker_image_uri == null ? var.worker_layer_arns : []

  depends_on = [aws_cloudwatch_log_group.media_worker, aws_iam_role_policy.media_worker]
}

resource "aws_lambda_event_source_mapping" "media_worker" {
  event_source_arn                   = aws_sqs_queue.media_events.arn
  function_name                      = aws_lambda_function.media_worker.arn
  batch_size                         = 1
  maximum_batching_window_in_seconds = 5
  function_response_types            = ["ReportBatchItemFailures"]
  enabled                            = var.worker_enabled
}
