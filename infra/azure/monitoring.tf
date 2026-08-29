resource "azurerm_monitor_action_group" "operations" {
  name                = "${local.name}-operations"
  resource_group_name = azurerm_resource_group.main.name
  short_name          = "pba-ops"
  tags                = local.tags
}

resource "azurerm_monitor_metric_alert" "function_http_5xx" {
  name                = "${local.name}-function-http-5xx"
  resource_group_name = azurerm_resource_group.main.name
  scopes              = [azurerm_linux_function_app.data_api.id]
  description         = "Azure data API returned an HTTP 5xx response."
  severity            = 1
  frequency           = "PT5M"
  window_size         = "PT5M"

  criteria {
    metric_namespace = "Microsoft.Web/sites"
    metric_name      = "Http5xx"
    aggregation      = "Total"
    operator         = "GreaterThanOrEqual"
    threshold        = 1
  }

  action {
    action_group_id = azurerm_monitor_action_group.operations.id
  }

  tags = local.tags
}

resource "azurerm_monitor_metric_alert" "cosmos_throttling" {
  name                = "${local.name}-cosmos-throttling"
  resource_group_name = azurerm_resource_group.main.name
  scopes              = [azurerm_cosmosdb_account.main.id]
  description         = "Cosmos DB requests are being throttled."
  severity            = 2
  frequency           = "PT5M"
  window_size         = "PT5M"

  criteria {
    metric_namespace = "Microsoft.DocumentDB/databaseAccounts"
    metric_name      = "TotalRequests"
    # TotalRequests with a StatusCode dimension only supports Count in Azure Monitor.
    aggregation = "Count"
    operator    = "GreaterThanOrEqual"
    threshold   = 1

    dimension {
      name     = "StatusCode"
      operator = "Include"
      values   = ["429"]
    }
  }

  action {
    action_group_id = azurerm_monitor_action_group.operations.id
  }

  tags = local.tags
}

resource "azurerm_portal_dashboard" "operations" {
  name                = "${local.name}-operations"
  resource_group_name = azurerm_resource_group.main.name
  location            = azurerm_resource_group.main.location
  tags                = local.tags

  dashboard_properties = jsonencode({
    lenses = {
      main = {
        order = 0
        parts = {
          application = {
            position = { x = 0, y = 0, rowSpan = 4, colSpan = 6 }
            metadata = {
              type = "Extension/AppInsightsExtension/PartType/AppMapGalPt"
              inputs = [{
                name  = "ComponentId"
                value = azurerm_application_insights.functions.id
              }]
            }
          }
          logs = {
            position = { x = 6, y = 0, rowSpan = 4, colSpan = 6 }
            metadata = {
              type = "Extension/Microsoft_OperationsManagementSuite_Workspace/PartType/LogsDashboardPart"
              inputs = [{
                name  = "resourceTypeMode"
                value = "workspace"
                }, {
                name  = "ComponentId"
                value = azurerm_log_analytics_workspace.main.id
              }]
            }
          }
        }
      }
    }
    metadata = { model = {} }
  })
}

output "operations_action_group_id" {
  value = azurerm_monitor_action_group.operations.id
}

output "operations_dashboard_id" {
  value = azurerm_portal_dashboard.operations.id
}

output "application_insights_app_id" {
  value = azurerm_application_insights.functions.app_id
}
