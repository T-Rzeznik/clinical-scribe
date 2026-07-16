# providers.tf
# ---------------------------------------------------------------------------
# Configures the AWS provider. Region is a variable so the whole stack can move
# regions without editing code. `default_tags` stamps every taggable resource
# in the module with a consistent set of tags, which makes cost allocation and
# "what did this project create?" cleanup trivial in the AWS console.
# ---------------------------------------------------------------------------

provider "aws" {
  region = var.aws_region

  default_tags {
    tags = {
      Project   = var.project_name
      ManagedBy = "terraform"
      # Marks these as demo/take-home resources so they are easy to find and
      # `terraform destroy` when you are done (to stop billing).
      Environment = "demo"
    }
  }
}
