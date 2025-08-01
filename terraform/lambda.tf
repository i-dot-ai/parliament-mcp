module "parliament_mcp_ingest_lambda" {
  source = "git::https://github.com/i-dot-ai/i-dot-ai-core-terraform-modules.git//modules/infrastructure/lambda?ref=v2.0.1-lambda"

  image_uri = "${data.aws_caller_identity.current.account_id}.dkr.ecr.${var.region}.amazonaws.com/parliament-mcp-lambda:${var.image_tag}"
  image_config = {
    working_directory : "",
  }
  lambda_additional_policy_arns  = { for idx, arn in [aws_iam_policy.parliament_mcp_secrets_manager.arn] : idx => arn }
  package_type                   = "Image"
  function_name                  = "${local.name}-parliament-mcp-ingest"
  timeout                        = 900
  memory_size                    = 1024
  aws_security_group_ids         = [aws_security_group.parliament_mcp_security_group.id]
  subnet_ids                     = data.terraform_remote_state.vpc.outputs.private_subnets
  account_id                     = data.aws_caller_identity.current.account_id
  reserved_concurrent_executions = -1
  permissions_boundary_name      = "infra/${local.name}-perms-boundary-app"
  # Only schedule in prod, disabled in dev
  schedule                       = terraform.workspace == "prod" ? "cron(0 5 ? * * *)" : null

  environment_variables = {
    APP_NAME = "${local.name}-parliament-mcp-ingest"
    ENVIRONMENT = terraform.workspace
    PROJECT_NAME = local.name
  }
}

data "aws_iam_policy_document" "parliament_mcp_secrets_manager" {
  statement {
    effect = "Allow"
    actions = [
      "secretsmanager:GetSecretValue"
    ]
    resources = [
      "arn:aws:secretsmanager:${var.region}:${data.aws_caller_identity.current.account_id}:secret:i-dot-ai-${terraform.workspace}-parliament-mcp-environment-variables-*"
    ]
  }

  statement {
    effect = "Allow"
    actions = [
      "ssm:GetParameter",
      "ssm:GetParameters",
      "ssm:GetParametersByPath"
    ]
    resources = [
      "arn:aws:ssm:*:${data.aws_caller_identity.current.account_id}:parameter/${local.name}/env_secrets/*"
    ]
  }

  statement {
    effect = "Allow"
    actions = [
      "kms:Decrypt",
      "kms:GenerateDataKey",
      "kms:DescribeKey",
    ]
    resources = [
      data.terraform_remote_state.platform.outputs.kms_key_arn,
    ]
  }
}

resource "aws_iam_policy" "parliament_mcp_secrets_manager" {
  name   = "${local.name}-secrets-ssm-access-policy"
  policy = data.aws_iam_policy_document.parliament_mcp_secrets_manager.json
}

resource "aws_security_group" "parliament_mcp_security_group" {
  vpc_id      = data.terraform_remote_state.vpc.outputs.vpc_id
  description = "${local.name} parliament mcp lambda SG"
  name        = "${local.name}-parliament-mcp-lambda-sg"
  lifecycle {
    create_before_destroy = true
  }
}

resource "aws_security_group_rule" "parliament_mcp_ingest_lambda_to_443_egress" {
  type              = "egress"
  from_port         = 443
  to_port           = 443
  protocol          = "tcp"
  cidr_blocks       = ["0.0.0.0/0"]
  ipv6_cidr_blocks  = ["::/0"]
  security_group_id = aws_security_group.parliament_mcp_security_group.id
}

resource "aws_security_group_rule" "parliament_mcp_ingest_lambda_to_qdrant_egress" {
  type              = "egress"
  from_port         = 6333
  to_port           = 6333
  protocol          = "tcp"
  cidr_blocks       = ["0.0.0.0/0"]
  ipv6_cidr_blocks  = ["::/0"]
  security_group_id = aws_security_group.parliament_mcp_security_group.id
}
