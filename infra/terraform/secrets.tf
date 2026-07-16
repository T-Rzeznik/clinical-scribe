# secrets.tf
# ---------------------------------------------------------------------------
# All runtime secrets live in ONE Secrets Manager secret, stored as a JSON object
# whose keys are exactly the environment-variable names the app reads. The EC2
# instance fetches this at boot (via its IAM role) and writes it to
# /etc/clinical-scribe.env, which systemd loads as the service's EnvironmentFile.
# Nothing sensitive is ever committed to the repo.
#
# The DB password and JWT secret are GENERATED here with random_password, so they
# exist only in Terraform state + Secrets Manager, never in source. The SAME
# generated DB password is set as the RDS master password (rds.tf) and embedded
# in DATABASE_URL below — they cannot drift because they reference one value.
# ---------------------------------------------------------------------------

# DB password. special=false keeps it URL-safe: it goes straight into
# DATABASE_URL (postgresql+asyncpg://user:PASSWORD@host/db) with no escaping, and
# it also dodges the handful of characters RDS forbids in a master password
# (/, @, ", space). 32 alphanumerics is plenty of entropy.
resource "random_password" "db" {
  length  = 32
  special = false
}

# JWT signing secret for the app's access tokens.
resource "random_password" "jwt" {
  length  = 48
  special = false
}

resource "aws_secretsmanager_secret" "app_config" {
  name        = "${var.project_name}/app-config"
  description = "Runtime env vars for the Clinical Scribe app (DB URL, JWT secret, API keys)."

  # Demo convenience: 0-day recovery window means a destroyed secret is deleted
  # immediately, so `terraform destroy` + re-apply won't collide with a secret
  # "pending deletion". In production you'd keep the default 30-day window.
  recovery_window_in_days = 0

  tags = { Name = "${var.project_name}-app-config" }
}

# The actual secret payload. Keys == env-var names the app expects. We assemble
# DATABASE_URL from the RDS endpoint + generated password, which also creates the
# dependency edge that makes Terraform build RDS before writing this version.
resource "aws_secretsmanager_secret_version" "app_config" {
  secret_id = aws_secretsmanager_secret.app_config.id

  secret_string = jsonencode({
    DATABASE_URL = "postgresql+asyncpg://${var.db_username}:${random_password.db.result}@${aws_db_instance.this.address}:5432/${var.db_name}"

    JWT_SECRET             = random_password.jwt.result
    JWT_ACCESS_TTL_MINUTES = tostring(var.jwt_access_ttl_minutes)
    REFRESH_TOKEN_TTL_DAYS = tostring(var.refresh_token_ttl_days)

    # Operator-supplied (sensitive var). Empty until you set it in tfvars.
    ANTHROPIC_API_KEY = var.anthropic_api_key
  })
}
