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
