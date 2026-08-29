output "api_base_url" {
  value = aws_apigatewayv2_api.main.api_endpoint
}

output "cognito_user_pool_id" {
  value = aws_cognito_user_pool.main.id
}

output "cognito_app_client_id" {
  value = aws_cognito_user_pool_client.web.id
}

output "cognito_oauth_domain" {
  value = "https://${aws_cognito_user_pool_domain.main.domain}.auth.${var.aws_region}.amazoncognito.com"
}

output "media_bucket" {
  value = aws_s3_bucket.media.id
}

output "external_provider_secret_arn" {
  value = aws_secretsmanager_secret.external_providers.arn
}

output "azure_worker_identity_secret_arn" {
  value = aws_secretsmanager_secret.azure_worker_identity.arn
}

output "azure_api_identity_secret_arn" {
  value = aws_secretsmanager_secret.azure_api_identity.arn
}

output "azure_notification_identity_secret_arn" {
  value = aws_secretsmanager_secret.azure_notification_identity.arn
}

output "notification_topic_arn" {
  value = aws_sns_topic.notifications.arn
}

output "notification_bridge_function_name" {
  value = aws_lambda_function.notification_bridge.function_name
}

output "media_worker_function_name" {
  value = aws_lambda_function.media_worker.function_name
}

output "media_worker_ecr_repository_url" {
  value = aws_ecr_repository.media_worker.repository_url
}

output "media_events_queue_url" {
  value = aws_sqs_queue.media_events.url
}
