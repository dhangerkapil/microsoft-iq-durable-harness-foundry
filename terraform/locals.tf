locals {
  suffix         = var.name_suffix != "" ? var.name_suffix : random_string.unique.result
  func_name      = "advteam${local.suffix}"
  loc_for_naming = lower(replace(var.location, " ", ""))
  gh_repo        = split("/", var.gh_repo)[1]

  foundry_name    = var.foundry_managed_network ? "aif${local.func_name}-managed" : "aif${local.func_name}"
  foundry_project = "proj-advteam-${local.suffix}"
  # Create the Foundry account, or use an existing one (for example in another resource group). Either way this
  # deployment creates its own project, so deployments that share an account never share agents, tools or state.
  create_foundry     = var.existing_foundry_account_id == ""
  foundry_account_id = local.create_foundry ? azapi_resource.ai_foundry[0].id : var.existing_foundry_account_id
  # Project connection names are unique across a Foundry account, not per project: in a shared account each
  # deployment's connections carry its own suffix. An account the deployment created keeps the plain names.
  connection_suffix = local.create_foundry ? "" : "-${local.func_name}"
  # The Application Insights connected to an existing account, which receives every one of its projects' traces.
  account_appinsights_ids = local.create_foundry ? [] : [
    for target in try(data.azapi_resource_list.existing_foundry_connections[0].output.appinsights, []) : target
    if target != null && target != ""
  ]
  # The endpoint host is the account's custom subdomain: the account name for an account created here.
  foundry_subdomain = local.create_foundry ? local.foundry_name : data.azapi_resource.existing_foundry[0].output.properties.customSubDomainName
  foundry_location  = local.create_foundry ? azurerm_resource_group.this.location : data.azapi_resource.existing_foundry[0].location
  foundry_endpoint  = "https://${local.foundry_subdomain}.services.ai.azure.com/api/projects/${local.foundry_project}"
  toolbox_endpoint  = "${local.foundry_endpoint}/toolboxes/${var.workiq_toolbox_name}/mcp?api-version=v1"
  # Create a Search service for Foundry IQ, or put this deployment's index and knowledge base in an existing one.
  create_search = var.foundry_iq_enabled && var.existing_search_service_id == ""
  search_id     = !var.foundry_iq_enabled ? "" : (local.create_search ? azurerm_search_service.knowledge[0].id : var.existing_search_service_id)
  # Entra display names: approved additions in a shared tenant start with advisor-team.
  app_prefix = "advisor-team-${local.suffix}"
  # Create a Fabric capacity, or put the workspace on an existing one.
  create_fabric_capacity = var.existing_fabric_capacity_id == "" && var.fabric_capacity_sku != ""
  fabric_enabled         = var.existing_fabric_capacity_id != "" || var.fabric_capacity_sku != ""
  # Optional toolboxes created by scripts/Set-AdvisorToolbox.ps1: the Work IQ governance probe and Fabric IQ.
  optional_toolbox_environment = merge(
    var.governance_toolbox_name == "" ? {} : {
      GOVERNANCE_TOOLBOX_ENDPOINT = "${local.foundry_endpoint}/toolboxes/${var.governance_toolbox_name}/mcp?api-version=v1"
    },
    var.fabric_toolbox_name == "" ? {} : {
      FABRIC_TOOLBOX_ENDPOINT = "${local.foundry_endpoint}/toolboxes/${var.fabric_toolbox_name}/mcp?api-version=v1"
    },
    var.drafts_toolbox_name == "" ? {} : {
      DRAFTS_TOOLBOX_ENDPOINT = "${local.foundry_endpoint}/toolboxes/${var.drafts_toolbox_name}/mcp?api-version=v1"
    },
    var.foundryiq_toolbox_name == "" ? {} : {
      FOUNDRYIQ_TOOLBOX_ENDPOINT = "${local.foundry_endpoint}/toolboxes/${var.foundryiq_toolbox_name}/mcp?api-version=v1"
    },
    var.webiq_toolbox_name == "" ? {} : {
      WEBIQ_TOOLBOX_ENDPOINT = "${local.foundry_endpoint}/toolboxes/${var.webiq_toolbox_name}/mcp?api-version=v1"
    },
    var.workiq_chat_toolbox_name == "" ? {} : {
      WORKIQ_CHAT_TOOLBOX_ENDPOINT = "${local.foundry_endpoint}/toolboxes/${var.workiq_chat_toolbox_name}/mcp?api-version=v1"
    },
    length(var.web_boundary_terms) == 0 ? {} : {
      WEB_BOUNDARY_TERMS = join(",", var.web_boundary_terms)
    },
  )
  workiq_mcp_endpoint = "https://workiq.svc.cloud.microsoft/mcp"

  deploy_agent = var.agent_image_uri != ""
  deploy_ui    = var.ui_image_uri != ""
  ui_name      = "ui-advisor-team"
  ui_url       = local.deploy_ui ? "https://${local.ui_name}.${azurerm_container_app_environment.ui[0].default_domain}" : null

  workiq_resource_app_id = "fdcc1f02-fc51-4226-8753-f668596af7f7"
  workiq_ask_scope_id    = "0b1715fd-f4bf-4c63-b16d-5be31f9847c2"

  foundry_operation_retry = {
    error_message_regex  = ["(?i)Another operation (is in progress|is being performed)"]
    interval_seconds     = 5
    max_interval_seconds = 30
  }

  tags = {
    managed_by = "terraform"
    repo       = local.gh_repo
    workload   = "advisor-team"
    solution   = "advisor-team"
  }
}
