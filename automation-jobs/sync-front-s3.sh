#!/usr/bin/env bash
#
# sync-front-s3.sh
# Sincroniza uma pasta local do front com o bucket S3 de HML ou PROD.
# Só envia ficheiros novos ou alterados (mais recentes ou com tamanho diferente
# do que está no bucket).
#
# Uso:
#   bash sync-front-s3.sh <pasta-local> <hml|prod|all> [opções]
#
# Exemplos (a partir de vtes-sim-site/):
#   bash ../automation-jobs/sync-front-s3.sh . hml    # -> s3://homolog-vtes-sim-site
#   bash ../automation-jobs/sync-front-s3.sh . prod   # -> s3://vtes-sim-site (pede confirmação)
#   bash ../automation-jobs/sync-front-s3.sh . all    # HML e depois PROD
#
# <pasta-local> é relativa ao diretório atual e tem de conter index.html.
# Se for omitida, usa ../vtes-sim-site (relativa a este script).
# Se o ambiente for omitido, o script pergunta.
#
# Opções:
#   -n, --dry-run   mostra o que seria enviado, sem enviar nada
#   -d, --delete    apaga do bucket os ficheiros que já não existem na pasta local
#   -y, --yes       não pede confirmação para PROD
#   -h, --help      mostra esta ajuda
#
# Variáveis de ambiente (opcionais):
#   AWS_PROFILE  perfil da AWS CLI (por omissão: vtes-sim)
#
# Os cabeçalhos Content-Type e Cache-Control são os mesmos usados no Terraform
# (terraform-vtes-sim/main.tf), para o "terraform plan" não ver diferenças.

set -euo pipefail

# ---------- Configuração ----------
PROD_BUCKET="vtes-sim-site"
HML_BUCKET="homolog-vtes-sim-site"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEFAULT_SITE_DIR="$SCRIPT_DIR/../vtes-sim-site"
export AWS_PROFILE="${AWS_PROFILE:-vtes-sim}"

DEFAULT_CACHE="public, max-age=300"

# padrão | Content-Type | Cache-Control
TYPED_FILES=(
  "*.html|text/html; charset=utf-8|$DEFAULT_CACHE"
  "*.htm|text/html; charset=utf-8|$DEFAULT_CACHE"
  "*.css|text/css; charset=utf-8|$DEFAULT_CACHE"
  "*.js|application/javascript; charset=utf-8|$DEFAULT_CACHE"
  "*.mjs|application/javascript; charset=utf-8|$DEFAULT_CACHE"
  "*.json|application/json; charset=utf-8|no-cache"
  "*.txt|text/plain; charset=utf-8|$DEFAULT_CACHE"
  "*.md|text/markdown; charset=utf-8|$DEFAULT_CACHE"
)

# Ficheiros locais que nunca vão para o bucket (o bucket é PÚBLICO):
# ocultos, lixo do Windows/OneDrive, backups, state/planos do Terraform, chaves.
EXCLUDES=(
  --exclude ".*"
  --exclude "*/.*"
  --exclude "*desktop.ini"
  --exclude "*Thumbs.db"
  --exclude "*~"
  --exclude "*.tfstate*"
  --exclude "*.tfplan"
  --exclude "*.tfvars"
  --exclude "*.pem"
  --exclude "*.key"
  --exclude "*__pycache__/*"
)

# Cores
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

# ---------- Funções auxiliares ----------
usage() {
  # imprime o bloco de comentários do topo (da linha 3 até à 1.ª linha sem '#')
  awk 'NR < 3 { next } /^#/ { sub(/^# ?/, ""); print; next } { exit }' "${BASH_SOURCE[0]}"
}

die() {
  echo -e "${RED}ERRO: $*${NC}" >&2
  exit 1
}

ask_yes_no() {
  local prompt="$1" yn
  while true; do
    read -r -p "$prompt [y/N]: " yn
    case "${yn:-n}" in
      [Yy]*) return 0 ;;
      [Nn]*) return 1 ;;
      *) echo "Responde y ou n." ;;
    esac
  done
}

