# iam.tf
# ---------------------------------------------------------------------------
# Gives the EC2 instance an identity (IAM role, surfaced as an instance profile)
# so it can read exactly ONE secret from Secrets Manager WITHOUT any static AWS
# access keys living on the box. The instance calls the AWS APIs using temporary
# credentials delivered through the instance metadata service.
#
# Least privilege: the inline policy allows only secretsmanager:GetSecretValue,
# and only on THIS project's secret ARN — not "*". If the box is compromised, the
# blast radius is "can read one secret it already needed", nothing more.
#
# We also attach the AWS-managed SSM Core policy so you can get a shell via
# Session Manager with no SSH key and no open port 22 (recommended). This is a
# standard AWS managed policy scoped to SSM's own agent operations.
# ---------------------------------------------------------------------------

# Trust policy: only the EC2 service may assume this role.
data "aws_iam_policy_document" "ec2_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ec2.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "ec2" {
  name               = "${var.project_name}-ec2-role"
  assume_role_policy = data.aws_iam_policy_document.ec2_assume.json
  tags               = { Name = "${var.project_name}-ec2-role" }
}

# Least-privilege permission: read only this app's secret.
data "aws_iam_policy_document" "read_secret" {
  statement {
    sid       = "ReadAppConfigSecret"
    actions   = ["secretsmanager:GetSecretValue"]
    resources = [aws_secretsmanager_secret.app_config.arn]
  }
}

resource "aws_iam_role_policy" "read_secret" {
  name   = "${var.project_name}-read-secret"
  role   = aws_iam_role.ec2.id
  policy = data.aws_iam_policy_document.read_secret.json
}

# Enables AWS SSM Session Manager (keyless shell) + Patch Manager basics.
resource "aws_iam_role_policy_attachment" "ssm" {
  role       = aws_iam_role.ec2.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"
}

# The instance profile is the wrapper EC2 actually attaches to the instance.
resource "aws_iam_instance_profile" "ec2" {
  name = "${var.project_name}-ec2-profile"
  role = aws_iam_role.ec2.name
}
