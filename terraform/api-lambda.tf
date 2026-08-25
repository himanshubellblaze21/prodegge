# API Lambda Function — handles all REST endpoints (status, transcript, excel, history)

resource "aws_lambda_function" "api" {
  filename         = data.archive_file.api_lambda.output_path
  function_name    = "${var.project_name}-api-${var.environment}"
  role             = aws_iam_role.lambda_role.arn
  handler          = "handler.lambda_handler"
  runtime          = "python3.11"
  timeout          = 30
  memory_size      = 512
  source_code_hash = data.archive_file.api_lambda.output_base64sha256

  environment {
    variables = {
      TRANSCRIPTS_BUCKET       = aws_s3_bucket.transcripts.id
      REPORTS_BUCKET           = aws_s3_bucket.reports.id
      RECORDINGS_BUCKET        = aws_s3_bucket.recordings.id
      DYNAMODB_TABLE           = aws_dynamodb_table.evaluations.name
      TRANSCRIPTION_LAMBDA_ARN = aws_lambda_function.transcription.arn
      EVALUATION_LAMBDA_NAME   = aws_lambda_function.evaluation.function_name
    }
  }

  tags = var.tags
}

# CloudWatch Log Group for API Lambda
resource "aws_cloudwatch_log_group" "api" {
  name              = "/aws/lambda/${aws_lambda_function.api.function_name}"
  retention_in_days = 7
}

# API Gateway Integration for API Lambda
resource "aws_apigatewayv2_integration" "api" {
  api_id           = aws_apigatewayv2_api.main.id
  integration_type = "AWS_PROXY"
  integration_uri  = aws_lambda_function.api.invoke_arn
}

# Routes

resource "aws_apigatewayv2_route" "upload_complete" {
  api_id    = aws_apigatewayv2_api.main.id
  route_key = "POST /upload/complete"
  target    = "integrations/${aws_apigatewayv2_integration.api.id}"
}

resource "aws_apigatewayv2_route" "get_status" {
  api_id    = aws_apigatewayv2_api.main.id
  route_key = "GET /evaluations/{evaluation_id}/status"
  target    = "integrations/${aws_apigatewayv2_integration.api.id}"
}

resource "aws_apigatewayv2_route" "get_transcript" {
  api_id    = aws_apigatewayv2_api.main.id
  route_key = "GET /evaluations/{evaluation_id}/transcript"
  target    = "integrations/${aws_apigatewayv2_integration.api.id}"
}

resource "aws_apigatewayv2_route" "get_excel" {
  api_id    = aws_apigatewayv2_api.main.id
  route_key = "GET /evaluations/{evaluation_id}/excel"
  target    = "integrations/${aws_apigatewayv2_integration.api.id}"
}

resource "aws_apigatewayv2_route" "get_evaluation" {
  api_id    = aws_apigatewayv2_api.main.id
  route_key = "GET /evaluations/{evaluation_id}"
  target    = "integrations/${aws_apigatewayv2_integration.api.id}"
}

resource "aws_apigatewayv2_route" "list_evaluations" {
  api_id    = aws_apigatewayv2_api.main.id
  route_key = "GET /evaluations"
  target    = "integrations/${aws_apigatewayv2_integration.api.id}"
}

# Lambda permission for API Gateway
resource "aws_lambda_permission" "api_gateway_api" {
  statement_id  = "AllowAPIGatewayInvoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.api.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_apigatewayv2_api.main.execution_arn}/*/*"
}
