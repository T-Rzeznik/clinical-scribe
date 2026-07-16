# versions.tf
# ---------------------------------------------------------------------------
# Pins the Terraform CLI and provider versions so the module behaves the same
# on every machine and in CI. Loose upper bounds (~>) let us pick up patch/minor
# fixes without silently jumping to a new major that could rename attributes.
# ---------------------------------------------------------------------------

terraform {
  required_version = ">= 1.5"

  required_providers {
    # The AWS provider. v5 is the current major; attribute names in this module
    # (e.g. aws_eip `domain`, aws_db_instance `db_name`) are the v5 spellings.
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }

    # Used to generate the DB password and JWT secret at apply time so no
    # credential is ever hardcoded in the repo (see secrets.tf).
    random = {
      source  = "hashicorp/random"
      version = "~> 3.0"
    }
  }
}
