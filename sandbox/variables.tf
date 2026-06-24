variable "tenant_id" {
  description = "Azure AD tenant ID"
  type        = string
}

variable "subscription_id" {
  description = "Azure subscription ID (the $200 credit sub)"
  type        = string
}

variable "location" {
  description = "Azure region"
  type        = string
  default     = "eastus"
}

variable "environment" {
  description = "Environment label"
  type        = string
  default     = "sandbox"
}

variable "app_name" {
  description = "Short application name — used in resource naming"
  type        = string
  default     = "dftest"
}

locals {
  prefix = "${var.app_name}-${var.environment}"
  tags = {
    "managed-by"  = "dataforge"
    "environment" = var.environment
    "purpose"     = "phase1-sandbox-test"
  }
}
