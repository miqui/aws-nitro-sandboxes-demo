# One security group per role. No ingress to the host, ever.

resource "aws_security_group" "host" {
  name_prefix = "${var.project_name}-host-"
  description = "Nitro Sandbox parent host: no ingress; egress 443 to VPC endpoints and S3 only"
  vpc_id      = aws_vpc.this.id

  tags = { Name = "${var.project_name}-host" }

  lifecycle {
    create_before_destroy = true
  }
}

resource "aws_security_group" "endpoints" {
  name_prefix = "${var.project_name}-endpoints-"
  description = "Interface VPC endpoints: HTTPS only from the host SG"
  vpc_id      = aws_vpc.this.id

  tags = { Name = "${var.project_name}-endpoints" }

  lifecycle {
    create_before_destroy = true
  }
}

resource "aws_vpc_security_group_ingress_rule" "endpoints_from_host" {
  security_group_id            = aws_security_group.endpoints.id
  description                  = "HTTPS from host SG"
  referenced_security_group_id = aws_security_group.host.id
  ip_protocol                  = "tcp"
  from_port                    = 443
  to_port                      = 443
}

resource "aws_vpc_security_group_egress_rule" "host_to_endpoints" {
  security_group_id            = aws_security_group.host.id
  description                  = "HTTPS to interface endpoints"
  referenced_security_group_id = aws_security_group.endpoints.id
  ip_protocol                  = "tcp"
  from_port                    = 443
  to_port                      = 443
}

resource "aws_vpc_security_group_egress_rule" "host_to_s3" {
  security_group_id = aws_security_group.host.id
  description       = "HTTPS to S3 via gateway endpoint prefix list"
  prefix_list_id    = aws_vpc_endpoint.s3.prefix_list_id
  ip_protocol       = "tcp"
  from_port         = 443
  to_port           = 443
}
