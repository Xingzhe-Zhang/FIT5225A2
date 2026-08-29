output "resource_group_name" {
  value = azurerm_resource_group.main.name
}

output "function_app_hostname" {
  value = azurerm_linux_function_app.data_api.default_hostname
}

output "function_app_url" {
  value = "https://${azurerm_linux_function_app.data_api.default_hostname}"
}

output "cosmos_endpoint" {
  value = azurerm_cosmosdb_account.main.endpoint
}

output "key_vault_uri" {
  value = azurerm_key_vault.main.vault_uri
}
