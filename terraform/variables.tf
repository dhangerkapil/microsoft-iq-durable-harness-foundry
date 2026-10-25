variable "subscription_id" {
  type      = string
  sensitive = true
}

variable "location" {
  type    = string
  default = "EastUS2"
}

variable "resource_group_name" {
  type        = string
  description = "Optional fixed resource group name; blank derives one from gh_repo and location."
  default     = ""
}

variable "fabric_capacity_sku" {
  type        = string
  description = "Fabric capacity SKU for the portfolio lakehouse and data agent (F2 or larger); blank skips it."
  default     = "F8"

  validation {
    condition     = var.fabric_capacity_sku == "" || can(regex("^F(2|4|8|16|32|64|128|256|512|1024|2048)$", var.fabric_capacity_sku))
    error_message = "Use a Fabric F SKU such as F8, or blank to skip the capacity."
  }
}

variable "fabric_location" {
  type        = string
  description = "Region for the Fabric capacity when it must differ from location (Fabric quota is regional)."
  default     = ""
}

variable "existing_foundry_account_id" {
  type        = string
  description = <<-EOT
    Resource ID of an existing Microsoft Foundry account (kind AIServices, project management enabled) to use instead
    of creating one, for example in another resource group. The deployment then creates only its own project and its
    own guardrail policy in that account, and uses the account's model deployments: chat_model, optimizer_model,
    embedding_model and every model_search_deployments key must already be deployed there. Empty (default): create
    the account and deploy the models.
  EOT
  default     = ""

  validation {
    condition     = var.existing_foundry_account_id == "" || can(regex("(?i)^/subscriptions/[^/]+/resourceGroups/[^/]+/providers/Microsoft\\.CognitiveServices/accounts/[^/]+$", var.existing_foundry_account_id))
    error_message = "Use the resource ID of a Microsoft.CognitiveServices/accounts resource, or blank to create one."
  }
}

variable "existing_fabric_capacity_id" {
  type        = string
  description = "Resource ID of an existing Fabric capacity (F2 or larger, for example in another resource group) for this deployment's Fabric workspace. Empty (default): create a capacity when fabric_capacity_sku is set."
  default     = ""

  validation {
    condition     = var.existing_fabric_capacity_id == "" || can(regex("(?i)^/subscriptions/[^/]+/resourceGroups/[^/]+/providers/Microsoft\\.Fabric/capacities/[^/]+$", var.existing_fabric_capacity_id))
    error_message = "Use the resource ID of a Microsoft.Fabric/capacities resource, or blank."
  }
}

variable "gh_repo" {
  type        = string
  description = "GitHub repository in owner/name form used for naming."
  default     = "dhangerkapil/investment-advisor-multi-agent"

  validation {
    condition     = can(regex("^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$", var.gh_repo))
    error_message = "Use a GitHub repository in owner/name form."
  }
}

variable "chat_model" {
  type        = string
  description = "Azure AI Foundry chat model to deploy for the agent."
  default     = "gpt-5.6-luna"
}

variable "chat_model_version" {
  type    = string
  default = "2026-07-09"
}

variable "chat_model_capacity" {
  type        = number
  description = "TPM capacity (in thousands) for the chat model deployment."
  default     = 50
}

variable "optimizer_model" {
  type        = string
  description = "Model the Foundry agent optimizer uses to generate candidates (gpt-5, gpt-5.1, gpt-5.2, gpt-5.4 or gpt-5.5); empty to skip."
  default     = "gpt-5.5"
}

variable "optimizer_model_version" {
  type    = string
  default = "2026-04-24"
}

variable "optimizer_model_capacity" {
  type        = number
  description = "TPM capacity (in thousands) for the optimizer model deployment, which is also the Insights judge."
  default     = 100
}

variable "model_search_deployments" {
  type = map(object({
    version  = string
    capacity = number
  }))
  description = "Extra chat model deployments the agent optimizer compares the agent on (model selection), keyed by model name, for example { \"gpt-6-sol\" = { version = \"2026-09-22\", capacity = 150 } }."
  default     = {}
}

variable "foundry_iq_enabled" {
  type        = bool
  description = "Foundry IQ firm knowledge base: Azure AI Search and an embedding deployment (the library is pushed into the index; no storage account)."
  default     = true
}

variable "foundry_iq_search_sku" {
  type        = string
  description = "Azure AI Search tier for the Foundry IQ knowledge base (basic or larger; agentic retrieval needs the semantic ranker)."
  default     = "basic"
}

variable "foundry_iq_location" {
  type        = string
  description = "Region for Azure AI Search when it differs from the resource group's (a region with agentic retrieval)."
  default     = ""
}

variable "embedding_model" {
  type        = string
  description = "Embedding model for the Foundry IQ knowledge source."
  default     = "text-embedding-3-large"
}

variable "embedding_model_version" {
  type    = string
  default = "1"
}

variable "embedding_model_capacity" {
  type        = number
  description = "TPM capacity (in thousands) for the embedding deployment."
  default     = 50
}

