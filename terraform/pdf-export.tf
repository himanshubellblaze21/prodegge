# PDF Export Lambda — renders the Excel scorecard (both sheets) to PDF.
# GET /evaluations/{evaluation_id}/pdf
#
# Packaged by create_pdf_package.py (Linux wheels for numpy / uharfbuzz), so
# Terraform uploads that zip instead of zipping the source directory.

resource "aws_lambda_function" "pdf_export" {
  filename         = "${path.module}/../lambda/pdf-export/deployment-package.zip"
  function_name    = "${var.project_name}-pdf-export-${var.environment}"
  role             = aws_iam_role.lambda_role.arn
  handler          = "handler.lambda_handler"
  runtime          = "python3.11"
  timeout          = 29 # API Gateway gives up at 30s
  memory_size      = 1024
  source_code_hash = filebase64sha256("${path.module}/../lambda/pdf-export/deployment-package.zip")

  environment {
    variables = {
      REPORTS_BUCKET = aws_s3_bucket.reports.id
      DYNAMODB_TABLE = aws_dynamodb_table.evaluations.name
    }
  }

  tags = var.tags
}

resource "aws_cloudwatch_log_group" "pdf_export" {
  name              = "/aws/lambda/${aws_lambda_function.pdf_export.function_name}"
  retention_in_days = 7
}

resource "aws_apigatewayv2_integration" "pdf_export" {
  api_id                 = aws_apigatewayv2_api.main.id
  integration_type       = "AWS_PROXY"
  integration_uri        = aws_lambda_function.pdf_export.invoke_arn
  payload_format_version = "1.0"
}

resource "aws_apigatewayv2_route" "get_pdf" {
  api_id    = aws_apigatewayv2_api.main.id
  route_key = "GET /evaluations/{evaluation_id}/pdf"
  target    = "integrations/${aws_apigatewayv2_integration.pdf_export.id}"
}

resource "aws_lambda_permission" "api_gateway_pdf_export" {
  statement_id  = "AllowAPIGatewayInvoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.pdf_export.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_apigatewayv2_api.main.execution_arn}/*/*"
}
