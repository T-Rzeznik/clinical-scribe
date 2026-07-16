# outputs.tf
# ---------------------------------------------------------------------------
# Handy values printed after `terraform apply` — the URL to open, the IP to SSH
# to, and identifiers for the post-apply runbook steps.
# ---------------------------------------------------------------------------

output "https_url" {
  description = "Public app URL (real Let's Encrypt cert once certbot finishes on first boot)."
  value       = "https://${var.duckdns_subdomain}.duckdns.org"
}

output "ec2_public_ip" {
  description = "Stable Elastic IP of the instance (also the DuckDNS A-record target)."
  value       = aws_eip.this.public_ip
}

output "rds_endpoint" {
  description = "Private RDS endpoint (resolvable only inside the VPC — proves it's not public)."
  value       = aws_db_instance.this.address
}

output "secret_arn" {
  description = "ARN of the Secrets Manager secret holding the app's env config."
  value       = aws_secretsmanager_secret.app_config.arn
}

output "ssh_hint" {
  description = "How to SSH in (only works if you set key_name and your IP in ssh_ingress_cidr)."
  value       = "ssh ubuntu@${aws_eip.this.public_ip}   # or: aws ssm start-session --target ${aws_instance.this.id}"
}
