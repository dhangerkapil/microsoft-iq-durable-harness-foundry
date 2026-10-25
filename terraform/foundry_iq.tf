# ---------------------------------------------------------------------------
# Foundry IQ: the firm's knowledge base. Azure AI Search agentic retrieval over
# the firm library (policies, procedures, house view, approved products).
# scripts/Set-FoundryIqKnowledge.ps1 pushes the library's sections and their
# embeddings into a search index (no storage account or indexer, so it works
# where policy disables public storage access), then creates the knowledge
# source, the knowledge base, the project connection to its MCP endpoint and
# the toolbox.
# ---------------------------------------------------------------------------
resource "azurerm_search_service" "knowledge" {
  count = local.create_search ? 1 : 0

  name                          = "srch${local.func_name}"
  resource_group_name           = azurerm_resource_group.this.name
  location                      = var.foundry_iq_location != "" ? var.foundry_iq_location : azurerm_resource_group.this.location
  sku                           = var.foundry_iq_search_sku
  semantic_search_sku           = "standard"
  local_authentication_enabled  = false
  public_network_access_enabled = true
  tags                          = local.tags

  identity {
    type = "SystemAssigned"
  }

  lifecycle {
    ignore_changes = [tags]
  }
}

# Embeddings for the knowledge source's chunks (vector half of hybrid search). An existing Foundry account already has
# this deployment (existing_foundry_account_id checks it).
resource "azapi_resource" "embedding_deployment" {
  count = var.foundry_iq_enabled && local.create_foundry ? 1 : 0

  type                      = "Microsoft.CognitiveServices/accounts/deployments@2024-10-01"
  name                      = var.embedding_model
  parent_id                 = azapi_resource.ai_foundry[0].id
  schema_validation_enabled = false
  retry                     = local.foundry_operation_retry

  body = {
    sku = {
      name     = "GlobalStandard"
      capacity = var.embedding_model_capacity
    }
    properties = {
      model = {
        format  = "OpenAI"
        name    = var.embedding_model
        version = var.embedding_model_version
      }
      versionUpgradeOption = "OnceNewDefaultVersionAvailable"
    }
  }

  # One deployment operation at a time per account.
  depends_on = [azapi_resource.team_deployment, azapi_resource.specialist_deployment, azapi_resource.model_search_deployment]
}

# The search service calls the embedding model (query-time vectors) and the query-planning model as itself.
resource "azurerm_role_assignment" "search_foundry_user" {
  # An existing Search service already calls the existing account (its owner granted this).
  count = local.create_search ? 1 : 0

  scope                = local.foundry_account_id
  role_definition_name = "Cognitive Services User"
  principal_id         = azurerm_search_service.knowledge[0].identity[0].principal_id
  principal_type       = "ServicePrincipal"
}

# The project's managed identity backs the knowledge base MCP connection: read-only retrieval.
resource "azurerm_role_assignment" "project_search_reader" {
  count = var.foundry_iq_enabled ? 1 : 0

  # Read-only, for this project's identity. In a shared Search service this is a listed addition.
  scope                = local.search_id
  role_definition_name = "Search Index Data Reader"
  principal_id         = azapi_resource.ai_foundry_project.output.identity.principalId
  principal_type       = "ServicePrincipal"
}

# The deploying user creates the index, pushes the library and creates the knowledge source and knowledge base.
resource "azurerm_role_assignment" "current_user_search_contributor" {
  # In a shared Search service the deploying user must already hold these roles; nothing is added for them.
  count = local.create_search ? 1 : 0

  scope                = azurerm_search_service.knowledge[0].id
  role_definition_name = "Search Service Contributor"
  principal_id         = data.azurerm_client_config.current.object_id
}

resource "azurerm_role_assignment" "current_user_search_data_contributor" {
  count = local.create_search ? 1 : 0

  scope                = azurerm_search_service.knowledge[0].id
  role_definition_name = "Search Index Data Contributor"
  principal_id         = data.azurerm_client_config.current.object_id
}
