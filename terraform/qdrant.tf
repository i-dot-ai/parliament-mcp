locals {
  qdrant_port = 6333

  is_production       = terraform.workspace == "prod"
  domain_name_prod    = "qdrant-${var.project_name}.${var.domain_name}"
  domain_name_nonprod = "qdrant-${var.project_name}.${terraform.workspace}.${var.domain_name}"
  host_qdrant         = local.is_production ? local.domain_name_prod : local.domain_name_nonprod
}

# Qdrant service using the same ECS module pattern as backend
module "qdrant" {
  name = "${local.name}-qdrant"
  # checkov:skip=CKV_SECRET_4:Skip secret check as these have to be used within the Github Action
  # checkov:skip=CKV_TF_1: We're using semantic versions instead of commit hash
  #source                      = "../../i-dot-ai-core-terraform-modules//modules/infrastructure/ecs" # For testing local changes
  source                       = "git::https://github.com/i-dot-ai/i-dot-ai-core-terraform-modules.git//modules/infrastructure/ecs?ref=v5.8.0-ecs"

  # Using public Qdrant image - no ECR repository needed
  image_tag                    = "latest"
  ecr_repository_uri           = "qdrant/qdrant"

  vpc_id                       = data.terraform_remote_state.vpc.outputs.vpc_id
  private_subnets              = data.terraform_remote_state.vpc.outputs.private_subnets
  host                         = local.host_qdrant
  load_balancer_security_group = module.load_balancer.load_balancer_security_group_id
  aws_lb_arn                   = module.load_balancer.alb_arn
  ecs_cluster_id               = data.terraform_remote_state.platform.outputs.ecs_cluster_id
  ecs_cluster_name             = data.terraform_remote_state.platform.outputs.ecs_cluster_name
  create_listener              = true
  certificate_arn              = data.terraform_remote_state.universal.outputs.certificate_arn
  target_group_name_override   = "parliament-mcp-qdrant-${var.env}-tg"
  permissions_boundary_name    = "infra/i-dot-ai-${var.env}-parliament-mcp-perms-boundary-app"
  container_port               = local.qdrant_port

  # Resource allocation for Qdrant - sized for millions of documents
  memory = 16384
  cpu    = 4096

  # Scaled to zero - using hosted Qdrant Cloud instead
  desired_app_count          = 0
  autoscaling_minimum_target = 0
  autoscaling_maximum_target = 0

  environment_variables = {
    "QDRANT__LOG_LEVEL" = terraform.workspace == "prod" ? "warn" : "info"
    "QDRANT__SERVICE__HTTP_PORT" = tostring(local.qdrant_port)

    # Storage optimizers for millions of vectors
    "QDRANT__STORAGE__OPTIMIZERS__MEMMAP_THRESHOLD_KB" = "100000"  # Enable memmap for segments >100MB
    "QDRANT__STORAGE__OPTIMIZERS__INDEXING_THRESHOLD_KB" = "100000"  # Index segments >100MB
    "QDRANT__STORAGE__OPTIMIZERS__MAX_SEGMENT_SIZE_KB" = "5000000"  # 5GB max segment size
    "QDRANT__STORAGE__OPTIMIZERS__MAX_OPTIMIZATION_THREADS" = "4"  # Optimization parallelism

    # HNSW tuning for large scale (storage-level defaults)
    "QDRANT__STORAGE__HNSW_INDEX__M" = "32"  # Increased for better recall at scale
    "QDRANT__STORAGE__HNSW_INDEX__EF_CONSTRUCT" = "400"  # Higher for better index quality
    "QDRANT__STORAGE__HNSW_INDEX__FULL_SCAN_THRESHOLD_KB" = "50000"  # Increased threshold (KB)
    "QDRANT__STORAGE__HNSW_INDEX__MAX_INDEXING_THREADS" = "4"  # Parallel indexing threads

    # Performance tuning for large scale
    "QDRANT__PERFORMANCE__MAX_SEARCH_THREADS" = "4"  # Search parallelism

    # Memory management
    "QDRANT__SERVICE__MAX_REQUEST_SIZE_MB" = "64"  # 64MB for batch operations
  }

  efs_mount_configuration = [
    {
      file_system_id  = aws_efs_file_system.qdrant.id
      container_path  = "/qdrant/storage"
      access_point_id = aws_efs_access_point.qdrant.id
    }
  ]

  health_check = {
    accepted_response   = 200
    path                = "/readyz"
    interval            = 30
    timeout             = 5
    healthy_threshold   = 2
    unhealthy_threshold = 3
    port                = local.qdrant_port
  }
}

module "qdrant-ecs-alarm" {
  # checkov:skip=CKV_TF_1: We're using semantic versions instead of commit hash
  source                       = "git::https://github.com/i-dot-ai/i-dot-ai-core-terraform-modules.git//modules/observability/ecs-alarms?ref=v1.0.1-ecs-alarms"
  name                         = "${local.name}-qdrant"
  ecs_service_name             = module.qdrant.ecs_service_name
  ecs_cluster_name             = data.terraform_remote_state.platform.outputs.ecs_cluster_name
  sns_topic_arn                = [module.sns_topic.sns_topic_arn]
}

module "qdrant-alb-alarm" {
  # checkov:skip=CKV_TF_1: We're using semantic versions instead of commit hash
  source                       = "git::https://github.com/i-dot-ai/i-dot-ai-core-terraform-modules.git//modules/observability/alb-alarms?ref=v1.0.0-alb-alarms"
  name                         = "${local.name}-qdrant"
  alb_arn                      = module.load_balancer.alb_arn
  target_group                 = module.qdrant.aws_lb_target_group_name
  sns_topic_arn                = [module.sns_topic.sns_topic_arn]
}

# Allow backend to connect to Qdrant internally
resource "aws_security_group_rule" "backend_to_qdrant" {
  type                     = "ingress"
  from_port                = local.qdrant_port
  to_port                  = local.qdrant_port
  protocol                 = "tcp"
  source_security_group_id = module.backend.ecs_sg_id
  security_group_id        = module.qdrant.ecs_sg_id
  description              = "Allow backend to connect to Qdrant internally"
}
