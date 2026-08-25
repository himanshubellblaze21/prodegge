output "api_endpoint" {
  description = "API Gateway endpoint URL — paste this into frontend/.env as VITE_API_ENDPOINT"
  value       = aws_apigatewayv2_api.main.api_endpoint
}

output "frontend_env" {
  description = "Copy-paste this block into frontend/.env"
  value       = <<-EOT
    VITE_AWS_REGION=${var.aws_region}
    VITE_API_ENDPOINT=${aws_apigatewayv2_api.main.api_endpoint}
    VITE_DEBUG=false
  EOT
}

output "recordings_bucket" {
  description = "S3 bucket for recordings"
  value       = aws_s3_bucket.recordings.id
}

output "transcripts_bucket" {
  description = "S3 bucket for transcripts"
  value       = aws_s3_bucket.transcripts.id
}

output "reports_bucket" {
  description = "S3 bucket for reports"
  value       = aws_s3_bucket.reports.id
}

output "dynamodb_table" {
  description = "DynamoDB table name"
  value       = aws_dynamodb_table.evaluations.id
}

output "lambda_functions" {
  description = "Lambda function names"
  value = {
    ingestion      = aws_lambda_function.ingestion.function_name
    preprocessing  = aws_lambda_function.preprocessing.function_name
    transcription  = aws_lambda_function.transcription.function_name
    evaluation     = aws_lambda_function.evaluation.function_name
    excel_generator = aws_lambda_function.excel_generator.function_name
  }
}
