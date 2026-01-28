locals {

  backend_port  = 8080


  additional_policy_arns = {for idx, arn in [aws_iam_policy.ecs_exec_custom_policy.arn] : idx => arn}
}


module "backend" {
  name = "${local.name}-backend"
  # checkov:skip=CKV_SECRET_4:Skip secret check as these have to be used within the Github Action
  # checkov:skip=CKV_TF_1: We're using semantic versions instead of commit hash
  #source                      = "../../i-dot-ai-core-terraform-modules//modules/infrastructure/ecs" # For testing local changes
  source                       = "git::https://github.com/i-dot-ai/i-dot-ai-core-terraform-modules.git//modules/infrastructure/ecs?ref=v5.8.0-ecs"
  image_tag                    = var.image_tag
  ecr_repository_uri           = "${data.aws_caller_identity.current.account_id}.dkr.ecr.${var.region}.amazonaws.com/parliament-mcp-mcp_server"
  vpc_id                       = data.terraform_remote_state.vpc.outputs.vpc_id
  private_subnets              = data.terraform_remote_state.vpc.outputs.private_subnets
  host                         = local.host_backend
  load_balancer_security_group = module.load_balancer.load_balancer_security_group_id
  aws_lb_arn                   = module.load_balancer.alb_arn
  ecs_cluster_id               = data.terraform_remote_state.platform.outputs.ecs_cluster_id
  ecs_cluster_name             = data.terraform_remote_state.platform.outputs.ecs_cluster_name
  task_additional_iam_policies = local.additional_policy_arns
  certificate_arn              = data.terraform_remote_state.universal.outputs.certificate_arn
  target_group_name_override   =  "parliament-mcp-be-${var.env}-tg"
  permissions_boundary_name    = "infra/i-dot-ai-${var.env}-parliament-mcp-perms-boundary-app"

  # Resource allocation - increased from defaults (256/512) for embedding model overhead
  memory = 2048
  cpu    = 512

  create_networking = true
  create_listener   = true


  environment_variables = {
    "ENVIRONMENT" : terraform.workspace,
    "APP_NAME" : "${local.name}-backend"
    "PORT" : local.backend_port,
    "REPO" : "parliament-mcp",
    "AWS_ACCOUNT_ID": data.aws_caller_identity.current.account_id,
    "DOCKER_BUILDER_CONTAINER": "parliament-mcp",
    "AUTH_PROVIDER_PUBLIC_KEY": data.aws_ssm_parameter.auth_provider_public_key.value,
    # Qdrant connection is via QDRANT_URL and QDRANT_API_KEY from SSM (hosted Qdrant Cloud)
    # MCP allowed hosts for DNS rebinding protection
    "MCP_ALLOWED_HOSTS": "localhost,127.0.0.1,${local.host_backend}"
  }

  secrets = [
    for k, v in aws_ssm_parameter.env_secrets : {
      name = regex("([^/]+$)", v.arn)[0], # Extract right-most string (param name) after the final slash
      valueFrom = v.arn
    }
  ]

  container_port             = local.backend_port
  # Limit to single instance to avoid session affinity issues with MCP
  # (MCP sessions are stored in-memory, so horizontal scaling breaks session continuity)
  desired_app_count          = 1
  autoscaling_minimum_target = 1
  autoscaling_maximum_target = 1

  health_check = {
    accepted_response   = 200
    path                = "/healthcheck"
    interval            = 60
    timeout             = 70
    healthy_threshold   = 2
    unhealthy_threshold = 5
    port                = local.backend_port
  }
}






module "sns_topic" {
  # checkov:skip=CKV_TF_1: We're using semantic versions instead of commit hash
  # source                       = "../../i-dot-ai-core-terraform-modules/modules/observability/cloudwatch-slack-integration"
  source                       = "git::https://github.com/i-dot-ai/i-dot-ai-core-terraform-modules.git//modules/observability/cloudwatch-slack-integration?ref=v2.0.1-cloudwatch-slack-integration"
  name                         = local.name
  slack_webhook                = data.aws_secretsmanager_secret_version.platform_slack_webhook.secret_string

  permissions_boundary_name    = "infra/i-dot-ai-${var.env}-parliament-mcp-perms-boundary-app"
}

module "backend-ecs-alarm" {
  # checkov:skip=CKV_TF_1: We're using semantic versions instead of commit hash
  # source                       = "../../i-dot-ai-core-terraform-modules/modules/observability/ecs-alarms"
  source                       = "git::https://github.com/i-dot-ai/i-dot-ai-core-terraform-modules.git//modules/observability/ecs-alarms?ref=v1.0.1-ecs-alarms"
  name                         = "${local.name}-backend"
  ecs_service_name             = module.backend.ecs_service_name
  ecs_cluster_name             = data.terraform_remote_state.platform.outputs.ecs_cluster_name
  sns_topic_arn                = [module.sns_topic.sns_topic_arn]

  period             = 120
  evaluation_periods = 3
}

module "backend-alb-alarm" {
  # checkov:skip=CKV_TF_1: We're using semantic versions instead of commit hash
  # source                       = "../../i-dot-ai-core-terraform-modules/modules/observability/alb-alarms"
  source                       = "git::https://github.com/i-dot-ai/i-dot-ai-core-terraform-modules.git//modules/observability/alb-alarms?ref=v1.0.0-alb-alarms"
  name                         = "${local.name}-backend"
  alb_arn                      = module.load_balancer.alb_arn
  target_group                 = module.backend.aws_lb_target_group_name
  sns_topic_arn                = [module.sns_topic.sns_topic_arn]
}
