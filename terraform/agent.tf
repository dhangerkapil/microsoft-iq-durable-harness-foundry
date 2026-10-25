# ---------------------------------------------------------------------------
# Hosted agent release (skipped until the image is published)
# ---------------------------------------------------------------------------
locals {
  # The hosted agent's environment (TEAM_MODE=team: the lead, its specialist agents and the review-packs steps).
  agent_environment = merge({
    AZURE_AI_PROJECT_ENDPOINT      = local.foundry_endpoint
    AZURE_AI_MODEL_DEPLOYMENT_NAME = var.team_lead_deployment
    TEAM_MODE                      = var.team_mode
    OPTIMIZATION_LOCAL_DIR         = "/app/.agent_configs/${var.team_mode}"
    TEAM_LEAD_DEPLOYMENT           = var.team_lead_deployment
    TEAM_SPECIALIST_DEPLOYMENT     = var.team_specialist_deployment
    TEAM_SPECIALIST_ENDPOINT       = local.specialist_endpoint
    TEAM_JUDGE_DEPLOYMENT          = var.team_judge_deployment
    ADVISOR_STORE_PREFIX           = "team"
    AGENTSERVER_STATE_ROOT         = "/tmp/agentserver"
    AZURE_TENANT_ID                = data.azurerm_client_config.current.tenant_id
    TOOLBOX_ENDPOINT               = local.toolbox_endpoint
    COSMOS_ENDPOINT                = azurerm_cosmosdb_account.this.endpoint
    COSMOS_DATABASE                = azurerm_cosmosdb_sql_database.this.name
    COSMOS_HISTORY_CONTAINER       = azurerm_cosmosdb_sql_container.history.name
    ENABLE_SENSITIVE_DATA          = var.agent_capture_content ? "true" : "false"
    WORKIQ_MODE                    = var.workiq_tool_mode == "" ? var.workiq_connection_kind : var.workiq_tool_mode
    ADVISOR_EVIDENCE_LOOP          = var.advisor_evidence_loop ? "on" : "off"
    DEMO_FAULTS                    = var.demo_faults
  }, local.optional_toolbox_environment, local.addon_environment, var.agent_extra_environment)
}

resource "azapi_data_plane_resource" "agent" {
  count = local.deploy_agent ? 1 : 0

  type      = "Microsoft.Foundry/agents@v1"
  name      = var.agent_name
  parent_id = trimprefix(local.foundry_endpoint, "https://")

  body = {
    name = var.agent_name
    definition = {
      kind = "hosted"
      container_configuration = {
        image = var.agent_image_uri
      }
      cpu    = var.agent_cpu
      memory = var.agent_memory
      protocol_versions = [{
        protocol = "responses"
        version  = "2.0.0"
      }]
      environment_variables = local.agent_environment
      rai_config = {
        rai_policy_name = azapi_resource.rai_policy.id
      }
    }
  }

  response_export_values = ["versions.latest.version", "instance_identity.principal_id", "instance_identity.client_id"]

  depends_on = [
    azapi_resource.ai_foundry_project,
    azapi_resource.team_deployment,
    azapi_resource.specialist_deployment,
    azapi_resource.registry_connection,
    azapi_resource.foundry_cosmos_outbound_rule,
    azurerm_role_assignment.project_foundry_user,
    azurerm_role_assignment.current_user_project_manager,
    azurerm_role_assignment.current_user_foundry_user,
    azuread_service_principal.toolbox,
    azapi_resource.workiq_mcp,
  ]
}

# Agent 365 observability: when the platform enables it for a hosted agent, the agent exports its traces to
# Microsoft Agent 365 as its own Entra agent identity, which needs the OtelWrite app role. Skipped in tenants
# without the Agent365Observability service principal.
data "azuread_service_principals" "agent365_observability" {
  display_names  = ["Agent365Observability"]
  ignore_missing = true
}

resource "azuread_app_role_assignment" "agent365_observability" {
  count = local.deploy_agent && length(data.azuread_service_principals.agent365_observability.object_ids) > 0 ? 1 : 0

  app_role_id         = "8f71190c-00c8-461d-a63b-f74abde9ba52" # Agent365.Observability.OtelWrite (same in every tenant)
  principal_object_id = azapi_data_plane_resource.agent[0].output.instance_identity.principal_id
  resource_object_id  = data.azuread_service_principals.agent365_observability.object_ids[0]

  lifecycle {
    # The agent identity is stable across versions, but it reads as unknown while a new version is planned,
    # which would otherwise replace this grant on every release.
    ignore_changes = [principal_object_id]
  }
}
