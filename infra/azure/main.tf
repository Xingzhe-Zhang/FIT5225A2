data "azurerm_client_config" "current" {}

locals {
  name                = "${var.project_name}-${var.environment}"
  resource_group_name = coalesce(var.resource_group_name, "${local.name}-rg")
  compact_name        = replace("${var.project_name}${var.environment}${var.unique_suffix}", "-", "")
  tags = {
    Project     = var.project_name
    Environment = var.environment
    ManagedBy   = "Terraform"
  }
}

resource "azurerm_resource_group" "main" {
  name     = local.resource_group_name
  location = var.azure_location
  tags     = local.tags
}

resource "azurerm_storage_account" "functions" {
  name                            = substr(local.compact_name, 0, 24)
  resource_group_name             = azurerm_resource_group.main.name
  location                        = azurerm_resource_group.main.location
  account_tier                    = "Standard"
  account_replication_type        = "LRS"
  min_tls_version                 = "TLS1_2"
  allow_nested_items_to_be_public = false
  shared_access_key_enabled       = true
  tags                            = local.tags
}

resource "azurerm_service_plan" "functions" {
  name                = "${local.name}-plan"
  resource_group_name = azurerm_resource_group.main.name
  location            = azurerm_resource_group.main.location
  os_type             = "Linux"
  sku_name            = "Y1"
  tags                = local.tags
}

resource "azurerm_log_analytics_workspace" "main" {
  name                = "${local.name}-logs"
  resource_group_name = azurerm_resource_group.main.name
  location            = azurerm_resource_group.main.location
  sku                 = "PerGB2018"
  retention_in_days   = var.log_retention_days
  tags                = local.tags
}

resource "azurerm_application_insights" "functions" {
  name                = "${local.name}-insights"
  resource_group_name = azurerm_resource_group.main.name
  location            = azurerm_resource_group.main.location
  workspace_id        = azurerm_log_analytics_workspace.main.id
  application_type    = "web"
  tags                = local.tags
}

resource "azurerm_key_vault" "main" {
  name                       = substr("pba-${var.environment}-${var.unique_suffix}", 0, 24)
  location                   = azurerm_resource_group.main.location
  resource_group_name        = azurerm_resource_group.main.name
  tenant_id                  = data.azurerm_client_config.current.tenant_id
  sku_name                   = "standard"
  rbac_authorization_enabled = true
  purge_protection_enabled   = var.environment == "production"
  soft_delete_retention_days = 7
  tags                       = local.tags
}

resource "azurerm_cosmosdb_account" "main" {
  name                = "${local.name}-${var.unique_suffix}"
  location            = azurerm_resource_group.main.location
  resource_group_name = azurerm_resource_group.main.name
  offer_type          = "Standard"
  kind                = "GlobalDocumentDB"
  # The university tenant blocks application registration. The account key is
  # stored only in AWS Secrets Manager for the cross-cloud worker fallback.
  local_authentication_enabled = true

  capabilities {
    name = "EnableServerless"
  }

  consistency_policy {
    consistency_level = "Session"
  }

  geo_location {
    location          = azurerm_resource_group.main.location
    failover_priority = 0
  }

  tags = local.tags
}

resource "azurerm_cosmosdb_sql_database" "main" {
  name                = "bioarchive"
  resource_group_name = azurerm_resource_group.main.name
  account_name        = azurerm_cosmosdb_account.main.name
}

resource "azurerm_cosmosdb_sql_container" "media" {
  name                  = "media"
  resource_group_name   = azurerm_resource_group.main.name
  account_name          = azurerm_cosmosdb_account.main.name
  database_name         = azurerm_cosmosdb_sql_database.main.name
  partition_key_paths   = ["/owner_sub"]
  partition_key_version = 2

  indexing_policy {
    indexing_mode = "consistent"
    included_path { path = "/*" }
  }
}

resource "azurerm_cosmosdb_sql_container" "subscriptions" {
  name                  = "subscriptions"
  resource_group_name   = azurerm_resource_group.main.name
  account_name          = azurerm_cosmosdb_account.main.name
  database_name         = azurerm_cosmosdb_sql_database.main.name
  partition_key_paths   = ["/owner_sub"]
  partition_key_version = 2
}

