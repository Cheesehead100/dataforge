resource "azurerm_data_factory" "main" {
  name                   = "adf-${local.prefix}-001"
  resource_group_name    = azurerm_resource_group.main.name
  location               = azurerm_resource_group.main.location
  tags                   = local.tags
  public_network_enabled = false

  identity {
    type = "SystemAssigned"
  }
}

resource "azurerm_private_endpoint" "adf" {
  name                = "pe-${local.prefix}-adf"
  resource_group_name = azurerm_resource_group.main.name
  location            = azurerm_resource_group.main.location
  subnet_id           = azurerm_subnet.private_endpoints.id
  tags                = local.tags

  private_service_connection {
    name                           = "psc-adf"
    private_connection_resource_id = azurerm_data_factory.main.id
    subresource_names              = ["dataFactory"]
    is_manual_connection           = false
  }
}
