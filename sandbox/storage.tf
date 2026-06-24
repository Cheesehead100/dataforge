resource "azurerm_storage_account" "main" {
  name                     = "st${replace(local.prefix, "-", "")}001"
  resource_group_name      = azurerm_resource_group.main.name
  location                 = azurerm_resource_group.main.location
  account_tier             = "Standard"
  account_replication_type = "LRS"   # sandbox: LRS is cheapest
  account_kind             = "StorageV2"
  is_hns_enabled           = true    # ADLS Gen2
  tags                     = local.tags

  public_network_access_enabled     = false
  allow_nested_items_to_be_public   = false
  infrastructure_encryption_enabled = true
  min_tls_version                   = "TLS1_2"

  blob_properties {
    versioning_enabled  = false  # sandbox: save cost
    change_feed_enabled = false
  }

  network_rules {
    default_action = "Deny"
    bypass         = ["AzureServices"]
  }
}

resource "azurerm_storage_data_lake_gen2_filesystem" "raw" {
  name               = "raw"
  storage_account_id = azurerm_storage_account.main.id
}

resource "azurerm_storage_data_lake_gen2_filesystem" "curated" {
  name               = "curated"
  storage_account_id = azurerm_storage_account.main.id
}

# Private endpoint for ADLS so ADF/Databricks can reach it
resource "azurerm_private_endpoint" "storage_blob" {
  name                = "pe-${local.prefix}-storage-blob"
  resource_group_name = azurerm_resource_group.main.name
  location            = azurerm_resource_group.main.location
  subnet_id           = azurerm_subnet.private_endpoints.id
  tags                = local.tags

  private_service_connection {
    name                           = "psc-storage-blob"
    private_connection_resource_id = azurerm_storage_account.main.id
    subresource_names              = ["blob"]
    is_manual_connection           = false
  }
}

resource "azurerm_private_endpoint" "storage_dfs" {
  name                = "pe-${local.prefix}-storage-dfs"
  resource_group_name = azurerm_resource_group.main.name
  location            = azurerm_resource_group.main.location
  subnet_id           = azurerm_subnet.private_endpoints.id
  tags                = local.tags

  private_service_connection {
    name                           = "psc-storage-dfs"
    private_connection_resource_id = azurerm_storage_account.main.id
    subresource_names              = ["dfs"]
    is_manual_connection           = false
  }
}