resource "azurerm_cosmosdb_sql_container" "delivery_ledger" {
  name                  = "delivery-ledger"
  resource_group_name   = azurerm_resource_group.main.name
  account_name          = azurerm_cosmosdb_account.main.name
  database_name         = azurerm_cosmosdb_sql_database.main.name
  partition_key_paths   = ["/id"]
  partition_key_version = 2
}

resource "azurerm_cosmosdb_sql_container" "deletion_operations" {
  name                  = "deletion-operations"
  resource_group_name   = azurerm_resource_group.main.name
  account_name          = azurerm_cosmosdb_account.main.name
  database_name         = azurerm_cosmosdb_sql_database.main.name
  partition_key_paths   = ["/owner_sub"]
  partition_key_version = 2
}

resource "azurerm_linux_function_app" "data_api" {
  name                        = "${local.name}-data-${var.unique_suffix}"
  resource_group_name         = azurerm_resource_group.main.name
  location                    = azurerm_resource_group.main.location
  service_plan_id             = azurerm_service_plan.functions.id
  storage_account_name        = azurerm_storage_account.functions.name
  storage_account_access_key  = azurerm_storage_account.functions.primary_access_key
  https_only                  = true
  functions_extension_version = "~4"
  tags                        = local.tags

  identity {
    type = "SystemAssigned"
  }

  site_config {
    minimum_tls_version                    = "1.2"
    application_insights_connection_string = azurerm_application_insights.functions.connection_string
    application_stack {
      python_version = "3.12"
    }
    cors {
      allowed_origins = var.frontend_origins
    }
  }

  app_settings = {
    "FUNCTIONS_WORKER_RUNTIME"             = "python"
    "AzureWebJobsFeatureFlags"             = "EnableWorkerIndexing"
    "SCM_DO_BUILD_DURING_DEPLOYMENT"       = "true"
    "ENABLE_ORYX_BUILD"                    = "true"
    "COSMOS_ENDPOINT"                      = azurerm_cosmosdb_account.main.endpoint
    "COSMOS_DATABASE"                      = azurerm_cosmosdb_sql_database.main.name
    "COSMOS_MEDIA_CONTAINER"               = azurerm_cosmosdb_sql_container.media.name
    "COSMOS_SUBSCRIPTIONS_CONTAINER"       = azurerm_cosmosdb_sql_container.subscriptions.name
    "COSMOS_DELIVERY_LEDGER_CONTAINER"     = azurerm_cosmosdb_sql_container.delivery_ledger.name
    "COSMOS_DELETION_OPERATIONS_CONTAINER" = azurerm_cosmosdb_sql_container.deletion_operations.name
    "AWS_REGION"                           = "ap-southeast-2"
    "COGNITO_USER_POOL_ID"                 = split("/", var.cognito_issuer)[3]
    "COGNITO_ISSUER"                       = var.cognito_issuer
    "COGNITO_APP_CLIENT_ID"                = var.cognito_app_client_id
    "AWS_MEDIA_BUCKET"                     = var.aws_media_bucket
    "KEY_VAULT_URI"                        = azurerm_key_vault.main.vault_uri
  }
}

resource "azurerm_role_assignment" "function_storage_blob" {
  scope                = azurerm_storage_account.functions.id
  role_definition_name = "Storage Blob Data Contributor"
  principal_id         = azurerm_linux_function_app.data_api.identity[0].principal_id
}

resource "azurerm_role_assignment" "function_key_vault" {
  scope                = azurerm_key_vault.main.id
  role_definition_name = "Key Vault Secrets User"
  principal_id         = azurerm_linux_function_app.data_api.identity[0].principal_id
}

resource "azurerm_cosmosdb_sql_role_definition" "component_reader" {
  name                = "${local.name}-component-reader"
  resource_group_name = azurerm_resource_group.main.name
  account_name        = azurerm_cosmosdb_account.main.name
  type                = "CustomRole"
  assignable_scopes   = [azurerm_cosmosdb_account.main.id]

  permissions {
    data_actions = [
      "Microsoft.DocumentDB/databaseAccounts/readMetadata",
      "Microsoft.DocumentDB/databaseAccounts/sqlDatabases/containers/executeQuery",
      "Microsoft.DocumentDB/databaseAccounts/sqlDatabases/containers/items/read",
    ]
  }
}

