# ---------------------------------------------------------------------------
# The team's models. One deployment per role, with this
# solution's own names, so the team never shares another workload's rate limits:
#   team-lead        the lead advisor, the writer and the compliance reviewer
#   team-specialist  the four IQ specialists (they run in parallel, so they get their own deployment; with
#                    specialist_location set it lives in a separate account in that region, for quota)
#   team-judge       evaluation judges and the agent optimizer
# In an existing account these are additions next to its own deployments; nothing existing is touched.
# ---------------------------------------------------------------------------
locals {
  team_deployments = {
    (var.team_lead_deployment) = {
      model = var.team_lead_model, version = var.team_lead_model_version, sku = var.team_lead_sku, capacity = var.team_lead_capacity
    }
    (var.team_judge_deployment) = {
      model = var.team_judge_model, version = var.team_judge_model_version, sku = var.team_judge_sku, capacity = var.team_judge_capacity
    }
  }
  separate_specialist_account = var.specialist_location != ""
  specialist_account_name     = "aisp${local.func_name}"
  # The specialists call their deployment directly when it lives in another account; otherwise through the project.
  specialist_endpoint = local.separate_specialist_account ? "https://${local.specialist_account_name}.openai.azure.com/openai/v1/" : ""
}

resource "azapi_resource" "team_deployment" {
  for_each = local.team_deployments

  type                      = "Microsoft.CognitiveServices/accounts/deployments@2024-10-01"
  name                      = each.key
  parent_id                 = local.foundry_account_id
  schema_validation_enabled = false
  retry                     = local.foundry_operation_retry

  body = {
    sku = {
      name     = each.value.sku
      capacity = each.value.capacity
    }
    properties = {
      model = {
        format  = "OpenAI"
        name    = each.value.model
        version = each.value.version
      }
      versionUpgradeOption = "OnceNewDefaultVersionAvailable"
      raiPolicyName        = azapi_resource.rai_policy.name
    }
  }

  # Cognitive Services runs one deployment operation at a time per account.
  depends_on = [azapi_resource.ai_foundry_project, azapi_resource.rai_policy]
}

resource "azapi_resource" "specialist_account" {
  count = local.separate_specialist_account ? 1 : 0

  type                      = "Microsoft.CognitiveServices/accounts@2026-05-01"
  name                      = local.specialist_account_name
  parent_id                 = azurerm_resource_group.this.id
  location                  = var.specialist_location
  schema_validation_enabled = false
  tags                      = local.tags

  body = {
    kind     = "AIServices"
    sku      = { name = "S0" }
    identity = { type = "SystemAssigned" }
    properties = {
      disableLocalAuth    = true
      customSubDomainName = local.specialist_account_name
      publicNetworkAccess = "Enabled"
      networkAcls         = { defaultAction = "Allow" }
    }
  }

  response_export_values = ["identity.principalId"]
}

resource "azapi_resource" "specialist_rai_policy" {
  count = local.separate_specialist_account ? 1 : 0

  type                      = "Microsoft.CognitiveServices/accounts/raiPolicies@2025-06-01"
  name                      = "team-guardrail"
  parent_id                 = azapi_resource.specialist_account[0].id
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

  lifecycle {
    ignore_changes = [body]
  }
}

resource "azapi_resource" "specialist_deployment" {
  type                      = "Microsoft.CognitiveServices/accounts/deployments@2024-10-01"
  name                      = var.team_specialist_deployment
  parent_id                 = local.separate_specialist_account ? azapi_resource.specialist_account[0].id : local.foundry_account_id
  schema_validation_enabled = false
  retry                     = local.foundry_operation_retry

  body = {
    sku = {
      name     = var.team_specialist_sku
      capacity = var.team_specialist_capacity
    }
    properties = {
      model = {
        format  = "OpenAI"
        name    = var.team_specialist_model
        version = var.team_specialist_model_version
      }
      versionUpgradeOption = "OnceNewDefaultVersionAvailable"
      raiPolicyName        = local.separate_specialist_account ? azapi_resource.specialist_rai_policy[0].name : azapi_resource.rai_policy.name
    }
  }

  depends_on = [azapi_resource.team_deployment]
}

# The hosted agent's identity calls the specialist deployment directly when it lives in its own account.
resource "azurerm_role_assignment" "agent_specialist_user" {
  count = local.deploy_agent && local.separate_specialist_account ? 1 : 0

  scope                = azapi_resource.specialist_account[0].id
  role_definition_name = "Cognitive Services OpenAI User"
  principal_id         = azapi_data_plane_resource.agent[0].output.instance_identity.principal_id
  principal_type       = "ServicePrincipal"

  lifecycle {
    ignore_changes = [principal_id]
  }
}

# The deploying user runs the console host and the evaluations against the specialist deployment too.
resource "azurerm_role_assignment" "current_user_specialist_user" {
  count = local.separate_specialist_account ? 1 : 0

  scope                = azapi_resource.specialist_account[0].id
  role_definition_name = "Cognitive Services OpenAI User"
  principal_id         = data.azurerm_client_config.current.object_id
}
