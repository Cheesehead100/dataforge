output "resource_group_name" {
  value = azurerm_resource_group.main.name
}

output "adf_id" {
  value       = azurerm_data_factory.main.id
  description = "Resource ID of Azure Data Factory"
}

output "adf_managed_identity_principal_id" {
  value       = azurerm_data_factory.main.identity[0].principal_id
  description = "ADF managed identity principal ID — used to verify RBAC in portal"
}

output "databricks_workspace_id" {
  value       = azurerm_databricks_workspace.main.id
  description = "Resource ID of Databricks workspace"
}

output "databricks_workspace_url" {
  value       = "https://${azurerm_databricks_workspace.main.workspace_url}"
  description = "URL to open the Databricks workspace"
}

output "adls_id" {
  value       = azurerm_storage_account.main.id
  description = "Resource ID of ADLS Gen2 storage account"
}

output "adls_dfs_endpoint" {
  value       = azurerm_storage_account.main.primary_dfs_endpoint
  description = "DFS endpoint for ADLS Gen2 (use in Databricks mount)"
}

output "key_vault_uri" {
  value       = azurerm_key_vault.main.vault_uri
  description = "Key Vault URI"
}

output "vnet_id" {
  value       = azurerm_virtual_network.main.id
  description = "VNet ID"
}

output "cost_estimate" {
  value = <<-EOT
    Estimated monthly cost (idle, no clusters running):
      ADF:             ~$0/month (no pipeline runs)
      Databricks:      ~$0/month (workspace only, no clusters)
      ADLS Gen2:       ~$0.02/month (minimal data)
      Key Vault:       ~$0.03/month
      Private endpoints: ~$0.36/each/month (~$1.80/month for 5)
      VNet/subnets:    Free
      Total idle:      ~$2/month

    Run `terraform destroy` when done to stop all charges.
  EOT
}
