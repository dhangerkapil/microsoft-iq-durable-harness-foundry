# ---------------------------------------------------------------------------
# Entra-only Foundry account and project. With existing_foundry_account_id the
# account (and its model deployments) come from elsewhere, for example another
# resource group, and only this deployment's project is created in it.
# ---------------------------------------------------------------------------
resource "azapi_resource" "ai_foundry" {
  count = local.create_foundry ? 1 : 0

  type                      = "Microsoft.CognitiveServices/accounts@2026-05-01"
  name                      = local.foundry_name
  parent_id                 = azurerm_resource_group.this.id
  location                  = azurerm_resource_group.this.location
  schema_validation_enabled = false
  tags                      = local.tags

  body = {
    kind     = "AIServices"
    sku      = { name = "S0" }
    identity = { type = "SystemAssigned" }
    properties = merge({
      disableLocalAuth       = true
      allowProjectManagement = true
      customSubDomainName    = local.foundry_name
      publicNetworkAccess    = "Enabled"
      networkAcls            = { defaultAction = "Allow" }
      }, jsondecode(var.foundry_managed_network ? jsonencode({
        networkInjections = [{
          scenario                   = "agent"
          subnetArmId                = ""
          useMicrosoftManagedNetwork = true
        }]
    }) : "{}"))
  }

  response_export_values = ["identity.principalId"]
}

# Deployments made before the account became optional keep their state.
moved {
  from = azapi_resource.ai_foundry
  to   = azapi_resource.ai_foundry[0]
}

data "azapi_resource" "existing_foundry" {
  count = local.create_foundry ? 0 : 1

  type                   = "Microsoft.CognitiveServices/accounts@2026-05-01"
  resource_id            = var.existing_foundry_account_id
  response_export_values = ["properties.customSubDomainName", "properties.allowProjectManagement", "kind"]
}

# The existing account's own connections, to find the Application Insights its projects trace to.
data "azapi_resource_list" "existing_foundry_connections" {
  count = local.create_foundry ? 0 : 1

  type      = "Microsoft.CognitiveServices/accounts/connections@2025-06-01"
  parent_id = var.existing_foundry_account_id
  response_export_values = {
    appinsights = "value[?properties.category == 'AppInsights'].properties.target"
  }
}

# Foundry sends the traces of every project in an account to the Application Insights connected to the account, so
# in an existing account this project's traces land in that account's Application Insights. Insights and trace
# evaluations read them as the project identity, which gets read access there. An account with no Application
# Insights yet gets this deployment's.
resource "azurerm_role_assignment" "project_account_appinsights_reader" {
  for_each = length(local.account_appinsights_ids) > 0 ? toset(["Log Analytics Reader", "Monitoring Data Reader", "Monitoring Reader"]) : toset([])

  scope                = local.account_appinsights_ids[0]
  role_definition_name = each.value
  principal_id         = azapi_resource.ai_foundry_project.output.identity.principalId
  principal_type       = "ServicePrincipal"
}

resource "azapi_resource" "existing_foundry_appinsights_connection" {
  count = !local.create_foundry && length(local.account_appinsights_ids) == 0 ? 1 : 0

  type                      = "Microsoft.CognitiveServices/accounts/connections@2025-06-01"
  name                      = "appi${local.connection_suffix}"
  parent_id                 = var.existing_foundry_account_id
  schema_validation_enabled = false
  retry                     = local.foundry_operation_retry

  body = {
    properties = {
      category      = "AppInsights"
      target        = azurerm_application_insights.this.id
      authType      = "ApiKey"
      isSharedToAll = true
      credentials   = { key = azurerm_application_insights.this.connection_string }
      metadata = {
        ApiType    = "Azure"
        ResourceId = azurerm_application_insights.this.id
      }
    }
  }
}

# An existing account must already have the models this deployment uses; a missing one stops the plan here.
data "azapi_resource" "existing_model_deployment" {
  for_each = local.create_foundry ? toset([]) : toset(compact(concat(
    [var.foundry_iq_enabled ? var.embedding_model : ""],
    keys(var.model_search_deployments),
  )))

  type        = "Microsoft.CognitiveServices/accounts/deployments@2024-10-01"
  resource_id = "${var.existing_foundry_account_id}/deployments/${each.value}"
}

resource "azurerm_role_assignment" "foundry_network_connection_approver" {
  for_each = var.foundry_managed_network && local.create_foundry ? {
    foundry = azapi_resource.ai_foundry[0].id
    cosmos  = azurerm_cosmosdb_account.this.id
  } : {}

  scope                = each.value
  role_definition_name = "Azure AI Enterprise Network Connection Approver"
  principal_id         = azapi_resource.ai_foundry[0].output.identity.principalId
  principal_type       = "ServicePrincipal"
}