lower() {
  echo "$1" | tr '[:upper:]' '[:lower:]'
}

is_env() {
  case "$(lower "$1")" in
    hml|homolog|prod|producao|all|ambos) return 0 ;;
    *) return 1 ;;
  esac
}

check_bucket() {
  local bucket="$1" out
  if ! out=$(aws s3api head-bucket --bucket "$bucket" 2>&1); then
    if grep -q -E '404|Not Found|NoSuchBucket' <<<"$out"; then
      die "o bucket '$bucket' não existe. Crie-o primeiro (ver terraform)."
    elif grep -q -E '403|Forbidden|AccessDenied' <<<"$out"; then
      die "sem acesso ao bucket '$bucket'. A política IAM do perfil '$AWS_PROFILE' não o inclui, ou o bucket pertence a outra conta."
    else
      die "não foi possível verificar o bucket '$bucket':\n$out"
    fi
  fi
}

website_url() {
  local bucket="$1" region
  region=$(aws s3api get-bucket-location --bucket "$bucket" \
    --query LocationConstraint --output text 2>/dev/null || true)
  [[ -z "$region" || "$region" == "None" || "$region" == "null" ]] && region="us-east-1"

  aws s3api get-bucket-website --bucket "$bucket" >/dev/null 2>&1 || return 0

  case "$region" in
    us-east-1|us-west-1|us-west-2|eu-west-1|ap-southeast-1|ap-southeast-2|ap-northeast-1|sa-east-1|us-gov-west-1)
      echo "http://${bucket}.s3-website-${region}.amazonaws.com" ;;
    *)
      echo "http://${bucket}.s3-website.${region}.amazonaws.com" ;;
  esac
}

sync_bucket() {
  local bucket="$1"
  local dest="s3://${bucket}"
  local common=(--no-progress "${EXCLUDES[@]}")
  local handled=()
  local entry pattern ctype cache

  (( DRY_RUN )) && common+=(--dryrun)
  (( DELETE )) && common+=(--delete)

  # 1) Tipos conhecidos, com Content-Type/Cache-Control explícitos
  for entry in "${TYPED_FILES[@]}"; do
    IFS='|' read -r pattern ctype cache <<<"$entry"
    handled+=(--exclude "$pattern")
    aws s3 sync "$SITE_DIR" "$dest" \
      --exclude "*" --include "$pattern" "${common[@]}" \
      --content-type "$ctype" --cache-control "$cache"
  done

  # 2) Restantes ficheiros (imagens, fontes, ...): Content-Type detetado pela AWS CLI
  aws s3 sync "$SITE_DIR" "$dest" \
    "${handled[@]}" "${common[@]}" \
    --cache-control "$DEFAULT_CACHE"
}

# ---------- Argumentos ----------
TARGET=""
SITE_DIR_ARG=""
DRY_RUN=0
DELETE=0
ASSUME_YES=0
POSITIONAL=()

while [[ $# -gt 0 ]]; do
  case "$1" in
    -n|--dry-run|--dryrun) DRY_RUN=1 ;;
    -d|--delete)           DELETE=1 ;;
    -y|--yes)              ASSUME_YES=1 ;;
    -h|--help)             usage; exit 0 ;;
    -*)                    usage; die "opção desconhecida: $1" ;;
    *)                     POSITIONAL+=("$1") ;;
  esac
  shift
done

# <pasta-local> <ambiente>; aceita também só o ambiente, só a pasta, ou a ordem trocada
case ${#POSITIONAL[@]} in
  0) ;;
  1)
    if is_env "${POSITIONAL[0]}"; then
      TARGET="$(lower "${POSITIONAL[0]}")"
    else
      SITE_DIR_ARG="${POSITIONAL[0]}"
    fi
    ;;
  2)
    if is_env "${POSITIONAL[0]}" && ! is_env "${POSITIONAL[1]}"; then
      TARGET="$(lower "${POSITIONAL[0]}")"; SITE_DIR_ARG="${POSITIONAL[1]}"
    else
      SITE_DIR_ARG="${POSITIONAL[0]}"; TARGET="$(lower "${POSITIONAL[1]}")"
    fi
    ;;
  *)
    usage
    die "argumentos a mais: use <pasta-local> <hml|prod|all>."
    ;;
