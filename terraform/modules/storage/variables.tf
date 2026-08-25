variable "project_name" {
  description = "Project name"
  type        = string
}

variable "environment" {
  description = "Environment name"
  type        = string
}

variable "kms_key_arn" {
  description = "KMS key ARN for encryption"
  type        = string
}

variable "cloudfront_distribution_arn" {
  description = "CloudFront distribution ARN for frontend bucket policy"
  type        = string
  default     = ""
}
