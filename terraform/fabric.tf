# ---------------------------------------------------------------------------
# Fabric capacity for the portfolio lakehouse and the Fabric data agent.
# The data agent requires a paid F2 or larger capacity in the same region as
# its data sources. Pause it when idle: scripts/Set-FabricCapacityState.ps1.
# With existing_fabric_capacity_id the workspace goes on that capacity instead.
# ---------------------------------------------------------------------------
data "azuread_user" "current" {
  object_id = data.azurerm_client_config.current.object_id
}

resource "azurerm_fabric_capacity" "this" {
  count = local.create_fabric_capacity ? 1 : 0

  name                   = "fab${local.func_name}"
  resource_group_name    = azurerm_resource_group.this.name
  location               = var.fabric_location != "" ? var.fabric_location : azurerm_resource_group.this.location
  administration_members = [data.azuread_user.current.user_principal_name]
  tags                   = local.tags

  sku {
    name = var.fabric_capacity_sku
    tier = "Fabric"
  }

  lifecycle {
    # Subscription governance policies may add tags; do not fight them.
    ignore_changes = [tags]
  }
}
