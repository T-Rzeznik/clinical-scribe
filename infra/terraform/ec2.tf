# ec2.tf
# ---------------------------------------------------------------------------
# The single web/app instance: Ubuntu 22.04 running nginx (TLS + reverse proxy)
# in front of uvicorn (FastAPI on 127.0.0.1:8000), plus the built React SPA.
# All configuration happens in templates/user_data.sh.tftpl at first boot.
#
# An Elastic IP is attached so the public address is STABLE across stop/start.
# That stability is what lets the DuckDNS A-record (and therefore the Let's
# Encrypt cert) keep pointing at the box. An EIP attached to a RUNNING instance
# is free; you only pay if it's left unattached, so we never leave it dangling.
# ---------------------------------------------------------------------------

# Latest Ubuntu 22.04 LTS (Jammy) amd64 AMI, owned by Canonical. Looked up by
# name pattern so we always get the current patched image and the module works
# in any region (AMI IDs are region-specific).
data "aws_ami" "ubuntu" {
  most_recent = true
  owners      = ["099720109477"] # Canonical's AWS account ID

  filter {
    name   = "name"
    values = ["ubuntu/images/hvm-ssd/ubuntu-jammy-22.04-amd64-server-*"]
  }
  filter {
    name   = "virtualization-type"
    values = ["hvm"]
  }
}

# Stable public IP. Allocated in the VPC scope.
resource "aws_eip" "this" {
  domain = "vpc"
  tags   = { Name = "${var.project_name}-eip" }
}

resource "aws_instance" "this" {
  ami                    = data.aws_ami.ubuntu.id
  instance_type          = var.instance_type
  subnet_id              = aws_subnet.public.id
  vpc_security_group_ids = [aws_security_group.ec2.id]
  iam_instance_profile   = aws_iam_instance_profile.ec2.name

  # Attach an SSH key only if one was named; otherwise launch keyless (use SSM).
  key_name = var.key_name != "" ? var.key_name : null

  # A public IP is assigned at launch (subnet default) for the brief window until
  # the Elastic IP association below takes over.
  associate_public_ip_address = true

  root_block_device {
    volume_type = "gp3"
    volume_size = var.root_volume_size
    encrypted   = true
  }

  # First-boot provisioning script. We pass the Elastic IP's address in so the
  # instance can register it with DuckDNS BEFORE certbot runs its HTTP-01 check.
  # NOTE: we reference aws_eip.this.public_ip here, so the EIP is created before
  # the instance; the association (below) completes moments after boot, and the
  # certbot step in user-data retries with backoff to cover that short gap.
  user_data = templatefile("${path.module}/templates/user_data.sh.tftpl", {
    aws_region        = var.aws_region
    secret_arn        = aws_secretsmanager_secret.app_config.arn
    repo_url          = var.repo_url
    duckdns_subdomain = var.duckdns_subdomain
    duckdns_token     = var.duckdns_token
    duckdns_ip        = aws_eip.this.public_ip
    letsencrypt_email = var.letsencrypt_email
  })

  # Replace (and thus re-run user-data) if the provisioning script changes.
  user_data_replace_on_change = true

  # Don't boot until the secret VERSION is written (it embeds the RDS endpoint +
  # password). This transitively forces RDS to exist first, so at first boot the
  # instance can both fetch a fully-populated secret AND reach a live database.
  depends_on = [aws_secretsmanager_secret_version.app_config]

  tags = { Name = "${var.project_name}-web" }
}

# Bind the Elastic IP to the instance. Kept as a separate resource so the EIP can
# exist/allocate before the instance and be referenced in user_data above.
resource "aws_eip_association" "this" {
  instance_id   = aws_instance.this.id
  allocation_id = aws_eip.this.id
}
