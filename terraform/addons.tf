# ---------------------------------------------------------------------------
# Add-ons in the lead: what-if analyst (O3, on by default), investment committee (O4, experimental),
# Microsoft Purview on the lead's turns (O2). Each is a toggle in the agent's environment.
# ---------------------------------------------------------------------------
locals {
  addon_environment = merge(
    { TEAM_WHAT_IF = var.enable_what_if ? "on" : "off" },
    { TEAM_COMMITTEE = var.enable_committee ? "on" : "off" },
    var.enable_purview ? { TEAM_PURVIEW = "on" } : {},
  )
  purview_graph_roles = ["ProtectionScopes.Compute.All", "Content.Process.All", "ContentActivity.Write"]
}

data "azuread_service_principal" "microsoft_graph" {
  count     = var.enable_purview ? 1 : 0
  client_id = "00000003-0000-0000-c000-000000000000"
}

# Purview's APIs for AI apps, called by the agent's own identity on behalf of each advisor's activity.
resource "azuread_app_role_assignment" "agent_purview" {
  for_each = local.deploy_agent && var.enable_purview ? toset(local.purview_graph_roles) : toset([])

  app_role_id         = data.azuread_service_principal.microsoft_graph[0].app_role_ids[each.value]
  principal_object_id = azapi_data_plane_resource.agent[0].output.instance_identity.principal_id
  resource_object_id  = data.azuread_service_principal.microsoft_graph[0].object_id

  lifecycle {
    ignore_changes = [principal_object_id]
  }
}