resource "azurerm_cosmosdb_sql_role_definition" "component_writer" {
  name                = "${local.name}-component-writer"
  resource_group_name = azurerm_resource_group.main.name
  account_name        = azurerm_cosmosdb_account.main.name
  type                = "CustomRole"
  assignable_scopes   = [azurerm_cosmosdb_account.main.id]

  permissions {
    data_actions = [
      "Microsoft.DocumentDB/databaseAccounts/readMetadata",
      "Microsoft.DocumentDB/databaseAccounts/sqlDatabases/containers/executeQuery",
      "Microsoft.DocumentDB/databaseAccounts/sqlDatabases/containers/items/read",
      "Microsoft.DocumentDB/databaseAccounts/sqlDatabases/containers/items/create",
      "Microsoft.DocumentDB/databaseAccounts/sqlDatabases/containers/items/replace",
      "Microsoft.DocumentDB/databaseAccounts/sqlDatabases/containers/items/upsert",
      "Microsoft.DocumentDB/databaseAccounts/sqlDatabases/containers/items/delete",
    ]
  }
}

locals {
  cosmos_container_scopes = {
    media               = "${azurerm_cosmosdb_account.main.id}/dbs/${azurerm_cosmosdb_sql_database.main.name}/colls/${azurerm_cosmosdb_sql_container.media.name}"
    subscriptions       = "${azurerm_cosmosdb_account.main.id}/dbs/${azurerm_cosmosdb_sql_database.main.name}/colls/${azurerm_cosmosdb_sql_container.subscriptions.name}"
    delivery_ledger     = "${azurerm_cosmosdb_account.main.id}/dbs/${azurerm_cosmosdb_sql_database.main.name}/colls/${azurerm_cosmosdb_sql_container.delivery_ledger.name}"
    deletion_operations = "${azurerm_cosmosdb_account.main.id}/dbs/${azurerm_cosmosdb_sql_database.main.name}/colls/${azurerm_cosmosdb_sql_container.deletion_operations.name}"
  }
  api_cosmos_scopes = var.api_principal_id == null ? {} : local.cosmos_container_scopes
  notification_cosmos_scopes = var.notification_principal_id == null ? {} : {
    subscriptions   = local.cosmos_container_scopes.subscriptions
    delivery_ledger = local.cosmos_container_scopes.delivery_ledger
  }
}

resource "azurerm_cosmosdb_sql_role_assignment" "function_media_reader" {
  resource_group_name = azurerm_resource_group.main.name
  account_name        = azurerm_cosmosdb_account.main.name
  role_definition_id  = azurerm_cosmosdb_sql_role_definition.component_reader.id
  principal_id        = azurerm_linux_function_app.data_api.identity[0].principal_id
  scope               = local.cosmos_container_scopes.media
}

resource "azurerm_cosmosdb_sql_role_assignment" "api_data" {
  for_each            = local.api_cosmos_scopes
  resource_group_name = azurerm_resource_group.main.name
  account_name        = azurerm_cosmosdb_account.main.name
  role_definition_id  = azurerm_cosmosdb_sql_role_definition.component_writer.id
  principal_id        = var.api_principal_id
  scope               = each.value
}

resource "azurerm_cosmosdb_sql_role_assignment" "worker_media_data" {
  count               = var.worker_principal_id == null ? 0 : 1
  resource_group_name = azurerm_resource_group.main.name
  account_name        = azurerm_cosmosdb_account.main.name
  role_definition_id  = azurerm_cosmosdb_sql_role_definition.component_writer.id
  principal_id        = var.worker_principal_id
  scope               = local.cosmos_container_scopes.media
}

resource "azurerm_cosmosdb_sql_role_assignment" "notification_data" {
  for_each            = local.notification_cosmos_scopes
  resource_group_name = azurerm_resource_group.main.name
  account_name        = azurerm_cosmosdb_account.main.name
  role_definition_id  = azurerm_cosmosdb_sql_role_definition.component_writer.id
  principal_id        = var.notification_principal_id
  scope               = each.value
}
