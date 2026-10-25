# ---------------------------------------------------------------------------
# Add-on O1: the morning note, a durable multi-agent workflow on Azure Functions (Flex Consumption) and the Durable
# Task Scheduler. It handles no client data and acts as no user: it runs as its own managed identity,
# reads public news (Web IQ) and firm policy (Foundry IQ) through this solution's toolboxes, and publishes the approved
# house view to this solution's firm library index, which the team's firm-policy specialist reads.
# ---------------------------------------------------------------------------
locals {
  morning_note       = var.enable_morning_note
  morning_note_name  = "func-mn-${local.suffix}"
  morning_note_index = "team-firm-library-index"
}

resource "azurerm_user_assigned_identity" "morning_note" {
  count = local.morning_note ? 1 : 0

  name                = "id-morning-note-${local.suffix}"
  resource_group_name = azurerm_resource_group.this.name
  location            = azurerm_resource_group.this.location
  tags                = local.tags
}

resource "azapi_resource" "dts_scheduler" {
  count = local.morning_note ? 1 : 0

  type      = "Microsoft.DurableTask/schedulers@2026-02-01"
  name      = "dts-advteam-${local.suffix}"
  parent_id = azurerm_resource_group.this.id
  location  = azurerm_resource_group.this.location
  tags      = local.tags
  body = {
    properties = {
      ipAllowlist = ["0.0.0.0/0"]
      sku         = { name = "Consumption" }
    }
  }
  response_export_values = ["properties.endpoint"]
}

resource "azapi_resource" "dts_task_hub" {
  count = local.morning_note ? 1 : 0

  type      = "Microsoft.DurableTask/schedulers/taskHubs@2026-02-01"
  name      = "advisor-team"
  parent_id = azapi_resource.dts_scheduler[0].id
  body      = { properties = {} }
}

resource "azurerm_storage_account" "morning_note" {
  count = local.morning_note ? 1 : 0

  name                            = "stmn${local.suffix}${substr(md5(azurerm_resource_group.this.id), 0, 6)}"
  resource_group_name             = azurerm_resource_group.this.name
  location                        = azurerm_resource_group.this.location
  account_tier                    = "Standard"
  account_replication_type        = "LRS"
  min_tls_version                 = "TLS1_2"
  allow_nested_items_to_be_public = false
  shared_access_key_enabled       = false
  # Private, as policy in many subscriptions requires: the app reaches it through private endpoints in its own network.
  public_network_access_enabled = false
  tags                          = local.tags
}

# The app's own network: its outbound traffic and the private endpoints of its storage.
resource "azurerm_virtual_network" "morning_note" {
  count = local.morning_note ? 1 : 0

  name                = "vnet-mn-${local.suffix}"
  resource_group_name = azurerm_resource_group.this.name
  location            = azurerm_resource_group.this.location
  address_space       = ["10.51.0.0/24"]
  tags                = local.tags
}

resource "azurerm_subnet" "morning_note_app" {
  count = local.morning_note ? 1 : 0

  name                            = "app"
  resource_group_name             = azurerm_resource_group.this.name
  virtual_network_name            = azurerm_virtual_network.morning_note[0].name
  address_prefixes                = ["10.51.0.0/26"]
  default_outbound_access_enabled = true

  delegation {
    name = "flex-consumption"
    service_delegation {
      name    = "Microsoft.App/environments"
      actions = ["Microsoft.Network/virtualNetworks/subnets/join/action"]
    }
  }
}

resource "azurerm_subnet" "morning_note_endpoints" {
  count = local.morning_note ? 1 : 0

  name                 = "endpoints"
  resource_group_name  = azurerm_resource_group.this.name
  virtual_network_name = azurerm_virtual_network.morning_note[0].name
  address_prefixes     = ["10.51.0.64/27"]
}

resource "azurerm_private_dns_zone" "morning_note" {
  for_each = local.morning_note ? toset(["blob", "queue", "table"]) : toset([])

  name                = "privatelink.${each.key}.core.windows.net"
  resource_group_name = azurerm_resource_group.this.name
  tags                = local.tags
}

resource "azurerm_private_dns_zone_virtual_network_link" "morning_note" {
  for_each = azurerm_private_dns_zone.morning_note

  name                  = "mn-${each.key}"
  resource_group_name   = azurerm_resource_group.this.name
  private_dns_zone_name = each.value.name
  virtual_network_id    = azurerm_virtual_network.morning_note[0].id
}

resource "azurerm_private_endpoint" "morning_note" {
  for_each = azurerm_private_dns_zone.morning_note

  name                = "pe-mn-${each.key}-${local.suffix}"
  resource_group_name = azurerm_resource_group.this.name
  location            = azurerm_resource_group.this.location
  subnet_id           = azurerm_subnet.morning_note_endpoints[0].id
  tags                = local.tags

  private_service_connection {
    name                           = "mn-${each.key}"
    private_connection_resource_id = azurerm_storage_account.morning_note[0].id
    subresource_names              = [each.key]
    is_manual_connection           = false
  }

  private_dns_zone_group {
    name                 = "default"
    private_dns_zone_ids = [each.value.id]
  }
}

