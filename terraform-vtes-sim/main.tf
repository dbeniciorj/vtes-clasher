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
