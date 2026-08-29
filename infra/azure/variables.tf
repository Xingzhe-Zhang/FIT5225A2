variable "azure_subscription_id" {
  description = "Azure subscription selected by the human deployer."
  type        = string
  sensitive   = true
}

variable "project_name" {
  type    = string
  default = "pacific-bioarchive"
}

variable "environment" {
  type    = string
  default = "development"
}

variable "frontend_origins" {
  description = "Browser origins allowed to call the Azure Function."
  type        = list(string)
  default     = ["http://localhost:5173"]

  validation {
    condition     = length(var.frontend_origins) > 0 && alltrue([for origin in var.frontend_origins : can(regex("^https?://[^/]+$", origin))])
    error_message = "frontend_origins must contain at least one origin such as https://app.example.com."
  }
}

variable "azure_location" {
  type    = string
  default = "australiaeast"
}

variable "resource_group_name" {
  type     = string
  default  = null
  nullable = true
}

variable "unique_suffix" {
  description = "Short lowercase suffix making globally named Azure resources unique."
  type        = string

  validation {
    condition     = can(regex("^[a-z0-9]{4,10}$", var.unique_suffix))
    error_message = "unique_suffix must contain 4-10 lowercase letters or digits."
  }
}

variable "cognito_issuer" {
  type = string
}

variable "cognito_app_client_id" {
  type = string
}

variable "aws_media_bucket" {
  description = "AWS S3 media bucket whose signed thumbnail URLs are accepted by the query API."
  type        = string
}

variable "worker_principal_id" {
  description = "Optional Azure service-principal object ID used by the AWS media worker."
  type        = string
  default     = null
  nullable    = true

  validation {
    condition     = var.worker_principal_id == null || can(regex("^[0-9a-fA-F-]{36}$", var.worker_principal_id))
    error_message = "worker_principal_id must be an Azure service-principal object ID."
  }
}

variable "api_principal_id" {
  description = "Optional Azure service-principal object ID used only by the AWS API Lambda."
  type        = string
  default     = null
  nullable    = true

  validation {
    condition     = var.api_principal_id == null || can(regex("^[0-9a-fA-F-]{36}$", var.api_principal_id))
    error_message = "api_principal_id must be an Azure service-principal object ID."
  }
}

variable "notification_principal_id" {
  description = "Optional Azure service-principal object ID used only by the AWS notification bridge."
  type        = string
  default     = null
  nullable    = true

  validation {
    condition     = var.notification_principal_id == null || can(regex("^[0-9a-fA-F-]{36}$", var.notification_principal_id))
    error_message = "notification_principal_id must be an Azure service-principal object ID."
  }
}

variable "log_retention_days" {
  type    = number
  default = 30
}