resource "azurerm_storage_container" "morning_note_deployment" {
  count = local.morning_note ? 1 : 0

  name                  = "app-package"
  storage_account_id    = azurerm_storage_account.morning_note[0].id
  container_access_type = "private"
}

resource "azurerm_role_assignment" "morning_note_storage" {
  for_each = local.morning_note ? toset(["Storage Blob Data Owner", "Storage Queue Data Contributor", "Storage Table Data Contributor"]) : toset([])

  scope                = azurerm_storage_account.morning_note[0].id
  role_definition_name = each.value
  principal_id         = azurerm_user_assigned_identity.morning_note[0].principal_id
  principal_type       = "ServicePrincipal"
}

# The deploying user uploads the package and reads the run history (the scheduler dashboard).
resource "azurerm_role_assignment" "current_user_morning_note" {
  for_each = local.morning_note ? {
    blob = { scope = azurerm_storage_account.morning_note[0].id, role = "Storage Blob Data Owner" }
    dts  = { scope = azapi_resource.dts_scheduler[0].id, role = "Durable Task Data Contributor" }
  } : {}

  scope                = each.value.scope
  role_definition_name = each.value.role
  principal_id         = data.azurerm_client_config.current.object_id
}

resource "azurerm_role_assignment" "morning_note_access" {
  for_each = local.morning_note ? {
    dts     = { scope = azapi_resource.dts_scheduler[0].id, role = "Durable Task Data Contributor" }
    project = { scope = azapi_resource.ai_foundry_project.id, role = "Foundry User" }
    index   = { scope = "${local.search_id}/indexes/${local.morning_note_index}", role = "Search Index Data Contributor" }
  } : {}

  scope                = each.value.scope
  role_definition_name = each.value.role
  principal_id         = azurerm_user_assigned_identity.morning_note[0].principal_id
  principal_type       = "ServicePrincipal"
}

resource "azurerm_service_plan" "morning_note" {
  count = local.morning_note ? 1 : 0

  name                = "asp-mn-${local.suffix}"
  resource_group_name = azurerm_resource_group.this.name
  location            = azurerm_resource_group.this.location
  os_type             = "Linux"
  sku_name            = "FC1"
  tags                = local.tags
}

resource "azurerm_function_app_flex_consumption" "morning_note" {
  count = local.morning_note ? 1 : 0

  name                              = local.morning_note_name
  resource_group_name               = azurerm_resource_group.this.name
  location                          = azurerm_resource_group.this.location
  service_plan_id                   = azurerm_service_plan.morning_note[0].id
  storage_container_type            = "blobContainer"
  storage_container_endpoint        = "${azurerm_storage_account.morning_note[0].primary_blob_endpoint}${azurerm_storage_container.morning_note_deployment[0].name}"
  storage_authentication_type       = "UserAssignedIdentity"
  storage_user_assigned_identity_id = azurerm_user_assigned_identity.morning_note[0].id
  runtime_name                      = "python"
  runtime_version                   = "3.12"
  maximum_instance_count            = 40
  instance_memory_in_mb             = 2048
  https_only                        = true
  virtual_network_subnet_id         = azurerm_subnet.morning_note_app[0].id
  tags                              = local.tags

  identity {
    type         = "UserAssigned"
    identity_ids = [azurerm_user_assigned_identity.morning_note[0].id]
  }

  site_config {
    application_insights_connection_string = azurerm_application_insights.this.connection_string
  }

  app_settings = {
    AzureWebJobsStorage__accountName         = azurerm_storage_account.morning_note[0].name
    AzureWebJobsStorage__credential          = "managedidentity"
    AzureWebJobsStorage__clientId            = azurerm_user_assigned_identity.morning_note[0].client_id
    DURABLE_TASK_SCHEDULER_CONNECTION_STRING = "Endpoint=${azapi_resource.dts_scheduler[0].output.properties.endpoint};Authentication=ManagedIdentity;ClientID=${azurerm_user_assigned_identity.morning_note[0].client_id}"
    TASKHUB_NAME                             = azapi_resource.dts_task_hub[0].name
    AZURE_CLIENT_ID                          = azurerm_user_assigned_identity.morning_note[0].client_id
    PROJECT_ENDPOINT                         = local.foundry_endpoint
    MODEL_DEPLOYMENT                         = var.team_lead_deployment
    WEBIQ_TOOLBOX_ENDPOINT                   = "${local.foundry_endpoint}/toolboxes/${var.webiq_toolbox_name}/mcp?api-version=v1"
    FOUNDRYIQ_TOOLBOX_ENDPOINT               = "${local.foundry_endpoint}/toolboxes/${var.foundryiq_toolbox_name}/mcp?api-version=v1"
    SEARCH_ENDPOINT                          = var.foundry_iq_enabled ? "https://${element(split("/", local.search_id), 8)}.search.windows.net" : ""
    SEARCH_INDEX                             = local.morning_note_index
    EMBEDDING_DEPLOYMENT                     = var.foundry_iq_enabled ? var.embedding_model : ""
    WATCH_LIST                               = var.morning_note_watch_list
    MORNING_NOTE_SCHEDULE                    = var.morning_note_schedule
  }

  depends_on = [azurerm_role_assignment.morning_note_storage, azurerm_private_endpoint.morning_note,
  azurerm_private_dns_zone_virtual_network_link.morning_note]
}
