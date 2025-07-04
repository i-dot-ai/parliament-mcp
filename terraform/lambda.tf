module "parliament_mcp_ingest_lambda" {
  source = "git::https://github.com/i-dot-ai/i-dot-ai-core-terraform-modules.git//modules/infrastructure/lambda?ref=v1.2.0-lambda"

  image_uri = "${data.aws_caller_identity.current.account_id}.dkr.ecr.${var.region}.amazonaws.com/parliament-mcp-lambda:${var.image_tag}"
  image_config = {
    working_directory : "",
  }
  policies                       = [jsonencode(data.aws_iam_policy_document.parliament_mcp_secrets_manager.json)]
  package_type                   = "Image"
  function_name                  = "${local.name}-parliament-mcp-ingest"
  iam_role_name                  = "${local.name}-parliament-mcp-ingest-lambda-role"
  timeout                        = 900
  memory_size                    = 1024
  aws_security_group_ids         = [aws_security_group.parliament_mcp_security_group.id]
  subnet_ids                     = data.terraform_remote_state.vpc.outputs.private_subnets
  account_id                     = data.aws_caller_identity.current.account_id
  reserved_concurrent_executions = -1
  permissions_boundary_name      = "infra/${local.name}-perms-boundary-app"
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

resource "aws_scheduler_schedule" "parliament_mcp_ingest_schedule" {
  name = "${local.name}-parliament-mcp-ingest-schedule"
  flexible_time_window {
    mode = "OFF"
  }
  # Runs every day at 05:00 UTC
  schedule_expression = "cron(0 5 ? * * *)"
  target {
    arn      = module.parliament_mcp_ingest_lambda.arn
    role_arn = aws_iam_role.parliament_mcp_scheduler_role.arn

    input = jsonencode({
      from_date = null,
      to_date   = null
    })
  }
}

resource "aws_iam_role" "parliament_mcp_scheduler_role" {
  name = "${local.name}-parliament-mcp-scheduler-role"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Action = "sts:AssumeRole"
        Effect = "Allow"
        Principal = {
          Service = "scheduler.amazonaws.com"
        }
      }
    ]
  })
}

resource "aws_iam_role_policy" "parliament_mcp_scheduler_lambda_policy" {
  name = "${local.name}-parliament-mcp-scheduler-lambda-policy"
  role = aws_iam_role.parliament_mcp_scheduler_role.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "lambda:InvokeFunction"
        ]
        Resource = [
          module.parliament_mcp_ingest_lambda.arn
        ]
      }
    ]
  })
}