variable "foundryiq_toolbox_name" {
  type        = string
  description = "Foundry IQ toolbox (knowledge_base_retrieve) for the search_firm_knowledge tool; empty disables it."
  default     = ""
}

variable "webiq_toolbox_name" {
  type        = string
  description = "Web IQ toolbox for the search_public_web and read_public_web_page tools; empty disables them."
  default     = ""
}

variable "workiq_chat_toolbox_name" {
  type        = string
  description = "Work IQ Chat toolbox (work_iq_preview over the RemoteA2A connection) for the delegate_to_work_iq tool; empty disables it."
  default     = ""
}

variable "web_boundary_terms" {
  type        = list(string)
  description = "Names that must never be sent to Web IQ (clients, advisors, analysts); the agent's boundary guard blocks them."
  default     = []
}

variable "user_object_ids" {
  type        = set(string)
  description = "Entra object IDs granted Foundry User on the project, not Microsoft 365 data access."
  default     = []
}

variable "workiq_toolbox_name" {
  type        = string
  description = "Foundry Toolbox exposing the custom Work IQ MCP connection."
  default     = "work-iq-toolbox"

  validation {
    condition     = can(regex("^[A-Za-z0-9_-]+$", var.workiq_toolbox_name))
    error_message = "Use a toolbox name containing only letters, digits, underscores and hyphens."
  }
}

variable "agent_capture_content" {
  type        = bool
  description = "Record prompts, responses and tool payloads in traces (ENABLE_SENSITIVE_DATA). Enable only for synthetic demo tenants."
  default     = false
}

variable "advisor_evidence_loop" {
  type        = bool
  description = "Enable the harness evidence loop: one extra pass when a client brief lacks Fabric IQ or Work IQ evidence."
  default     = true
}

variable "demo_faults" {
  type        = string
  description = "Comma-separated components whose outage the agent always simulates (for example \"fabric\"); empty for none."
  default     = ""
}

variable "workiq_tool_mode" {
  type        = string
  description = <<-EOT
    How the agent uses Work IQ: mcp (granular Work IQ MCP tools through a toolbox whose custom OAuth MCP
    connection scripts/Set-AdvisorToolbox.ps1 creates) or a2a (the work_iq_preview tool). Empty follows
    workiq_connection_kind.
  EOT
  default     = ""

  validation {
    condition     = contains(["", "a2a", "mcp"], var.workiq_tool_mode)
    error_message = "Use mcp, a2a or an empty string."
  }
}

variable "governance_toolbox_name" {
  type        = string
  description = "Optional demo-only toolbox for the Work IQ governance probe (fetch and create_entity); empty disables the probe."
  default     = ""
}

variable "drafts_toolbox_name" {
  type        = string
  description = "Optional toolbox exposing only Work IQ create_entity, for the approval-gated draft email tool; empty disables it."
  default     = ""
}

variable "fabric_toolbox_name" {
  type        = string
  description = "Optional Fabric IQ-only toolbox for the lazy query_portfolio_data tool; empty disables it."
  default     = ""
}

variable "agent_extra_environment" {
  type        = map(string)
  description = "Extra hosted-agent environment variables, for example ADVISOR_CLOCK_NOT_BEFORE to rehearse the story before Monday's alert, ADVISOR_CLOCK_NOT_AFTER to present it after its Thursday, or ADVISOR_OWN_SESSION_PREFIXES for tests."
  default     = {}
}

variable "workiq_connection_kind" {
  type        = string
  description = <<-EOT
    a2a (default): the documented work_iq_preview toolbox tool over a RemoteA2A connection that
    scripts/Set-WorkIqConnection.ps1 creates. mcp: the original Terraform-managed custom OAuth MCP connection.
    Switching an existing mcp deployment to a2a: run `terraform state rm azapi_resource.workiq_mcp` first,
    because that connection has prevent_destroy.
  EOT
  default     = "a2a"

  validation {
    condition     = contains(["a2a", "mcp"], var.workiq_connection_kind)
    error_message = "Use a2a or mcp."
  }
}

variable "workiq_a2a_connection_name" {
  type        = string
  description = "Name of the Work IQ RemoteA2A project connection created by scripts/Set-WorkIqConnection.ps1."
  default     = "work-iq"

  validation {
    condition     = can(regex("^[A-Za-z0-9_-]+$", var.workiq_a2a_connection_name))
    error_message = "Use a connection name containing only letters, digits, underscores and hyphens."
  }
}

variable "foundry_managed_network" {
  type        = bool
  description = <<-EOT
    Enable the Foundry managed virtual network (preview). OAuth2 tool connections, including Work IQ,
    currently report "not a supported target" on managed-network accounts, so the advisor demo disables it.
    Changing this value replaces the Foundry account.
  EOT
  default     = true
}