esac

if [[ -z "$TARGET" ]]; then
  if [[ -t 0 ]]; then
    read -r -p "Ambiente (hml/prod/all) [hml]: " TARGET
    TARGET="$(lower "${TARGET:-hml}")"
  else
    usage
    die "indique o ambiente: hml, prod ou all."
  fi
fi

case "$TARGET" in
  hml|homolog)    BUCKETS=("$HML_BUCKET") ;;
  prod|producao)  BUCKETS=("$PROD_BUCKET") ;;
  all|ambos)      BUCKETS=("$HML_BUCKET" "$PROD_BUCKET") ;;
  *)              die "ambiente inválido: '$TARGET' (use hml, prod ou all)." ;;
esac

# ---------- Verificações ----------
echo -e "${BLUE}================================================${NC}"
echo -e "${BLUE}  VTES Simulator – Sync do front para o S3      ${NC}"
echo -e "${BLUE}================================================${NC}"
echo

command -v aws >/dev/null 2>&1 || die "AWS CLI não encontrada. Instale a AWS CLI v2."

SITE_DIR_INPUT="${SITE_DIR_ARG:-$DEFAULT_SITE_DIR}"
[[ -d "$SITE_DIR_INPUT" ]] || die "pasta local não encontrada: $SITE_DIR_INPUT (a partir de $(pwd))"
SITE_DIR="$(cd "$SITE_DIR_INPUT" && pwd)"
# Proteção: o bucket é público, por isso só sincroniza uma pasta que seja mesmo o front
[[ -f "$SITE_DIR/index.html" ]] || die "não encontrei index.html em $SITE_DIR. Esta pasta não parece ser o front; nada foi enviado."

if ! CALLER_ARN=$(aws sts get-caller-identity --query Arn --output text 2>&1); then
  die "credenciais AWS inválidas para o perfil '$AWS_PROFILE':\n$CALLER_ARN"
fi

for bucket in "${BUCKETS[@]}"; do
  check_bucket "$bucket"
done

echo "  Pasta local : $SITE_DIR"
echo "  Perfil AWS  : $AWS_PROFILE ($CALLER_ARN)"
echo "  Destino(s)  : ${BUCKETS[*]}"
echo "  Modo        : $( (( DRY_RUN )) && echo 'dry-run (nada é enviado)' || echo 'envio real')"
echo "  Apagar órfãos no bucket: $( (( DELETE )) && echo 'sim' || echo 'não')"
echo

if [[ " ${BUCKETS[*]} " == *" $PROD_BUCKET "* ]] && (( ! DRY_RUN )) && (( ! ASSUME_YES )); then
  echo -e "${YELLOW}Atenção: vai atualizar PRODUÇÃO (s3://$PROD_BUCKET).${NC}"
  ask_yes_no "Continuar?" || { echo "Cancelado."; exit 0; }
  echo
fi

# ---------- Sync ----------
for bucket in "${BUCKETS[@]}"; do
  echo -e "${GREEN}>> A sincronizar $SITE_DIR -> s3://$bucket${NC}"
  sync_bucket "$bucket"
  url="$(website_url "$bucket")"
  echo -e "${GREEN}>> s3://$bucket concluído.${NC}${url:+ Site: $url}"
  echo
done

if (( DRY_RUN )); then
  echo -e "${YELLOW}Dry-run: nenhum ficheiro foi enviado. Corra sem --dry-run para aplicar.${NC}"
else
  echo -e "${GREEN}Sync concluído.${NC}"
fi
