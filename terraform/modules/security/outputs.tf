output "kms_key_id" {
  description = "KMS key ID"
  value       = aws_kms_key.main.key_id
}

output "kms_key_arn" {
  description = "KMS key ARN"
  value       = aws_kms_key.main.arn
}

output "lambda_execution_role_arn" {
  description = "Lambda execution role ARN"
  value       = aws_iam_role.lambda_execution.arn
}

output "lambda_execution_role_name" {
  description = "Lambda execution role name"
  value       = aws_iam_role.lambda_execution.name
}

output "step_functions_role_arn" {
  description = "Step Functions execution role ARN"
  value       = aws_iam_role.step_functions.arn
}

output "whisper_instance_profile_name" {
  description = "Whisper EC2 instance profile name"
  value       = aws_iam_instance_profile.whisper_instance.name
}

output "whisper_instance_role_arn" {
  description = "Whisper EC2 instance role ARN"
  value       = aws_iam_role.whisper_instance.arn
}

output "eventbridge_role_arn" {
  description = "EventBridge role ARN"
  value       = aws_iam_role.eventbridge.arn
}

output "api_config_secret_arn" {
  description = "API configuration secret ARN"
  value       = aws_secretsmanager_secret.api_config.arn
}
