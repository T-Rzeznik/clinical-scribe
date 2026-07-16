# variables.tf
# ---------------------------------------------------------------------------
# Every input to the stack. Anything with a `default` is a safe, cost-optimized
# choice you can override in terraform.tfvars. The five variables with NO default
# force the operator to supply them (Terraform errors out if they are missing) —
# these are either secrets or per-operator values we must never guess:
#   ssh_ingress_cidr, anthropic_api_key(*), duckdns_subdomain, duckdns_token,
#   letsencrypt_email.
# (*) anthropic_api_key keeps an empty-string default so `plan` works before you
#     have a key, but the app will refuse to generate notes until you set it.
# ---------------------------------------------------------------------------

# ---- Provider / naming -----------------------------------------------------

variable "aws_region" {
  description = "AWS region to deploy into. Any region with RDS + Secrets Manager works."
  type        = string
  default     = "us-east-1"
}

variable "project_name" {
  description = "Short name used to prefix/tag resources."
  type        = string
  default     = "clinical-scribe"
}

# ---- Access control (operator MUST set ssh_ingress_cidr) -------------------

variable "ssh_ingress_cidr" {
  description = <<-EOT
    CIDR allowed to SSH (port 22) to the EC2 instance, e.g. "203.0.113.45/32".
    NO DEFAULT ON PURPOSE: defaulting to 0.0.0.0/0 would expose SSH to the whole
    internet. Set this to YOUR public IP /32. If you would rather not open SSH at
    all, use AWS SSM Session Manager instead (the instance role already allows it —
    see the runbook) and set this to a placeholder like "203.0.113.45/32".
  EOT
  type        = string
}

variable "key_name" {
  description = <<-EOT
    Optional name of an existing EC2 key pair for SSH. Leave "" to launch with no
    key pair (recommended — use SSM Session Manager for shell access instead).
  EOT
  type    = string
  default = ""
}

# ---- Application source -----------------------------------------------------

variable "repo_url" {
  description = <<-EOT
    Git URL the instance clones the app from at boot (backend/ + frontend/).
    Replace with your fork's clone URL. Must be reachable without credentials
    (public repo) since the instance has no git auth configured.
  EOT
  type    = string
  default = "https://github.com/YOUR_GITHUB_USERNAME/clinical-scribe.git"
}

# ---- Secrets / third-party (operator MUST set the DuckDNS + email ones) -----

variable "anthropic_api_key" {
  description = <<-EOT
    Anthropic API key for SOAP-note generation. Stored in Secrets Manager, never
    in the repo. Empty default lets `terraform plan` run before you have a key,
    but note generation will fail until you set a real value in terraform.tfvars.
  EOT
  type      = string
  default   = ""
  sensitive = true
}

variable "duckdns_subdomain" {
  description = <<-EOT
    Your free DuckDNS subdomain WITHOUT the .duckdns.org suffix (e.g.
    "clinicalscribe" -> the app is served at https://clinicalscribe.duckdns.org).
    NO DEFAULT: create a free account at duckdns.org, add a subdomain (~30s), and
    put it here. This gives us a real, publicly-resolvable hostname so Let's
    Encrypt can issue a REAL cert (no registrar, no cost).
  EOT
  type = string
}

variable "duckdns_token" {
  description = <<-EOT
    Your DuckDNS account token (shown at the top of the DuckDNS dashboard). Used
    once at boot to point the subdomain at this instance's Elastic IP via the
    DuckDNS update API. Sensitive; NO DEFAULT.
  EOT
  type      = string
  sensitive = true
}

variable "letsencrypt_email" {
  description = <<-EOT
    Email for Let's Encrypt / certbot registration and expiry-warning notices.
    NO DEFAULT (Let's Encrypt requires a contact address).
  EOT
  type = string
}

# ---- App config (non-secret defaults, fine to leave as-is) -----------------

variable "jwt_access_ttl_minutes" {
  description = "Access-token lifetime in minutes (matches the app's default)."
  type        = number
  default     = 20
}

variable "refresh_token_ttl_days" {
  description = "Refresh-token lifetime in days (matches the app's default)."
  type        = number
  default     = 30
}

# ---- Compute (cost-optimized, free-tier-friendly) --------------------------

variable "instance_type" {
  description = "EC2 instance type. t3.micro is free-tier eligible and enough for a demo."
  type        = string
  default     = "t3.micro"
}

variable "root_volume_size" {
  description = "Root EBS volume size in GB (gp3). 20 GB fits the OS + venv + node build."
  type        = number
  default     = 20
}

# ---- Database (cost-optimized) ---------------------------------------------

variable "rds_instance_class" {
  description = "RDS instance class. db.t4g.micro (Graviton) is the cheapest current-gen option."
  type        = string
  default     = "db.t4g.micro"
}

variable "db_allocated_storage" {
  description = "RDS storage in GB (gp3)."
  type        = number
  default     = 20
}

variable "postgres_version" {
  description = "RDS PostgreSQL major version. 16 supports pgvector on RDS."
  type        = string
  default     = "16"
}

variable "db_name" {
  description = "Initial database name created by RDS."
  type        = string
  default     = "clinical_scribe"
}

variable "db_username" {
  description = "RDS master username. Avoid reserved words like 'admin'/'postgres'."
  type        = string
  default     = "scribe"
}

# ---- Network ---------------------------------------------------------------

variable "vpc_cidr" {
  description = "CIDR block for the VPC. /16 leaves plenty of room for the /24 subnets."
  type        = string
  default     = "10.0.0.0/16"
}
