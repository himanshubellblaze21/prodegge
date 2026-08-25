output "recordings_bucket_name" {
  description = "Recordings S3 bucket name"
  value       = aws_s3_bucket.recordings.id
}

output "recordings_bucket_arn" {
  description = "Recordings S3 bucket ARN"
  value       = aws_s3_bucket.recordings.arn
}

output "transcripts_bucket_name" {
  description = "Transcripts S3 bucket name"
  value       = aws_s3_bucket.transcripts.id
}

output "transcripts_bucket_arn" {
  description = "Transcripts S3 bucket ARN"
  value       = aws_s3_bucket.transcripts.arn
}

output "reports_bucket_name" {
  description = "Reports S3 bucket name"
  value       = aws_s3_bucket.reports.id
}

output "reports_bucket_arn" {
  description = "Reports S3 bucket ARN"
  value       = aws_s3_bucket.reports.arn
}

output "feedback_bucket_name" {
  description = "Feedback S3 bucket name"
  value       = aws_s3_bucket.feedback.id
}

output "feedback_bucket_arn" {
  description = "Feedback S3 bucket ARN"
  value       = aws_s3_bucket.feedback.arn
}

output "frontend_bucket_name" {
  description = "Frontend S3 bucket name"
  value       = aws_s3_bucket.frontend.id
}

output "frontend_bucket_arn" {
  description = "Frontend S3 bucket ARN"
  value       = aws_s3_bucket.frontend.arn
}

output "dynamodb_table_name" {
  description = "DynamoDB table name"
  value       = aws_dynamodb_table.evaluations.name
}

output "dynamodb_table_arn" {
  description = "DynamoDB table ARN"
  value       = aws_dynamodb_table.evaluations.arn
}

output "recording_upload_event_rule_name" {
  description = "EventBridge rule name for recording uploads"
  value       = aws_cloudwatch_event_rule.recording_upload.name
}
