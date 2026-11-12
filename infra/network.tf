data "aws_availability_zones" "available" {
  # checkov:skip=CKV_AWS_394:Only the first 3 names are used; the host sits in the first one.
  state = "available"
}

locals {
  azs = slice(data.aws_availability_zones.available.names, 0, 3)

  interface_endpoints = toset(concat(
    ["ssm", "ssmmessages", "ec2messages"],
    var.enable_logs_endpoint ? ["logs"] : [],
    var.enable_phase2_kms ? ["kms"] : [],
  ))
}

resource "aws_vpc" "this" {
  # checkov:skip=CKV2_AWS_11:No flow-log sink in the demo; in the production checklist.
  cidr_block           = var.vpc_cidr
  enable_dns_support   = true
  enable_dns_hostnames = true # required for private DNS of interface endpoints

  tags = { Name = var.project_name }
}

# Lock the default SG down: no rules at all.
resource "aws_default_security_group" "default" {
  vpc_id = aws_vpc.this.id
  tags   = { Name = "${var.project_name}-default-unused" }
}

# Private subnets only. There is deliberately no internet gateway and no NAT gateway.
resource "aws_subnet" "private" {
  count = 3

  vpc_id                  = aws_vpc.this.id
  availability_zone       = local.azs[count.index]
  cidr_block              = cidrsubnet(var.vpc_cidr, 8, count.index)
  map_public_ip_on_launch = false

  tags = { Name = "${var.project_name}-private-${local.azs[count.index]}" }

  lifecycle {
    precondition {
      condition     = length(data.aws_availability_zones.available.names) >= 3
      error_message = "The region needs at least 3 available AZs."
    }
  }
}

resource "aws_route_table" "private" {
  vpc_id = aws_vpc.this.id
  tags   = { Name = "${var.project_name}-private" }
}

resource "aws_route_table_association" "private" {
  count = 3

  subnet_id      = aws_subnet.private[count.index].id
  route_table_id = aws_route_table.private.id
}

# Gateway endpoint: S3 (free). Adds the S3 prefix-list route to the private route table.
resource "aws_vpc_endpoint" "s3" {
  vpc_id            = aws_vpc.this.id
  service_name      = "com.amazonaws.${var.region}.s3"
  vpc_endpoint_type = "Gateway"
  route_table_ids   = [aws_route_table.private.id]

  tags = { Name = "${var.project_name}-s3" }
}

# Interface endpoints (billed hourly per AZ). Needed for SSM Session Manager without NAT.
resource "aws_vpc_endpoint" "interface" {
  for_each = local.interface_endpoints

  vpc_id              = aws_vpc.this.id
  service_name        = "com.amazonaws.${var.region}.${each.key}"
  vpc_endpoint_type   = "Interface"
  subnet_ids          = aws_subnet.private[*].id
  security_group_ids  = [aws_security_group.endpoints.id]
  private_dns_enabled = true

  tags = { Name = "${var.project_name}-${each.key}" }
}
