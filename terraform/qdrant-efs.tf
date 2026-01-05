locals {
  qdrant_efs_name = "${var.team_name}-${terraform.workspace}-${var.project_name}-qdrant-efs"
}

resource "aws_efs_file_system" "qdrant" {
  creation_token = local.qdrant_efs_name
  encrypted      = true
  kms_key_id     = aws_kms_key.qdrant_efs.arn

  tags = {
    "Name" = local.qdrant_efs_name
  }
}

resource "aws_efs_mount_target" "qdrant" {
  count = length(data.terraform_remote_state.vpc.outputs.private_subnets)

  file_system_id  = aws_efs_file_system.qdrant.id
  subnet_id       = data.terraform_remote_state.vpc.outputs.private_subnets[count.index]
  security_groups = [aws_security_group.qdrant_efs.id]
}

resource "aws_efs_access_point" "qdrant" {
  file_system_id = aws_efs_file_system.qdrant.id

  posix_user {
    gid = 1000  # Qdrant container user
    uid = 1000  # Qdrant container user
  }

  root_directory {
    path = "/qdrant"
    creation_info {
      owner_gid   = 1000
      owner_uid   = 1000
      permissions = "0755"
    }
  }

  tags = {
    "Name" = local.qdrant_efs_name
  }
}

resource "aws_security_group" "qdrant_efs" {
  name   = "${local.qdrant_efs_name}-sg"
  vpc_id = data.terraform_remote_state.vpc.outputs.vpc_id

  ingress {
    from_port = 2049
    to_port   = 2049
    protocol  = "TCP"
    security_groups = [
      module.qdrant.ecs_sg_id,
    ]
  }
}

resource "aws_efs_backup_policy" "qdrant" {
  file_system_id = aws_efs_file_system.qdrant.id

  backup_policy {
    status = "ENABLED"
  }
}

resource "aws_kms_key" "qdrant_efs" {
  description         = local.qdrant_efs_name
  enable_key_rotation = true
  policy              = data.aws_iam_policy_document.qdrant_efs.json
}

resource "aws_kms_alias" "qdrant_efs" {
  name          = "alias/${local.qdrant_efs_name}"
  target_key_id = aws_kms_key.qdrant_efs.key_id
}

data "aws_iam_policy_document" "qdrant_efs" {
  statement {
    sid = "AllowEFSAccess"
    actions = [
      "kms:Encrypt",
      "kms:Decrypt",
      "kms:ReEncrypt*",
      "kms:GenerateDataKey*",
      "kms:CreateGrant",
      "kms:DescribeKey",
      "kms:ListAliases"
    ]
    resources = ["*"]

    principals {
      type        = "AWS"
      identifiers = ["*"]
    }

    condition {
      test     = "StringEquals"
      variable = "kms:ViaService"
      values   = ["elasticfilesystem.${data.aws_region.current.name}.amazonaws.com"]
    }
    condition {
      test     = "StringEquals"
      variable = "kms:CallerAccount"
      values   = [data.aws_caller_identity.current.account_id]
    }
  }

  statement {
    sid = "AllowAWSView"
    actions = [
      "kms:Describe*",
      "kms:List*",
      "kms:Get*",
    ]

    resources = ["*"]

    principals {
      type        = "AWS"
      identifiers = ["arn:aws:iam::${data.aws_caller_identity.current.account_id}:root"]
    }
  }

  statement {
    sid = "AllowKeyAdministration"
    actions = [
      "kms:Create*",
      "kms:Describe*",
      "kms:Enable*",
      "kms:List*",
      "kms:Put*",
      "kms:Update*",
      "kms:Revoke*",
      "kms:Disable*",
      "kms:Get*",
      "kms:Delete*",
      "kms:ScheduleKeyDeletion",
      "kms:CancelKeyDeletion",
    ]

    resources = ["*"]

    principals {
      type        = "AWS"
      identifiers = [
          "arn:aws:iam::${data.aws_caller_identity.current.account_id}:role/infra/${local.name}-ci-deployment-role",
          "arn:aws:iam::${data.aws_caller_identity.current.account_id}:role/admin-role",
        ]
    }
  }
}
