# Variables for the multi-agent team (the rest are in variables.tf).

variable "name_suffix" {
  type        = string
  description = "Short lowercase suffix for this deployment's names (proj-advteam-<suffix>, advisor-team-<suffix>-*). Blank: random."
  default     = ""

  validation {
    condition     = can(regex("^[a-z0-9]{0,8}$", var.name_suffix))
    error_message = "Use up to 8 lowercase letters and digits."
  }
}

variable "existing_search_service_id" {
  type        = string
  description = "Resource ID of an existing Azure AI Search service for Foundry IQ (this deployment adds its own index and knowledge base). Blank: create one."
  default     = ""

  validation {
    condition     = var.existing_search_service_id == "" || can(regex("(?i)^/subscriptions/[^/]+/resourceGroups/[^/]+/providers/Microsoft\\.Search/searchServices/[^/]+$", var.existing_search_service_id))
    error_message = "Use the resource ID of a Microsoft.Search/searchServices resource, or blank to create one."
  }
}

variable "create_workiq_service_principal" {
  type        = bool
  description = "Create the tenant's Work IQ service principal. Only for a tenant that has none; an existing one is never adopted."
  default     = false
}

variable "team_mode" {
  type        = string
  description = "How the hosted agent runs: team (the lead and its specialists) or solo (one agent with every tool)."
  default     = "team"

  validation {
    condition     = contains(["team", "solo"], var.team_mode)
    error_message = "Use team or solo."
  }
}

variable "team_lead_deployment" {
  type    = string
  default = "team-lead"
}

variable "team_lead_model" {
  type    = string
  default = "gpt-5.6-luna"
}

variable "team_lead_model_version" {
  type    = string
  default = "2026-07-09"
}

variable "team_lead_sku" {
  type        = string
  description = "GlobalStandard or DataZoneStandard."
  default     = "DataZoneStandard"
}

variable "team_lead_capacity" {
  type        = number
  description = "Thousands of tokens a minute."
  default     = 100
}

variable "team_specialist_deployment" {
  type    = string
  default = "team-specialist"
}

variable "team_specialist_model" {
  type    = string
  default = "gpt-5.6-luna"
}

variable "team_specialist_model_version" {
  type    = string
  default = "2026-07-09"
}

variable "team_specialist_sku" {
  type    = string
  default = "DataZoneStandard"
}

variable "team_specialist_capacity" {
  type    = number
  default = 100
}

variable "specialist_location" {
  type        = string
  description = "Region for a separate account that hosts only team-specialist (for quota). Blank: the main account."
  default     = ""
}

variable "team_judge_deployment" {
  type    = string
  default = "team-judge"
}

variable "team_judge_model" {
  type    = string
  default = "gpt-5.5"
}

variable "team_judge_model_version" {
  type    = string
  default = "2026-04-24"
}

variable "team_judge_sku" {
  type    = string
  default = "GlobalStandard"
}

variable "team_judge_capacity" {
  type    = number
  default = 150
}

variable "enable_what_if" {
  description = "Add-on O3: the what-if analyst (scenarios computed in code in a Monty sandbox)."
  type        = bool
  default     = true
}

variable "enable_committee" {
  description = "Add-on O4: the investment committee group chat (experimental)."
  type        = bool
  default     = false
}

variable "enable_purview" {
  description = "Add-on O2: Microsoft Purview on the lead's turns. Grants the agent identity three Microsoft Graph application permissions (admin consent)."
  type        = bool
  default     = false
}

variable "enable_morning_note" {
  description = "Add-on O1: the morning note on Azure Functions (Flex Consumption) and the Durable Task Scheduler."
  type        = bool
  default     = false
}

variable "morning_note_watch_list" {
  description = "Tickers the morning note covers (comma-separated, public symbols only)."
  type        = string
  default     = "NVDA,MSFT,AAPL"
}

variable "morning_note_schedule" {
  description = "When the morning note starts on its own (NCRONTAB, UTC); empty to start it only on demand."
  type        = string
  default     = "0 0 11 * * 1-5"
}