# This settings API returns no ARM ID, so configure it with an explicit PUT.
resource "azapi_resource_action" "foundry_managed_network" {
  count = var.foundry_managed_network && local.create_foundry ? 1 : 0

  type        = "Microsoft.CognitiveServices/accounts/managedNetworks@2025-10-01-preview"
  resource_id = "${azapi_resource.ai_foundry[0].id}/managedNetworks/default"
  method      = "PUT"
  when        = "apply"
  retry       = local.foundry_operation_retry

  body = {
    properties = {
      managedNetwork = {
        isolationMode      = "AllowInternetOutbound"
        managedNetworkKind = "V2"
      }
    }
  }

  depends_on = [azurerm_role_assignment.foundry_network_connection_approver]
}

resource "azapi_resource" "foundry_cosmos_outbound_rule" {
  count = var.foundry_managed_network && local.create_foundry ? 1 : 0

  type                      = "Microsoft.CognitiveServices/accounts/managedNetworks/outboundRules@2025-10-01-preview"
  name                      = "cosmos-history"
  parent_id                 = azapi_resource_action.foundry_managed_network[0].resource_id
  schema_validation_enabled = false
  retry                     = local.foundry_operation_retry

  body = {
    properties = {
      type     = "PrivateEndpoint"
      category = "UserDefined"
      destination = {
        serviceResourceId = azurerm_cosmosdb_account.this.id
        subresourceTarget = "Sql"
      }
    }
  }
}

resource "azapi_resource" "ai_foundry_project" {
  type                      = "Microsoft.CognitiveServices/accounts/projects@2025-06-01"
  name                      = local.foundry_project
  parent_id                 = local.foundry_account_id
  location                  = local.foundry_location
  schema_validation_enabled = false
  retry                     = local.foundry_operation_retry

  body = {
    sku      = { name = "S0" }
    identity = { type = "SystemAssigned" }
    properties = {
      displayName = "Advisor Team"
      description = "Investment Advisor multi-agent team: one hosted agent, a team of Agent Framework agents and durable workflows, grounded in Work IQ, Fabric IQ, Foundry IQ and Web IQ."
    }
  }

  response_export_values = ["identity.principalId"]

  depends_on = [azapi_resource.foundry_cosmos_outbound_rule]

  lifecycle {
    precondition {
      condition     = local.create_foundry || try(data.azapi_resource.existing_foundry[0].output.properties.allowProjectManagement, false) == true
      error_message = "The existing Foundry account must allow project management (a Foundry resource of kind AIServices with projects)."
    }
    precondition {
      condition     = local.create_foundry || !var.foundry_managed_network
      error_message = "foundry_managed_network applies only to a Foundry account this deployment creates; set it to false with existing_foundry_account_id."
    }
  }
}

# ---------------------------------------------------------------------------
# Blocking guardrail and chat model. In an existing account the guardrail gets
# this deployment's own name, so it never replaces another deployment's.
# ---------------------------------------------------------------------------
resource "azapi_resource" "rai_policy" {
  type                      = "Microsoft.CognitiveServices/accounts/raiPolicies@2025-06-01"
  name                      = local.create_foundry ? "team-guardrail" : "team-guardrail-${local.suffix}"
  parent_id                 = local.foundry_account_id
  schema_validation_enabled = false
  retry                     = local.foundry_operation_retry

  body = {
    properties = {
      basePolicyName = "Microsoft.Default"
      mode           = "Blocking"
      contentFilters = flatten([
        for source in ["Prompt", "Completion"] : [
          for name in ["Hate", "Sexual", "Violence", "Selfharm"] : {
            name              = name
            source            = source
            enabled           = true
            blocking          = true
            severityThreshold = "Medium"
          }
        ]
      ])
    }
  }

  # Cognitive Services serializes mutations across an account and its children.
  depends_on = [azapi_resource.ai_foundry_project]

  lifecycle {
    # The service adds Defender for AI filters after creation; keep them instead of
    # removing them on every apply.
    ignore_changes = [body]
  }
}

# Replaced by the team deployments (team_models.tf); kept at zero so older state moves cleanly.
resource "azapi_resource" "chat_deployment" {
  count = 0

  type                      = "Microsoft.CognitiveServices/accounts/deployments@2024-10-01"
  name                      = var.chat_model
  parent_id                 = azapi_resource.ai_foundry[0].id
  schema_validation_enabled = false
  retry                     = local.foundry_operation_retry

  body = {
    sku = {
      name     = "GlobalStandard"
      capacity = var.chat_model_capacity
    }
    properties = {
      model = {
        format  = "OpenAI"
        name    = var.chat_model
        version = var.chat_model_version
      }
      versionUpgradeOption = "OnceNewDefaultVersionAvailable"
      raiPolicyName        = azapi_resource.rai_policy.name
    }
  }
}

moved {
  from = azapi_resource.chat_deployment
  to   = azapi_resource.chat_deployment[0]
}

