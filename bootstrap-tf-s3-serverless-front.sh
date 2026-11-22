#!/usr/bin/env bash
#
# bootstrap-tf-s3-serverless-front.sh
# Gera uma estrutura Terraform completa para hospedar o site estático VTES no S3
# (Static Website Hosting). Pergunta os dados necessários na execução.
#
# Uso:
#   chmod +x bootstrap-tf-s3-serverless-front.sh
#   ./bootstrap-tf-s3-serverless-front.sh
#
# Ficheiros gerados em terraform-<projeto>/:
#   providers.tf, variables.tf, main.tf, outputs.tf, .gitignore
#
# Pode ser executado várias vezes: os ficheiros gerados são sobrescritos.
#

set -euo pipefail

# Cores para output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

echo -e "${BLUE}================================================${NC}"
echo -e "${BLUE}  VTES Simulator – Gerador de Terraform (S3)   ${NC}"
echo -e "${BLUE}================================================${NC}"
echo

# ---------- Funções auxiliares ----------
ask() {
  local prompt="$1"
  local default="${2:-}"
  local var
  if [[ -n "$default" ]]; then
    read -r -p "$prompt [$default]: " var
    echo "${var:-$default}"
  else
    read -r -p "$prompt: " var
    echo "$var"
  fi
}

ask_yes_no() {
  local prompt="$1"
  local default="${2:-n}"
  local yn
  while true; do
    read -r -p "$prompt [y/N]: " yn
    yn="${yn:-$default}"
    case "$yn" in
      [Yy]*) return 0 ;;
      [Nn]*) return 1 ;;
      *) echo "Responde y ou n." ;;
    esac
  done
}

# ---------- Perguntas ----------
echo -e "${YELLOW}Vou precisar de alguns dados para gerar os ficheiros Terraform.${NC}"
echo

PROJECT_NAME=$(ask "Nome do projeto (usado em tags e prefixos)" "vtes-sim")
BUCKET_NAME=$(ask "Nome do bucket S3 (tem de ser globalmente único)" "${PROJECT_NAME}-$(date +%Y%m%d)")
AWS_REGION=$(ask "Região AWS" "eu-west-1")
SITE_SOURCE_DIR=$(ask "Caminho local da pasta do site (index.html, styles.css, etc.)" "./vtes-sim-site")

# Normalizar caminho
SITE_SOURCE_DIR=$(realpath -m "$SITE_SOURCE_DIR" 2>/dev/null || echo "$SITE_SOURCE_DIR")

# Validações básicas
if [[ ! "$BUCKET_NAME" =~ ^[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]$ ]]; then
  echo -e "${RED}Nome de bucket inválido: '$BUCKET_NAME'.${NC}"
  echo "Usa 3-63 caracteres: minúsculas, números, '.' e '-' (sem começar/terminar com '.' ou '-')."
  exit 1
fi

if [[ ! -d "$SITE_SOURCE_DIR" ]]; then
  echo -e "${RED}A pasta do site não existe: $SITE_SOURCE_DIR${NC}"
  exit 1
fi

if [[ ! -f "$SITE_SOURCE_DIR/index.html" ]]; then
  echo -e "${YELLOW}Aviso: não encontrei index.html em $SITE_SOURCE_DIR${NC}"
fi

echo
echo -e "Resumo:"
echo "  Projeto      : $PROJECT_NAME"
echo "  Bucket       : $BUCKET_NAME"
echo "  Região       : $AWS_REGION"
echo "  Pasta do site: $SITE_SOURCE_DIR"
echo

if ! ask_yes_no "Confirmar e gerar ficheiros Terraform?"; then
  echo "Cancelado."
  exit 0
fi

# ---------- Criar diretório de saída ----------
OUT_DIR="terraform-${PROJECT_NAME}"
mkdir -p "$OUT_DIR"
cd "$OUT_DIR"

# Caminho do site relativo à pasta Terraform (funciona em WSL, Linux e Windows).
# Se não for possível calcular, usa o caminho absoluto.
SITE_SOURCE_REL=$(realpath -m --relative-to="$(realpath -m .)" "$SITE_SOURCE_DIR" 2>/dev/null || echo "$SITE_SOURCE_DIR")

echo
echo -e "${GREEN}A gerar ficheiros em: $(pwd)${NC}"
echo

# ---------- providers.tf ----------
cat > providers.tf <<EOF
terraform {
  required_version = ">= 1.5.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
}

provider "aws" {
  region = var.aws_region

  default_tags {
    tags = {
      Project     = var.project_name
      ManagedBy   = "terraform"
      Environment = "production"
    }
  }
}
EOF

# ---------- variables.tf ----------
cat > variables.tf <<EOF
variable "project_name" {
  description = "Nome do projeto (usado em tags)"
  type        = string
  default     = "${PROJECT_NAME}"
}

variable "bucket_name" {
  description = "Nome do bucket S3 (globalmente único)"
  type        = string
  default     = "${BUCKET_NAME}"
}

variable "aws_region" {
  description = "Região AWS"
  type        = string
  default     = "${AWS_REGION}"
}

