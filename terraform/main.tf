terraform {
  required_version = ">= 1.0"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
    archive = {
      source  = "hashicorp/archive"
      version = "~> 2.4"
    }
  }
}

provider "aws" {
  region = var.aws_region
}

# S3 Buckets
resource "aws_s3_bucket" "recordings" {
  bucket = "${var.project_name}-recordings-${var.environment}"
  tags   = var.tags
}

resource "aws_s3_bucket" "transcripts" {
  bucket = "${var.project_name}-transcripts-${var.environment}"
  tags   = var.tags
}

resource "aws_s3_bucket" "reports" {
  bucket = "${var.project_name}-reports-${var.environment}"
  tags   = var.tags
}

# CORS Configuration for Recordings Bucket (allow direct uploads from frontend)
resource "aws_s3_bucket_cors_configuration" "recordings" {
  bucket = aws_s3_bucket.recordings.id

  cors_rule {
    allowed_headers = ["*"]
    allowed_methods = ["PUT", "POST", "GET", "HEAD"]
    allowed_origins = ["*"]
    expose_headers  = ["ETag"]
    max_age_seconds = 3000
  }
}

# Upload Excel Template
resource "aws_s3_object" "excel_template" {
  bucket       = aws_s3_bucket.reports.id
  key          = "templates/prodegee_template.xlsx"
  source       = "${path.module}/../.vscode/template/Prodigee_Template.xlsx"
  content_type = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
  etag         = filemd5("${path.module}/../.vscode/template/Prodigee_Template.xlsx")
}

# Upload Rubric
resource "aws_s3_object" "rubric" {
  bucket       = aws_s3_bucket.reports.id
  key          = "config/rcm-audio-pd-rubric.json"
  source       = "${path.module}/../config/rcm-audio-pd-rubric.json"
  content_type = "application/json"
  etag         = filemd5("${path.module}/../config/rcm-audio-pd-rubric.json")
}

# Upload System Prompt
resource "aws_s3_object" "system_prompt" {
  bucket       = aws_s3_bucket.reports.id
  key          = "config/system-prompt.txt"
  source       = "${path.module}/../evaluation-agent/system-prompt.txt"
  content_type = "text/plain"
  etag         = filemd5("${path.module}/../evaluation-agent/system-prompt.txt")
}

# DynamoDB Table
resource "aws_dynamodb_table" "evaluations" {
  name         = "${var.project_name}-evaluations-${var.environment}"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "evaluation_id"
  range_key    = "created_at"

  attribute {
    name = "evaluation_id"
    type = "S"
  }

  attribute {
    name = "created_at"
    type = "S"
  }

  tags = var.tags
}

# IAM Role for Lambda
resource "aws_iam_role" "lambda_role" {
  name = "${var.project_name}-lambda-role-${var.environment}"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Action = "sts:AssumeRole"
        Effect = "Allow"
        Principal = {
          Service = "lambda.amazonaws.com"
        }
      }
    ]
  })

  tags = var.tags
}

# IAM Policy for Lambda
resource "aws_iam_role_policy" "lambda_policy" {
  name = "${var.project_name}-lambda-policy-${var.environment}"
  role = aws_iam_role.lambda_role.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "s3:*"
        ]
        Resource = [
          aws_s3_bucket.recordings.arn,
          "${aws_s3_bucket.recordings.arn}/*",
          aws_s3_bucket.transcripts.arn,
          "${aws_s3_bucket.transcripts.arn}/*",
          aws_s3_bucket.reports.arn,
          "${aws_s3_bucket.reports.arn}/*"
        ]
      },
      {
        Effect = "Allow"
        Action = [
          "dynamodb:*"
        ]
        Resource = aws_dynamodb_table.evaluations.arn
      },
      {
        Effect = "Allow"
        Action = [
          "transcribe:*"
        ]
        Resource = "*"
      },
      {
        Effect = "Allow"
        Action = [
          "bedrock:InvokeModel"
        ]
        Resource = "*"
      },
      {
        Effect = "Allow"
        Action = [
          "lambda:InvokeFunction"
        ]
        Resource = "*"
      },
      {
        Effect = "Allow"
        Action = [
          "logs:CreateLogGroup",
          "logs:CreateLogStream",
          "logs:PutLogEvents"
        ]
        Resource = "arn:aws:logs:*:*:*"
      }
    ]
  })
}

