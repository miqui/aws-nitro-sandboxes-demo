plugin "aws" {
  enabled = true
  version = "0.38.0" # UNVERIFIED version: bump to the latest tflint-ruleset-aws release; run `tflint --init`
  source  = "github.com/terraform-linters/tflint-ruleset-aws"
}

plugin "terraform" {
  enabled = true
  preset  = "recommended"
}
