resource "azurerm_key_vault" "main" {
  name                          = "kv-${local.prefix}-001"
  resource_group_name           = azurerm_resource_group.main.name
  location                      = azurerm_resource_group.main.location
  tenant_id                     = data.azurerm_client_config.current.tenant_id
  sku_name                      = "standard"
  tags                          = local.tags

  enable_rbac_authorization     = true
  purge_protection_enabled      = false  # sandbox: allow clean destroy
  soft_delete_retention_days    = 7      # sandbox: minimum retention
  public_network_access_enabled = false
}

resource "azurerm_private_endpoint" "key_vault" {
  name                = "pe-${local.prefix}-kv"
  resource_group_name = azurerm_resource_group.main.name
  location            = azurerm_resource_group.main.location
  subnet_id           = azurerm_subnet.private_endpoints.id
  tags                = local.tags

  private_service_connection {
    name                           = "psc-kv"
    private_connection_resource_id = azurerm_key_vault.main.id
    subresource_names              = ["vault"]
    is_manual_connection           = false
  }
}