# Package Lambda Functions
data "archive_file" "preprocessing_lambda" {
  type        = "zip"
  source_dir  = "${path.module}/../lambda/preprocessing"
  output_path = "${path.module}/packages/preprocessing-lambda.zip"
  excludes    = ["__pycache__", "*.pyc"]
}

data "archive_file" "transcription_lambda" {
  type        = "zip"
  source_dir  = "${path.module}/../lambda/transcription"
  output_path = "${path.module}/packages/transcription-lambda.zip"
  excludes    = ["__pycache__", "*.pyc"]
}

data "archive_file" "evaluation_lambda" {
  type        = "zip"
  source_dir  = "${path.module}/../lambda/evaluation"
  output_path = "${path.module}/packages/evaluation-lambda.zip"
  excludes    = ["__pycache__", "*.pyc"]
}

data "archive_file" "excel_generator_lambda" {
  type        = "zip"
  source_dir  = "${path.module}/../lambda/excel-generator"
  output_path = "${path.module}/packages/excel-generator-lambda.zip"
  excludes    = ["__pycache__", "*.pyc"]
}

data "archive_file" "ingestion_lambda" {
  type        = "zip"
  source_dir  = "${path.module}/../lambda/ingestion"
  output_path = "${path.module}/packages/ingestion-lambda.zip"
  excludes    = ["__pycache__", "*.pyc"]
}

data "archive_file" "api_lambda" {
  type        = "zip"
  source_dir  = "${path.module}/../lambda/api"
  output_path = "${path.module}/packages/api-lambda.zip"
  excludes    = ["__pycache__", "*.pyc", "package.zip"]
}

# Lambda Functions
resource "aws_lambda_function" "preprocessing" {
  filename         = data.archive_file.preprocessing_lambda.output_path
  function_name    = "${var.project_name}-preprocessing-${var.environment}"
  role            = aws_iam_role.lambda_role.arn
  handler         = "handler.lambda_handler"
  runtime         = "python3.11"
  timeout         = 300
  memory_size     = 512
  source_code_hash = data.archive_file.preprocessing_lambda.output_base64sha256

  environment {
    variables = {
      RECORDINGS_BUCKET        = aws_s3_bucket.recordings.id
      DYNAMODB_TABLE           = aws_dynamodb_table.evaluations.id
      TRANSCRIPTION_LAMBDA_NAME = "${var.project_name}-transcription-${var.environment}"
    }
  }

  tags = var.tags
}

resource "aws_lambda_function" "transcription" {
  filename         = data.archive_file.transcription_lambda.output_path
  function_name    = "${var.project_name}-transcription-${var.environment}"
  role            = aws_iam_role.lambda_role.arn
  handler         = "handler.lambda_handler"
  runtime         = "python3.11"
  timeout         = 900
  memory_size     = 1024
  source_code_hash = data.archive_file.transcription_lambda.output_base64sha256

  environment {
    variables = {
      RECORDINGS_BUCKET      = aws_s3_bucket.recordings.id
      TRANSCRIPTS_BUCKET     = aws_s3_bucket.transcripts.id
      DYNAMODB_TABLE         = aws_dynamodb_table.evaluations.id
      EVALUATION_LAMBDA_NAME = "${var.project_name}-evaluation-${var.environment}"
    }
  }

  tags = var.tags
}

