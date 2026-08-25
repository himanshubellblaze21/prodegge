environment         = "dev"
aws_region          = "ap-south-1"
vpc_cidr            = "10.0.0.0/16"
enable_nat_gateway  = true
enable_spot_instances = true

# Compute
whisper_instance_type = "g5.xlarge"

# Bedrock
bedrock_model_id = "anthropic.claude-3-haiku-20240307-v1:0"

# Monitoring
alert_email = "dev-alerts@bellblazetech.com"
log_retention_days = 7

# Budget
monthly_budget_usd = 1000

# Timeouts
transcription_timeout_seconds = 1800
evaluation_timeout_seconds = 300

# Security
enable_cloudtrail = true
enable_xray = true
enable_waf = false

# Backup
backup_retention_days = 30
