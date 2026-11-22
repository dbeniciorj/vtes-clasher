variable "project_name" {
  description = "Nome do projeto (usado em tags)"
  type        = string
  default     = "vtes-sim"
}

variable "bucket_name" {
  description = "Nome do bucket S3 (globalmente único)"
  type        = string
  default     = "vtes-sim-site"
}

variable "aws_region" {
  description = "Região AWS"
  type        = string
  default     = "us-east-1"
}

variable "site_source_dir" {
  description = "Pasta local com os ficheiros do site (relativa à pasta Terraform ou absoluta)"
  type        = string
  default     = "../vtes-sim-site"
}

variable "enable_versioning" {
  description = "Ativar versionamento no bucket"
  type        = bool
  default     = false
}
