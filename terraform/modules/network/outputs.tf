output "vpc_id" {
  description = "VPC ID"
  value       = aws_vpc.main.id
}

output "vpc_cidr" {
  description = "VPC CIDR block"
  value       = aws_vpc.main.cidr_block
}

output "public_subnet_ids" {
  description = "Public subnet IDs"
  value       = aws_subnet.public[*].id
}

output "private_subnet_ids" {
  description = "Private subnet IDs"
  value       = aws_subnet.private[*].id
}

output "lambda_security_group_id" {
  description = "Lambda security group ID"
  value       = aws_security_group.lambda.id
}

output "whisper_security_group_id" {
  description = "Whisper security group ID"
  value       = aws_security_group.whisper.id
}

output "nat_gateway_id" {
  description = "NAT gateway ID"
  value       = var.enable_nat_gateway ? aws_nat_gateway.main[0].id : null
}