resource "aws_lambda_function" "evaluation" {
  filename         = data.archive_file.evaluation_lambda.output_path
  function_name    = "${var.project_name}-evaluation-${var.environment}"
  role            = aws_iam_role.lambda_role.arn
  handler         = "handler.lambda_handler"
  runtime         = "python3.11"
  timeout         = 900
  memory_size     = 2048
  source_code_hash = data.archive_file.evaluation_lambda.output_base64sha256

  environment {
    variables = {
      TRANSCRIPTS_BUCKET        = aws_s3_bucket.transcripts.id
      REPORTS_BUCKET            = aws_s3_bucket.reports.id
      DYNAMODB_TABLE            = aws_dynamodb_table.evaluations.id
      BEDROCK_MODEL_ID          = "apac.anthropic.claude-3-5-sonnet-20240620-v1:0"
      EXCEL_GENERATOR_LAMBDA_ARN = aws_lambda_function.excel_generator.arn
    }
  }

  tags = var.tags
}

resource "aws_lambda_function" "excel_generator" {
  filename         = data.archive_file.excel_generator_lambda.output_path
  function_name    = "${var.project_name}-excel-generator-${var.environment}"
  role            = aws_iam_role.lambda_role.arn
  handler         = "handler.lambda_handler"
  runtime         = "python3.11"
  timeout         = 300
  memory_size     = 512
  source_code_hash = data.archive_file.excel_generator_lambda.output_base64sha256

  environment {
    variables = {
      REPORTS_BUCKET  = aws_s3_bucket.reports.id
      TEMPLATE_BUCKET = aws_s3_bucket.reports.id
      TEMPLATE_KEY    = "templates/prodegee_template.xlsx"
      DYNAMODB_TABLE  = aws_dynamodb_table.evaluations.id
    }
  }

  tags = var.tags
}

resource "aws_lambda_function" "ingestion" {
  filename         = data.archive_file.ingestion_lambda.output_path
  function_name    = "${var.project_name}-ingestion-${var.environment}"
  role            = aws_iam_role.lambda_role.arn
  handler         = "handler.generate_presigned_url"
  runtime         = "python3.11"
  timeout         = 300
  memory_size     = 512
  source_code_hash = data.archive_file.ingestion_lambda.output_base64sha256

  environment {
    variables = {
      RECORDINGS_BUCKET = aws_s3_bucket.recordings.id
      DYNAMODB_TABLE    = aws_dynamodb_table.evaluations.id
    }
  }

  tags = var.tags
}

# API Gateway
resource "aws_apigatewayv2_api" "main" {
  name          = "${var.project_name}-api-${var.environment}"
  protocol_type = "HTTP"
  
  cors_configuration {
    allow_origins = ["*"]
    allow_methods = ["GET", "POST", "PUT", "DELETE", "OPTIONS"]
    allow_headers = ["*"]
    max_age       = 300
  }

  tags = var.tags
}

resource "aws_apigatewayv2_stage" "default" {
  api_id      = aws_apigatewayv2_api.main.id
  name        = "$default"
  auto_deploy = true
}

# API Gateway Integration - Upload
resource "aws_apigatewayv2_integration" "upload" {
  api_id           = aws_apigatewayv2_api.main.id
  integration_type = "AWS_PROXY"
  integration_uri  = aws_lambda_function.ingestion.invoke_arn
}

resource "aws_apigatewayv2_route" "upload" {
  api_id    = aws_apigatewayv2_api.main.id
  route_key = "POST /upload/presigned"
  target    = "integrations/${aws_apigatewayv2_integration.upload.id}"
}

resource "aws_lambda_permission" "api_gateway_upload" {
  statement_id  = "AllowAPIGatewayInvoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.ingestion.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_apigatewayv2_api.main.execution_arn}/*/*"
}

# S3 Event Notification to trigger preprocessing Lambda when file is uploaded
resource "aws_s3_bucket_notification" "recording_upload" {
  bucket = aws_s3_bucket.recordings.id

  lambda_function {
    lambda_function_arn = aws_lambda_function.preprocessing.arn
    events              = ["s3:ObjectCreated:*"]
    filter_prefix       = "recordings/"
  }

  depends_on = [aws_lambda_permission.s3_invoke_preprocessing]
}

resource "aws_lambda_permission" "s3_invoke_preprocessing" {
  statement_id  = "AllowS3Invoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.preprocessing.function_name
  principal     = "s3.amazonaws.com"
  source_arn    = aws_s3_bucket.recordings.arn
}
