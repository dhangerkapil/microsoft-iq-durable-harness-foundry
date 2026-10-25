output "resource_group_name" {
  value = azurerm_resource_group.this.name
}

output "fabric_capacity_name" {
  value = (local.create_fabric_capacity ? azurerm_fabric_capacity.this[0].name :
  var.existing_fabric_capacity_id != "" ? element(split("/", var.existing_fabric_capacity_id), 8) : null)
}

output "fabric_capacity_id" {
  value = (local.create_fabric_capacity ? azurerm_fabric_capacity.this[0].id :
  var.existing_fabric_capacity_id != "" ? var.existing_fabric_capacity_id : null)
}

output "fabric_capacity_managed" {
  description = "Whether this deployment created the Fabric capacity (false: an existing capacity, or none)."
  value       = local.create_fabric_capacity
}

output "subscription_id" {
  value     = var.subscription_id
  sensitive = true
}

output "foundry_project_endpoint" {
  value = local.foundry_endpoint
}

output "chat_model_deployment" {
  description = "The lead's deployment (team-lead)."
  value       = var.team_lead_deployment
}

output "optimizer_model_deployment" {
  description = "Deployment the agent optimizer uses to generate candidates, or empty when not deployed."
  value       = var.team_judge_deployment
}

output "foundry_iq_search_endpoint" {
  value = var.foundry_iq_enabled ? "https://${element(split("/", local.search_id), 8)}.search.windows.net" : null
}

output "embedding_model_deployment" {
  value = var.foundry_iq_enabled ? var.embedding_model : null
}

output "foundry_account_endpoint" {
  description = "The Foundry account's Azure OpenAI endpoint (knowledge source embeddings and query planning)."
  value       = "https://${local.foundry_subdomain}.openai.azure.com"
}

output "foundry_account_id" {
  value = local.foundry_account_id
}

output "foundry_account_managed" {
  description = "Whether this deployment created the Foundry account (false: an existing account, which teardown keeps)."
  value       = local.create_foundry
}

output "connection_suffix" {
  description = "Suffix for this deployment's project connection names: empty for an account it created, else unique in the shared account."
  value       = local.connection_suffix
}

output "trace_application_insights_id" {
  description = "The Application Insights that receives the agent's traces: Foundry traces every project to the account's."
  value       = length(local.account_appinsights_ids) > 0 ? local.account_appinsights_ids[0] : azurerm_application_insights.this.id
}

output "model_search_deployments" {
  description = "Extra chat model deployments for the optimizer's model selection (comma-separated)."
  value       = join(",", keys(var.model_search_deployments))
}

output "registry_name" {
  value = azurerm_container_registry.this.name
}

output "registry_login_server" {
  value = azurerm_container_registry.this.login_server
}

output "rai_policy_id" {
  value = azapi_resource.rai_policy.id
}

output "agent_name" {
  value = var.agent_name
}

output "agent_version" {
  description = "Latest hosted-agent version, or null before the first release."
  value       = local.deploy_agent ? azapi_data_plane_resource.agent[0].output.versions.latest.version : null
}

output "tenant_id" {
  value = data.azurerm_client_config.current.tenant_id
}

output "foundry_project_id" {
  value = azapi_resource.ai_foundry_project.id
}

output "workiq_toolbox_endpoint" {
  value = local.toolbox_endpoint
}

output "workiq_toolbox_name" {
  value = var.workiq_toolbox_name
}

output "workiq_toolbox_client_id" {
  value = azuread_application.toolbox.client_id
}

output "workiq_toolbox_application_object_id" {
  value = azuread_application.toolbox.object_id
}

output "workiq_connection_kind" {
  value = var.workiq_connection_kind
}

output "workiq_connection_id" {
  description = "Work IQ project connection used by the toolbox (A2A connections are created by scripts/Set-WorkIqConnection.ps1)."
  value = (var.workiq_connection_kind == "mcp" ? azapi_resource.workiq_mcp[0].id :
  "${azapi_resource.ai_foundry_project.id}/connections/${var.workiq_a2a_connection_name}${local.connection_suffix}")
}

output "workiq_mcp_connection_id" {
  value = try(azapi_resource.workiq_mcp[0].id, null)
}

output "workiq_mcp_endpoint" {
  value = local.workiq_mcp_endpoint
}

output "workiq_mcp_allowed_tools" {
  value = var.workiq_mcp_allowed_tools
}

output "workiq_mcp_require_approval" {
  value = var.workiq_mcp_require_approval
}

output "cosmos_endpoint" {
  value = azurerm_cosmosdb_account.this.endpoint
}

output "agent_principal_id" {
  value = local.deploy_agent ? azapi_data_plane_resource.agent[0].output.instance_identity.principal_id : null
}

output "ui_url" {
  value = local.ui_url
}

output "ui_api_client_id" {
  value = local.deploy_ui ? azuread_application.ui_api[0].client_id : null
}

output "ui_browser_client_id" {
  value = local.deploy_ui ? azuread_application.ui_browser[0].client_id : null
}

output "ui_app_name" {
  value = local.deploy_ui ? azurerm_container_app.ui[0].name : null
}

output "search_service_id" {
  value = local.search_id
}

output "search_service_managed" {
  description = "Whether this deployment created the Search service (false: an existing one, which teardown keeps)."
  value       = local.create_search
}

output "name_suffix" {
  value = local.suffix
}

output "team_deployments" {
  value = {
    lead       = var.team_lead_deployment
    specialist = var.team_specialist_deployment
    judge      = var.team_judge_deployment
  }
}

output "specialist_endpoint" {
  description = "Direct endpoint of the specialist deployment's own account, or empty when it is in the main account."
  value       = local.specialist_endpoint
}

output "specialist_account_id" {
  value = try(azapi_resource.specialist_account[0].id, null)
}

# Principals this deployment created. The inventory diff approves role assignments and grants only for these.
output "principals" {
  value = {
    project            = azapi_resource.ai_foundry_project.output.identity.principalId
    agent              = local.deploy_agent ? azapi_data_plane_resource.agent[0].output.instance_identity.principal_id : ""
    morning_note       = local.morning_note ? azurerm_user_assigned_identity.morning_note[0].principal_id : ""
    toolbox            = azuread_service_principal.toolbox.object_id
    ui_identity        = local.deploy_ui ? azurerm_user_assigned_identity.ui[0].principal_id : ""
    ui_api             = local.deploy_ui ? azuread_service_principal.ui_api[0].object_id : ""
    ui_browser         = local.deploy_ui ? azuread_service_principal.ui_browser[0].object_id : ""
    specialist_account = try(azapi_resource.specialist_account[0].output.identity.principalId, "")
  }
}

output "morning_note_function_name" {
  value = local.morning_note ? azurerm_function_app_flex_consumption.morning_note[0].name : null
}

output "morning_note_url" {
  value = local.morning_note ? "https://${azurerm_function_app_flex_consumption.morning_note[0].default_hostname}" : null
}

output "durable_task_scheduler_endpoint" {
  value = local.morning_note ? azapi_resource.dts_scheduler[0].output.properties.endpoint : null
}
