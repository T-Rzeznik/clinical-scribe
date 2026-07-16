# security.tf
# ---------------------------------------------------------------------------
# Two security groups. The relationship between them is THE most important
# security control in this stack:
#
#   EC2 SG  -> allows 443/80 from the world (public HTTPS) + 22 from your IP.
#   RDS SG  -> allows 5432 ONLY from the EC2 SG (by security-group reference,
#              NOT a CIDR). Nothing else on earth can open a Postgres connection.
#
# Referencing the EC2 security group as the source (source_security_group_id)
# instead of an IP range means "any instance wearing the EC2 SG may talk to the
# DB, and nothing else." It keeps working even if the EC2 box is replaced and
# gets a new private IP, and it cannot be widened by accident the way a CIDR can.
# ---------------------------------------------------------------------------

# ---- EC2 security group ----------------------------------------------------
resource "aws_security_group" "ec2" {
  name        = "${var.project_name}-ec2-sg"
  description = "Public web tier: HTTPS/HTTP in, SSH from operator only."
  vpc_id      = aws_vpc.this.id

  # 443: public HTTPS. nginx terminates TLS here.
  ingress {
    description = "HTTPS from anywhere"
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  # 80: public HTTP. Needed for (a) Let's Encrypt's HTTP-01 challenge and
  # (b) the nginx :80 -> :443 redirect certbot installs. Not used for real app
  # traffic beyond the redirect.
  ingress {
    description = "HTTP from anywhere (ACME challenge + redirect to HTTPS)"
    from_port   = 80
    to_port     = 80
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  # 22: SSH, locked to the operator's IP (var has no default -> you must set it).
  # Prefer SSM Session Manager (keyless) and you can effectively ignore this port.
  ingress {
    description = "SSH from operator IP only"
    from_port   = 22
    to_port     = 22
    protocol    = "tcp"
    cidr_blocks = [var.ssh_ingress_cidr]
  }

  # Egress open: the instance must reach Secrets Manager, DuckDNS, Let's Encrypt,
  # the Anthropic API, the git host, apt/npm mirrors, and RDS. Outbound-open is
  # the normal, safe default; the inbound rules above are what actually gate access.
  egress {
    description = "All outbound"
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = { Name = "${var.project_name}-ec2-sg" }
}

# ---- RDS security group ----------------------------------------------------
resource "aws_security_group" "rds" {
  name        = "${var.project_name}-rds-sg"
  description = "Private DB tier: Postgres 5432 reachable ONLY from the EC2 SG."
  vpc_id      = aws_vpc.this.id

  # THE hard requirement: inbound 5432 accepts connections ONLY from instances
  # in the EC2 security group. No CIDR, no public exposure. Combined with the
  # database's publicly_accessible=false and its placement in private subnets,
  # the database is unreachable from the internet.
  ingress {
    description     = "Postgres from the EC2 security group only"
    from_port       = 5432
    to_port         = 5432
    protocol        = "tcp"
    security_groups = [aws_security_group.ec2.id] # <-- SG reference, not a CIDR
  }

  # RDS has no reason to initiate outbound connections, but leaving egress at the
  # default-open value is harmless (the private subnets have no internet route
  # anyway, so this traffic can't leave the VPC).
  egress {
    description = "All outbound (constrained by no-internet-route private subnets)"
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = { Name = "${var.project_name}-rds-sg" }
}
