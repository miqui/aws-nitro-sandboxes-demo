variable "project_name" {
  description = "Name prefix for all resources."
  type        = string
  default     = "nitro-sbx"

  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{2,24}$", var.project_name))
    error_message = "project_name must be 3-25 chars: lowercase letters, digits, hyphens; start with a letter."
  }
}

variable "region" {
  description = "AWS region. Nitro Sandboxes on EC2: all commercial regions (announcement, per plan)."
  type        = string
  default     = "us-east-1"
}

variable "instance_type" {
  description = "Parent instance type. MUST support AWS Nitro Enclaves (Nitro-based, not bare metal, not burstable)."
  type        = string
  # UNVERIFIED: the plan could not confirm m6i.2xlarge is Enclaves-capable. Check the
  # Nitro Enclaves requirements page before the first apply (see infra/README.md).
  default = "m6i.2xlarge"

  validation {
    condition = (
      can(regex("^[a-z][a-z0-9]*\\.[a-z0-9]+$", var.instance_type)) &&
      !can(regex("\\.metal", var.instance_type)) &&
      !can(regex("^t[0-9]", var.instance_type))
    )
    error_message = "instance_type must look like m6i.2xlarge; bare metal (.metal) and burstable (t*) types are not allowed for Nitro Enclaves/Sandboxes."
  }
}

variable "architecture" {
  description = "CPU architecture of the parent. Only x86_64 is wired in this demo (arm64 would need a Graviton type + arm64 AMI parameter)."
  type        = string
  default     = "x86_64"

  validation {
    condition     = var.architecture == "x86_64"
    error_message = "Only x86_64 is supported by this demo."
  }
}

variable "ami_ssm_parameter_x86_64" {
  description = "Public SSM parameter resolved by the launch template (no API call at plan time)."
  type        = string
  # Name from the Firecracker getting-started / EC2 sandbox docs (plan, verified).
  # AL2023 kernel 6.18 >= 6.18.51-120.162 is required.
  default = "/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-6.18-x86_64"
}

variable "vpc_cidr" {
  description = "CIDR of the VPC; three /24 private subnets are carved from it."
  type        = string
  default     = "10.20.0.0/16"

  validation {
    condition     = can(cidrhost(var.vpc_cidr, 0)) && can(cidrnetmask(var.vpc_cidr)) && can(regex("/16$", var.vpc_cidr))
    error_message = "vpc_cidr must be a valid IPv4 CIDR with prefix length /16."
  }
}

variable "root_volume_size_gb" {
  description = "Root EBS volume size (GiB). Holds kernel/rootfs/Firecracker; 30 is the plan default."
  type        = number
  default     = 30

  validation {
    condition     = var.root_volume_size_gb >= 30 && var.root_volume_size_gb <= 500 && floor(var.root_volume_size_gb) == var.root_volume_size_gb
    error_message = "root_volume_size_gb must be a whole number between 30 and 500 GiB."
  }
}

variable "enable_logs_endpoint" {
  description = "Create a CloudWatch Logs interface endpoint (extra hourly cost). Nothing in the demo ships logs by default."
  type        = bool
  default     = false
}

variable "enable_phase2_kms" {
  description = "Phase 2: create a KMS key released only to a sandbox with the pinned PCR0 (plus KMS interface endpoint). UNVERIFIED end to end."
  type        = bool
  default     = false
}

variable "kms_allowed_image_pcr0" {
  description = "Phase 2: hex SHA-384 (96 chars) of the sandbox image (PCR0) allowed to Decrypt. Required when enable_phase2_kms = true. Never an all-zero (debug) value."
  type        = string
  default     = ""

  validation {
    condition = (
      var.kms_allowed_image_pcr0 == "" ||
      (can(regex("^[0-9a-fA-F]{96}$", var.kms_allowed_image_pcr0)) &&
      !can(regex("^0{96}$", var.kms_allowed_image_pcr0)))
    )
    error_message = "kms_allowed_image_pcr0 must be empty or 96 hex chars and not all zeros (debug-mode PCRs are zeroed)."
  }
}
