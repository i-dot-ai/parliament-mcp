module "load_balancer" {
  # checkov:skip=CKV_TF_1: We're using semantic versions instead of commit hash
  #source           = "../../i-dot-ai-core-terraform-modules//modules/infrastructure/load_balancer" # For testing local changes
  source            = "git::https://github.com/i-dot-ai/i-dot-ai-core-terraform-modules.git//modules/infrastructure/load_balancer?ref=v2.0.1-load_balancer"
  name              = local.name
  account_id        = data.aws_caller_identity.current.account_id
  vpc_id            = data.terraform_remote_state.vpc.outputs.vpc_id
  public_subnets    = data.terraform_remote_state.vpc.outputs.public_subnets
  certificate_arn   = data.terraform_remote_state.universal.outputs.certificate_arn
  web_acl_arn       = module.waf.web_acl_arn
  env               = var.env
  alb_name_override = "${var.env}-parliament-mcp-alb"
}

module "waf" {
  # checkov:skip=CKV_TF_1: We're using semantic versions instead of commit hash
  #source        = "../../i-dot-ai-core-terraform-modules//modules/infrastructure/waf" # For testing local changes
  source         = "git::https://github.com/i-dot-ai/i-dot-ai-core-terraform-modules.git//modules/infrastructure/waf?ref=v7.0.0-waf"
  name           = local.name
  host           = local.host
  env            = var.env

  header_secured_access_configuration = {
    kms_key_id = data.terraform_remote_state.platform.outputs.kms_key_arn
    hostname = local.host_backend
    client_configs = [
      {
        client_name = "iai_devs",
      },
    ]
  }
}




resource "aws_route53_record" "type_a_record_backend" {
  zone_id = data.terraform_remote_state.account.outputs.hosted_zone_id
  name    = local.host_backend
  type    = "A"

  alias {
    name                   = module.load_balancer.load_balancer_dns_name
    zone_id                = module.load_balancer.load_balancer_zone_id
    evaluate_target_health = true
  }
}