# The agent optimizer generates candidate instructions with a gpt-5.x model; the chat model judges them.
# The optimizer uses team-judge (gpt-5.5, team_models.tf).
resource "azapi_resource" "optimizer_deployment" {
  count                     = 0
  type                      = "Microsoft.CognitiveServices/accounts/deployments@2024-10-01"
  name                      = var.optimizer_model
  parent_id                 = azapi_resource.ai_foundry[0].id
  schema_validation_enabled = false
  retry                     = local.foundry_operation_retry

  body = {
    sku = {
      name     = "GlobalStandard"
      capacity = var.optimizer_model_capacity
    }
    properties = {
      model = {
        format  = "OpenAI"
        name    = var.optimizer_model
        version = var.optimizer_model_version
      }
      versionUpgradeOption = "OnceNewDefaultVersionAvailable"
      raiPolicyName        = azapi_resource.rai_policy.name
    }
  }

  # One deployment operation at a time per account.
  depends_on = [azapi_resource.chat_deployment]
}

# Extra chat models for the agent optimizer's model selection (model_search_space): the optimizer evaluates the agent
# on each and reports quality against token cost. Empty by default.
resource "azapi_resource" "model_search_deployment" {
  for_each                  = local.create_foundry ? var.model_search_deployments : {}
  type                      = "Microsoft.CognitiveServices/accounts/deployments@2024-10-01"
  name                      = each.key
  parent_id                 = azapi_resource.ai_foundry[0].id
  schema_validation_enabled = false
  retry                     = local.foundry_operation_retry

  body = {
    sku = {
      name     = "GlobalStandard"
      capacity = each.value.capacity
    }
    properties = {
      model = {
        format  = "OpenAI"
        name    = each.key
        version = each.value.version
      }
      versionUpgradeOption = "OnceNewDefaultVersionAvailable"
      raiPolicyName        = azapi_resource.rai_policy.name
    }
  }

  depends_on = [azapi_resource.chat_deployment, azapi_resource.optimizer_deployment]
}

# ---------------------------------------------------------------------------
# Project managed-identity image pulls
# ---------------------------------------------------------------------------
resource "azapi_resource" "registry_connection" {
  type                      = "Microsoft.CognitiveServices/accounts/projects/connections@2025-06-01"
  name                      = "agent-images${local.connection_suffix}"
  parent_id                 = azapi_resource.ai_foundry_project.id
  schema_validation_enabled = false
  retry                     = local.foundry_operation_retry

  body = {
    properties = {
      category      = "ContainerRegistry"
      target        = azurerm_container_registry.this.login_server
      authType      = "ManagedIdentity"
      isSharedToAll = false
      credentials = {
        clientId   = azapi_resource.ai_foundry_project.output.identity.principalId
        resourceId = azurerm_container_registry.this.id
      }
      metadata = {
        ApiType    = "Azure"
        ResourceId = azurerm_container_registry.this.id
      }
    }
  }

  depends_on = [
    azurerm_role_assignment.project_acr_pull,
    azapi_resource.project_appinsights_connection,
  ]
}

# ---------------------------------------------------------------------------
# Foundry portal observability connections (the account-level one only for an
# account this deployment owns; the project-level one always)
# ---------------------------------------------------------------------------
resource "azapi_resource" "foundry_appinsights_connection" {
  count = local.create_foundry ? 1 : 0

  type                      = "Microsoft.CognitiveServices/accounts/connections@2025-06-01"
  name                      = "${local.foundry_name}-appinsights"
  parent_id                 = azapi_resource.ai_foundry[0].id
  schema_validation_enabled = false
  retry                     = local.foundry_operation_retry

  body = {
    properties = {
      category      = "AppInsights"
      target        = azurerm_application_insights.this.id
      authType      = "ApiKey"
      isSharedToAll = true
      credentials   = { key = azurerm_application_insights.this.connection_string }
      metadata = {
        ApiType    = "Azure"
        ResourceId = azurerm_application_insights.this.id
      }
    }
  }

  depends_on = [azapi_resource.chat_deployment]
}

moved {
  from = azapi_resource.foundry_appinsights_connection
  to   = azapi_resource.foundry_appinsights_connection[0]
}

resource "azapi_resource" "project_appinsights_connection" {
  type                      = "Microsoft.CognitiveServices/accounts/projects/connections@2025-06-01"
  name                      = "appi-connection${local.connection_suffix}"
  parent_id                 = azapi_resource.ai_foundry_project.id
  schema_validation_enabled = false
  retry                     = local.foundry_operation_retry

  body = {
    properties = {
      category      = "AppInsights"
      target        = azurerm_application_insights.this.id
      authType      = "ApiKey"
      isSharedToAll = false
      credentials   = { key = azurerm_application_insights.this.connection_string }
      metadata = {
        ApiType    = "Azure"
        ResourceId = azurerm_application_insights.this.id
      }
    }
  }

  depends_on = [azapi_resource.foundry_appinsights_connection]
}
