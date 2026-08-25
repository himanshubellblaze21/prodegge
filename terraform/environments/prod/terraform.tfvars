environment         = "prod"
aws_region          = "ap-south-1"
vpc_cidr            = "10.2.0.0/16"
enable_nat_gateway  = true
enable_spot_instances = false # Use on-demand for production reliability

# Compute
whisper_instance_type = "g5.xlarge"

# Bedrock
bedrock_model_id = "anthropic.claude-3-haiku-20240307-v1:0"

# Monitoring
alert_email = "prod-alerts@bellblazetech.com"
log_retention_days = 90

# Budget
monthly_budget_usd = 2500

# Timeouts
transcription_timeout_seconds = 1800
evaluation_timeout_seconds = 300

# Security
enable_cloudtrail = true
enable_xray = true
enable_waf = true

# Backup
backup_retention_days = 90

# Restrict access in production
allowed_cidr_blocks = ["0.0.0.0/0"] # Update with actual client IP ranges