variable "workiq_mcp_connection_name" {
  type        = string
  description = "Terraform-managed custom OAuth MCP connection; import an existing connection before applying."
  default     = "work-iq-mcp"

  validation {
    condition     = can(regex("^[A-Za-z0-9_-]+$", var.workiq_mcp_connection_name))
    error_message = "Use a connection name containing only letters, digits, underscores and hyphens."
  }
}

variable "workiq_mcp_client_secret" {
  type        = string
  description = "Existing OAuth app secret for connection creation/rotation only; omitted when importing/reusing stored credentials."
  sensitive   = true
  ephemeral   = true
  default     = null
}

variable "workiq_mcp_secret_version" {
  type        = string
  description = "Increment when supplying a new ephemeral secret to rotate the connection credential."
  default     = "0"
}

variable "workiq_mcp_allowed_tools" {
  type        = list(string)
  description = "Exact upstream MCP tool names exposed to the agent; defaults exclude mutation tools."
  default     = ["ask", "fetch", "get_schema", "search_paths", "fetch_blob", "retrieve"]

  validation {
    condition = (
      length(var.workiq_mcp_allowed_tools) > 0 &&
      length(distinct(var.workiq_mcp_allowed_tools)) == length(var.workiq_mcp_allowed_tools) &&
      alltrue([for name in var.workiq_mcp_allowed_tools : can(regex("^[A-Za-z0-9_-]+$", name))])
    )
    error_message = "Provide a nonempty list of unique upstream MCP tool names."
  }
}

variable "workiq_mcp_require_approval" {
  type        = string
  description = "Whether the agent must obtain confirmation before calling the allowed MCP tools."
  default     = "never"

  validation {
    condition     = contains(["always", "never"], var.workiq_mcp_require_approval)
    error_message = "Use always or never for MCP tool approval."
  }
}

variable "ui_image_uri" {
  type        = string
  description = "Immutable UI image URI; blank leaves the optional Azure chat UI unprovisioned."
  default     = ""

  validation {
    condition     = var.ui_image_uri == "" || can(regex("^[a-z0-9.-]+\\.azurecr\\.io/[a-z0-9/._-]+@sha256:[0-9a-f]{64}$", var.ui_image_uri))
    error_message = "Use an immutable ACR image URI ending in @sha256:<64 hex characters>."
  }
}

variable "ui_min_replicas" {
  type        = number
  description = "Minimum Container Apps UI replicas; zero scales to zero when idle."
  default     = 1

  validation {
    condition     = var.ui_min_replicas >= 0 && var.ui_min_replicas <= 2 && floor(var.ui_min_replicas) == var.ui_min_replicas
    error_message = "Use an integer from zero to two."
  }
}

variable "ui_additional_redirect_uris" {
  type        = list(string)
  description = "Optional additional SPA redirects, for example http://localhost:5173/ for local frontend development."
  default     = []

  validation {
    condition = alltrue([
      for uri in var.ui_additional_redirect_uris :
      can(regex("^https://[^/?#]+/[^?#]*$", uri)) || can(regex("^http://localhost:[0-9]+/[^?#]*$", uri))
    ])
    error_message = "Use HTTPS redirects, or localhost HTTP redirects with an explicit port."
  }
}

variable "workiq_toolbox_redirect_uri" {
  type        = string
  description = "Foundry OAuth redirect URL; scripts/setup_toolbox.py persists this nonsecret value."
  default     = ""

  validation {
    condition     = var.workiq_toolbox_redirect_uri == "" || can(regex("^https://[^/?#]+/", var.workiq_toolbox_redirect_uri))
    error_message = "Use the HTTPS OAuth redirect URL returned by the Foundry connection."
  }
}

# scripts/deploy.sh persists release inputs in agent.auto.tfvars.
variable "agent_name" {
  type        = string
  description = "Name of the Foundry hosted agent."
  default     = "advisor-team"
}

variable "agent_image_uri" {
  type        = string
  description = "Immutable ACR image URI. Leave blank for a platform-only apply."
  default     = ""

  validation {
    condition     = var.agent_image_uri == "" || can(regex("^[a-z0-9.-]+\\.azurecr\\.io/[a-z0-9/._-]+@sha256:[0-9a-f]{64}$", var.agent_image_uri))
    error_message = "Use an immutable ACR image URI ending in @sha256:<64 hex characters>."
  }

}

variable "history_retention_days" {
  type        = number
  description = "Cosmos message TTL; deletion is asynchronous. Foundry Responses retention is separate."
  default     = 30

  validation {
    condition     = var.history_retention_days >= 1 && floor(var.history_retention_days) == var.history_retention_days
    error_message = "History retention must be a positive whole number of days."
  }
}

variable "agent_cpu" {
  type    = string
  default = "1"
}

variable "agent_memory" {
  type    = string
  default = "2Gi"

  validation {
    condition     = contains(["0.5:1Gi", "1:2Gi", "2:4Gi"], "${var.agent_cpu}:${var.agent_memory}")
    error_message = "Choose a supported CPU/memory pair: 0.5/1Gi, 1/2Gi, or 2/4Gi."
  }
}
