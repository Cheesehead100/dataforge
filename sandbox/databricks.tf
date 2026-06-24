resource "azurerm_databricks_workspace" "main" {
  name                          = "dbw-${local.prefix}-001"
  resource_group_name           = azurerm_resource_group.main.name
  location                      = azurerm_resource_group.main.location
  sku                           = "premium"
  tags                          = local.tags

  public_network_access_enabled         = false
  network_security_group_rules_required = "NoAzureDatabricksRules"

  custom_parameters {
    no_public_ip        = true
    virtual_network_id  = azurerm_virtual_network.main.id
    private_subnet_name = azurerm_subnet.dbw_private.name
    public_subnet_name  = azurerm_subnet.dbw_public.name

    private_subnet_network_security_group_association_id = azurerm_subnet_network_security_group_association.dbw_private.id
    public_subnet_network_security_group_association_id  = azurerm_subnet_network_security_group_association.dbw_public.id
  }
}