variable "site_source_dir" {
  description = "Pasta local com os ficheiros do site (relativa à pasta Terraform ou absoluta)"
  type        = string
  default     = "${SITE_SOURCE_REL}"
}

variable "enable_versioning" {
  description = "Ativar versionamento no bucket"
  type        = bool
  default     = false
}
EOF

# ---------- main.tf ----------
cat > main.tf <<'EOF'
# ============================================================
# S3 Bucket – Static Website Hosting
# ============================================================

resource "aws_s3_bucket" "website" {
  bucket = var.bucket_name

  # Forçar a destruição mesmo com objetos (cuidado em produção)
  force_destroy = true
}

# Bloqueio de acesso público – precisamos desativar parcialmente
# para permitir website público
resource "aws_s3_bucket_public_access_block" "website" {
  bucket = aws_s3_bucket.website.id

  block_public_acls       = false
  block_public_policy     = false
  ignore_public_acls      = false
  restrict_public_buckets = false
}

# Website configuration
resource "aws_s3_bucket_website_configuration" "website" {
  bucket = aws_s3_bucket.website.id

  index_document {
    suffix = "index.html"
  }

  # Sem página 404 dedicada: devolve o index.html
  error_document {
    key = "index.html"
  }
}

# Versionamento (opcional)
resource "aws_s3_bucket_versioning" "website" {
  count  = var.enable_versioning ? 1 : 0
  bucket = aws_s3_bucket.website.id

  versioning_configuration {
    status = "Enabled"
  }
}

# Política pública de leitura (apenas GetObject)
data "aws_iam_policy_document" "public_read" {
  statement {
    sid       = "PublicReadGetObject"
    effect    = "Allow"
    actions   = ["s3:GetObject"]
    resources = ["${aws_s3_bucket.website.arn}/*"]

    principals {
      type        = "*"
      identifiers = ["*"]
    }
  }
}

resource "aws_s3_bucket_policy" "public_read" {
  bucket = aws_s3_bucket.website.id
  policy = data.aws_iam_policy_document.public_read.json

  # A política pública só é aceite depois de o bloqueio ser removido
  depends_on = [aws_s3_bucket_public_access_block.website]
}

# ============================================================
# Upload dos ficheiros do site
# ============================================================

locals {
  mime_types = {
    html  = "text/html; charset=utf-8"
    htm   = "text/html; charset=utf-8"
    css   = "text/css; charset=utf-8"
    js    = "application/javascript; charset=utf-8"
    mjs   = "application/javascript; charset=utf-8"
    json  = "application/json; charset=utf-8"
    txt   = "text/plain; charset=utf-8"
    md    = "text/markdown; charset=utf-8"
    xml   = "application/xml"
    svg   = "image/svg+xml"
    png   = "image/png"
    jpg   = "image/jpeg"
    jpeg  = "image/jpeg"
    gif   = "image/gif"
    webp  = "image/webp"
    ico   = "image/x-icon"
    woff  = "font/woff"
    woff2 = "font/woff2"
    ttf   = "font/ttf"
    pdf   = "application/pdf"
  }

  site_files = fileset(var.site_source_dir, "**")
}

resource "aws_s3_object" "site" {
  for_each = local.site_files

  bucket = aws_s3_bucket.website.id
  key    = each.value
  source = "${var.site_source_dir}/${each.value}"

  # Faz re-upload quando o conteúdo do ficheiro muda
  etag = filemd5("${var.site_source_dir}/${each.value}")

  content_type = lookup(
    local.mime_types,
    lower(element(reverse(split(".", each.value)), 0)),
    "application/octet-stream"
  )

  # events.json é atualizado com frequência; o resto pode ter cache curta
  cache_control = endswith(each.value, ".json") ? "no-cache" : "public, max-age=300"
}
EOF

# ---------- outputs.tf ----------
cat > outputs.tf <<'EOF'
output "bucket_name" {
  description = "Nome do bucket S3"
  value       = aws_s3_bucket.website.id
}

output "website_endpoint" {
  description = "Endpoint do website S3 (sem protocolo)"
  value       = aws_s3_bucket_website_configuration.website.website_endpoint
}

output "website_url" {
  description = "URL público do site"
  value       = "http://${aws_s3_bucket_website_configuration.website.website_endpoint}"
}

output "uploaded_files" {
  description = "Ficheiros enviados para o bucket"
  value       = sort(keys(aws_s3_object.site))
}
EOF

# ---------- .gitignore ----------
cat > .gitignore <<'EOF'
.terraform/
*.tfstate
*.tfstate.*
crash.log
*.tfplan
.terraform.tfstate.lock.info
EOF

echo -e "${GREEN}Ficheiros gerados:${NC}"
ls -1A
echo
echo -e "${BLUE}Próximos passos:${NC}"
echo "  cd \"$(pwd)\""
echo "  terraform init"
echo "  terraform plan"
echo "  terraform apply"
echo
echo "Depois do apply, o URL do site aparece no output 'website_url'."
echo -e "${YELLOW}Nota: o website endpoint do S3 só serve HTTP. Para HTTPS, acrescenta CloudFront.${NC}"
