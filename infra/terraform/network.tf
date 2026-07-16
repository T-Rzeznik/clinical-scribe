# network.tf
# ---------------------------------------------------------------------------
# A deliberately minimal VPC. The whole design goal is "cheapest topology that
# still keeps RDS private":
#
#   * 1 public subnet  -> the EC2 box (has a public IP + route to the Internet
#                          Gateway so it can serve HTTPS and reach out to
#                          Secrets Manager / DuckDNS / Let's Encrypt / Anthropic).
#   * 2 private subnets -> RDS. No route to the internet at all. RDS lives here
#                          so it is NOT publicly reachable (hard requirement).
#
# WHY NO NAT GATEWAY (saves ~$32/mo): a NAT gateway exists to give PRIVATE
# instances outbound internet. Our only private resource is RDS, and RDS never
# needs to call out (nothing initiates connections FROM the database). The EC2
# box that DOES need outbound lives in the PUBLIC subnet and uses the free
# Internet Gateway via its own public IP. So a NAT gateway would be pure cost
# for zero benefit here.
#
# WHY 2 PRIVATE SUBNETS FOR A SINGLE-AZ DB: an RDS "DB subnet group" REQUIRES
# subnets in at least two Availability Zones, even when multi_az = false. RDS
# uses the second AZ only if you later flip on Multi-AZ; until then the standby
# subnet just sits empty. It costs nothing to declare, so we satisfy the API
# requirement up front.
# ---------------------------------------------------------------------------

# Look up the AZs available in the chosen region so the module is region-agnostic
# (we don't hardcode "us-east-1a"). We use the first two.
data "aws_availability_zones" "available" {
  state = "available"
}

# ---- VPC -------------------------------------------------------------------

resource "aws_vpc" "this" {
  cidr_block = var.vpc_cidr

  # DNS support + hostnames are required so RDS gets an internal DNS name and so
  # the EC2 instance can resolve public endpoints (Secrets Manager, etc.).
  enable_dns_support   = true
  enable_dns_hostnames = true

  tags = { Name = "${var.project_name}-vpc" }
}

# ---- Internet Gateway (free) ----------------------------------------------
# Gives the public subnet a path to/from the internet. No hourly charge; you only
# pay for data transfer, same as a NAT would — but without NAT's hourly fee.
resource "aws_internet_gateway" "this" {
  vpc_id = aws_vpc.this.id
  tags   = { Name = "${var.project_name}-igw" }
}

# ---- Public subnet (EC2) ---------------------------------------------------
resource "aws_subnet" "public" {
  vpc_id            = aws_vpc.this.id
  cidr_block        = cidrsubnet(var.vpc_cidr, 8, 0) # 10.0.0.0/24
  availability_zone = data.aws_availability_zones.available.names[0]

  # Instances launched here get a public IP automatically. The EC2 instance also
  # gets a stable Elastic IP (see ec2.tf); this flag just ensures a public IP
  # exists during the brief window before the EIP is associated.
  map_public_ip_on_launch = true

  tags = { Name = "${var.project_name}-public" }
}

# ---- Private subnets (RDS) -------------------------------------------------
# Two subnets in two different AZs. No public IPs, no internet route -> private.
resource "aws_subnet" "private" {
  count             = 2
  vpc_id            = aws_vpc.this.id
  cidr_block        = cidrsubnet(var.vpc_cidr, 8, count.index + 10) # 10.0.10.0/24, 10.0.11.0/24
  availability_zone = data.aws_availability_zones.available.names[count.index]

  tags = { Name = "${var.project_name}-private-${count.index}" }
}

# ---- Public route table ----------------------------------------------------
# Default route 0.0.0.0/0 -> Internet Gateway. Only the PUBLIC subnet is
# associated with this table, which is exactly what makes it "public".
resource "aws_route_table" "public" {
  vpc_id = aws_vpc.this.id

  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.this.id
  }

  tags = { Name = "${var.project_name}-public-rt" }
}

resource "aws_route_table_association" "public" {
  subnet_id      = aws_subnet.public.id
  route_table_id = aws_route_table.public.id
}

# NOTE: We intentionally create NO route table for the private subnets. They
# fall back to the VPC's "main" route table, which contains only the implicit
# local route (intra-VPC). With no 0.0.0.0/0 entry, the private subnets have no
# path to the internet -> RDS is unreachable from outside the VPC. This is the
# no-NAT design working as intended.

# ---- DB subnet group -------------------------------------------------------
# Tells RDS which subnets it may place the database in. Must span >=2 AZs.
resource "aws_db_subnet_group" "this" {
  name       = "${var.project_name}-db-subnets"
  subnet_ids = aws_subnet.private[*].id
  tags       = { Name = "${var.project_name}-db-subnets" }
}
