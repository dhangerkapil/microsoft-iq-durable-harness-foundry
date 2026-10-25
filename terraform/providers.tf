terraform {
  required_version = ">= 1.11.0"

  # State stays outside the repository. Initialize with:
  #   terraform -chdir=terraform init -backend-config="path=<state file outside the repo>"
  # scripts/Use-IsolatedAzure.ps1 sets ADVISOR_TF_STATE for this purpose.
  backend "local" {}

  required_providers {
    azurerm = {
      source  = "hashicorp/azurerm"
      version = "=4.67.0"
    }
    random = {
      source  = "hashicorp/random"
      version = "=3.1.0"
    }
    azapi = {
      source  = "azure/azapi"
      version = "=2.13.0"
    }
    azuread = {
      source  = "hashicorp/azuread"
      version = "=3.6.0"
    }
  }
}

provider "azurerm" {
  features {
    key_vault {
      purge_soft_delete_on_destroy = false
    }
    resource_group {
      # The resource group belongs to this deployment alone: destroy also removes what scripts and Azure added to it
      # (UI instances from Publish-UiInstance.ps1, Application Insights smart-detection alert rules).
      prevent_deletion_if_contains_resources = false
    }
  }

  storage_use_azuread = true
  subscription_id     = var.subscription_id
}

provider "azapi" {
  subscription_id = var.subscription_id
}

provider "azuread" {
  tenant_id = data.azurerm_client_config.current.tenant_id
}

data "azurerm_client_config" "current" {}
