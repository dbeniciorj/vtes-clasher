# VTES Simulator & Calendar

Site com duas abas:

- **Calendário de eventos** de V:TES de Portugal, Espanha, Brasil e Estados Unidos, com dados do
  [calendário oficial VEKN](https://www.vekn.net/event-calendar) e da
  [Liga Española (vtes.es)](https://vtes.es/en/league/season).
- **Simulador de combates**: o utilizador envia 2 a 5 decks e a IA devolve uma simulação.

O site é **estático** (HTML + CSS + JS + `events.json`) e fica hospedado em **buckets S3** com
*static website hosting*. Existem dois ambientes: **HML** (homologação) e **PROD** (produção).
Em produção, o site é servido pela **Cloudflare** em `https://dbenicio.com/vtes/`.

---

## Arquitetura

```
                          Cloudflare  (zona dbenicio.com)
                 ┌───────────────────────────────────────────────┐
  Visitante ───▶ │ DNS · TLS / redirect HTTP→HTTPS               │
   (HTTPS)       │ Redirect Rules  (/vtes → /vtes/, raiz → ...)  │
                 │ Worker "vtes-proxy"  (rota /vtes/*)           │ ──HTTP──▶  S3 PROD
                 └───────────────────────────────────────────────┘           vtes-sim-site

  Você, para validar ───────────────── HTTP direto ─────────────────────▶  S3 HML
                                                                           homolog-vtes-sim-site
```

| Ambiente | Bucket S3 (us-east-1) | URL |
|---|---|---|
| **PROD** | `vtes-sim-site` | https://dbenicio.com/vtes/ (via Cloudflare)<br>direto: http://vtes-sim-site.s3-website-us-east-1.amazonaws.com |
| **HML** | `homolog-vtes-sim-site` | http://homolog-vtes-sim-site.s3-website-us-east-1.amazonaws.com (direto no S3, sem Cloudflare) |

O endpoint *website* do S3 só responde em **HTTP**. O HTTPS do site de produção é feito pela Cloudflare.

---

## Estrutura do repositório

```
.
├── README.md                              # este documento (NÃO vai para o S3)
├── bootstrap-tf-s3-serverless-front.sh    # gera o Terraform de um bucket S3 website
├── terraform-vtes-sim/                    # Terraform do bucket de PROD
├── automation-jobs/
│   ├── update_vekn_events.py              # eventos da VEKN    -> vtes-sim-site/events.json
│   ├── update_vtes_es_events.py           # eventos do vtes.es -> vtes-sim-site/events.json
│   ├── events_common.py                   # código partilhado pelos dois scripts acima
│   └── sync-front-s3.sh                   # sincroniza a pasta do front com o S3 (HML/PROD)
└── vtes-sim-site/                         # o front: SÓ esta pasta vai para o S3
    ├── index.html
    ├── styles.css
    ├── app.js
    ├── events.json                        # gerado pelos scripts de eventos
    └── example-deck-*.txt
```

> Tudo o que está em `vtes-sim-site/` fica **público** no bucket. Não guarde ali documentação
> interna, credenciais nem ficheiros de Terraform.

---

## Pré-requisitos

- **WSL (Ubuntu)**, Linux ou macOS, com `bash`
- **Python 3.8+** (`python3`). Os scripts só usam a biblioteca padrão, não precisa de `pip install`.
- **AWS CLI v2** com o perfil `vtes-sim` (ver abaixo)
- **OpenTofu/Terraform ≥ 1.5**, só para alterar a infraestrutura

### Credenciais AWS

| Item | Valor |
|---|---|
| Perfil da AWS CLI | `vtes-sim` |
| Utilizador IAM | `vtes-sim-deployer` |
| Política (inline) | `vtes-sim-s3`: `s3:*` em `vtes-sim-*` e `homolog-vtes-sim-*` (buckets e objetos) |

```bash
aws configure --profile vtes-sim                  # Access Key, Secret, região us-east-1, json
aws sts get-caller-identity --profile vtes-sim    # deve mostrar .../user/vtes-sim-deployer
```

O `sync-front-s3.sh` usa o perfil `vtes-sim` por padrão. Para o Terraform: `export AWS_PROFILE=vtes-sim`.

---

## Workflow de publicação (manual)

**Regra: tudo passa primeiro por HML. Só se HML estiver conforme o esperado é que se publica em PROD.**

```
 ┌──────────────────────────────┐
 │ 1. Atualizar os eventos      │  python3 automation-jobs/update_vekn_events.py
 │    (e/ou editar o front)     │  python3 automation-jobs/update_vtes_es_events.py
 └──────────────┬───────────────┘
                ▼
 ┌──────────────────────────────┐
 │ 2. Testar localmente         │  cd vtes-sim-site && python3 -m http.server 8000
 └──────────────┬───────────────┘
                ▼
 ┌──────────────────────────────┐
 │ 3. Publicar em HML           │  bash ../automation-jobs/sync-front-s3.sh . hml --dry-run
 │                              │  bash ../automation-jobs/sync-front-s3.sh . hml
 └──────────────┬───────────────┘
                ▼
 ┌──────────────────────────────┐   não   ┌──────────────────────────────┐
 │ 4. HML está OK? (checklist)  │ ──────▶ │ corrigir e voltar ao passo 1 │
 └──────────────┬───────────────┘         └──────────────────────────────┘
                │ sim
                ▼
 ┌──────────────────────────────┐
 │ 5. Publicar em PROD          │  bash ../automation-jobs/sync-front-s3.sh . prod
 └──────────────┬───────────────┘
                ▼
 ┌──────────────────────────────┐
 │ 6. Limpar o cache Cloudflare │  Purge de /vtes/events.json (e do que mudou)
 └──────────────┬───────────────┘
                ▼
 ┌──────────────────────────────┐
 │ 7. Validar PROD              │  https://dbenicio.com/vtes/
 └──────────────────────────────┘
```

### Passo a passo

Os comandos assumem que você está na raiz do projeto, no WSL.

**1. Atualizar os eventos.** Rode **nesta ordem**: primeiro a VEKN, depois o vtes.es.
```bash
python3 automation-jobs/update_vekn_events.py
python3 automation-jobs/update_vtes_es_events.py
```

**2. Testar localmente.** Abrir o `index.html` direto (`file://`) não carrega o `events.json`, por isso use um servidor local:
```bash
cd vtes-sim-site
python3 -m http.server 8000      # abrir http://localhost:8000 e parar com Ctrl+C
```

**3. Publicar em HML** (ainda dentro de `vtes-sim-site/`):
```bash
bash ../automation-jobs/sync-front-s3.sh . hml --dry-run   # mostra o que vai subir
bash ../automation-jobs/sync-front-s3.sh . hml
```

**4. Validar HML** em http://homolog-vtes-sim-site.s3-website-us-east-1.amazonaws.com:
- [ ] a página abre e as duas abas funcionam
- [ ] o calendário abre no mês atual e os filtros de país e mês funcionam
- [ ] os eventos mostram bandeira, hora, tipo, local e cidade
- [ ] "Última atualização dos dados" mostra a data/hora da última atualização
- [ ] os links dos eventos abrem na VEKN ou no vtes.es
- [ ] o formulário do simulador funciona
- [ ] a consola do navegador (F12) não mostra erros

Se algo estiver errado: corrija e volte ao passo 1 ou 2. **Não publique em PROD.**

**5. Publicar em PROD** (o script pede confirmação):
```bash
bash ../automation-jobs/sync-front-s3.sh . prod
```

**6. Limpar o cache da Cloudflare.** Sem isso, os visitantes podem ver a versão anterior durante
até 4 horas (ver [Cloudflare › Pontos de atenção](#pontos-de-atenção)).

**7. Validar PROD** em https://dbenicio.com/vtes/ (mesmo checklist do passo 4).

---

## Comandos

### Eventos do calendário (`events.json`)

| Script | Fonte | O que faz |
|---|---|---|
| `update_vekn_events.py` | vekn.net | Lê o calendário de PT, ES, US e BR, dos 2 meses anteriores ao atual até 5 meses à frente, e a página de cada evento (hora, local, cidade). Reescreve os eventos VEKN e **preserva** os de outras fontes. |
| `update_vtes_es_events.py` | vtes.es | Lê os torneios jogados e anunciados da temporada e a página de cada um. Acrescenta os que **não** existem na VEKN e substitui os da execução anterior. Ignora torneios cancelados. |

```bash
python3 automation-jobs/update_vekn_events.py
python3 automation-jobs/update_vtes_es_events.py
```

- **Eventos repetidos: a VEKN tem prioridade.** Um torneio do vtes.es é considerado repetido quando
  existe um evento VEKN **na mesma data** com nome parecido, mesmo local ou mesma cidade.
  Os dois scripts mostram no fim a lista de repetidos (`=`) e de acrescentados (`+`).
  Se a VEKN passar a ter um torneio que antes só existia no vtes.es, o script da VEKN remove
  a cópia do vtes.es.
- **Falhas:** se uma página de calendário ou de lista falhar, o `events.json` **não é alterado**
  e o script sai com código 1. Se só a página de um evento falhar, são reaproveitados os dados
  da execução anterior.
- **Configuração** (constantes no topo de cada script):
  - `update_vekn_events.py`: `COUNTRIES`, `MONTHS_BACK`, `MONTHS_AHEAD`
  - `update_vtes_es_events.py`: `SEASON_URL`. **Atualizar no início de cada temporada**
    (ex.: `.../season/6/2027-2028/tournament`, ver https://vtes.es/en/league/season).

### Sincronização com o S3 (`sync-front-s3.sh`)

```bash
bash <caminho>/sync-front-s3.sh <pasta-local> <hml|prod|all> [opções]

# exemplos, a partir de vtes-sim-site/
bash ../automation-jobs/sync-front-s3.sh . hml              # homolog-vtes-sim-site
bash ../automation-jobs/sync-front-s3.sh . prod             # vtes-sim-site (pede confirmação)
bash ../automation-jobs/sync-front-s3.sh . all              # HML e depois PROD
bash ../automation-jobs/sync-front-s3.sh . hml --dry-run    # só mostra, não envia
bash ../automation-jobs/sync-front-s3.sh -h                 # ajuda
```

| Opção | Efeito |
|---|---|
| `-n`, `--dry-run` | mostra o que seria enviado, sem enviar |
| `-d`, `--delete` | apaga do bucket os ficheiros que já não existem na pasta local (use antes com `--dry-run`) |
| `-y`, `--yes` | não pede confirmação para PROD |

- **O que envia:** só ficheiros novos ou alterados, ou seja, mais recentes ou com tamanho diferente do que está no bucket.
- **Cabeçalhos:** envia `Content-Type` (com `charset=utf-8`) e `Cache-Control` iguais aos do Terraform.
  O `events.json` vai com `no-cache`.
- **Proteções:**
  - recusa pastas sem `index.html`, para nunca publicar a raiz do projeto por engano;
  - nunca envia ficheiros ocultos, `desktop.ini`/`Thumbs.db`, `*.tfstate*`, `*.tfplan`, `*.tfvars`, `*.pem`, `*.key` nem `__pycache__`;
  - antes de enviar, confirma as credenciais e se o bucket existe.
- **Perfil AWS:** `vtes-sim` por padrão. Para usar outro: `AWS_PROFILE=outro bash ...`.

### Infraestrutura (Terraform / OpenTofu)

O bucket de **PROD** é gerido em `terraform-vtes-sim/`. Esse Terraform cria:
- o bucket;
- o *website hosting* (`index.html` também como página de erro);
- a política de leitura pública;
- o upload dos ficheiros de `vtes-sim-site/`, com os mesmos cabeçalhos do script de sync.

```bash
cd terraform-vtes-sim
export AWS_PROFILE=vtes-sim
terraform init
PLAN_FILE="plan-$(date +%s).tfplan"
terraform plan -out="${PLAN_FILE}"
terraform apply "${PLAN_FILE}"
```

- No dia a dia, o conteúdo do site é publicado com o `sync-front-s3.sh`. O Terraform serve para
  mudanças de infraestrutura. Um `terraform apply` também reenvia os ficheiros que mudaram, sem
  conflito com o sync.
- O bucket de **HML** (`homolog-vtes-sim-site`) não está neste Terraform.
- Para gerar o Terraform de um bucket novo com a mesma configuração, use o bootstrap. Ele pergunta
  o projeto, o bucket, a região e a pasta, e cria `terraform-<projeto>/`:
  ```bash
  bash bootstrap-tf-s3-serverless-front.sh
  ```
  Os nomes de bucket S3 são **globais**. Se o nome já estiver em uso noutra conta, dá `BucketAlreadyExists`.

---

## Cloudflare

O domínio `dbenicio.com` está na Cloudflare. É lá que estão as configurações de **DNS**, **TLS**
(certificado e redirect HTTP→HTTPS), as **Redirect Rules** e o **Worker** que entrega o site em
`https://dbenicio.com/vtes/` a partir do bucket S3 de PROD. As respostas trazem cabeçalhos
`x-amz-*` do S3, o que confirma o proxy.

| O quê | Para que serve | Link |
|---|---|---|
| **DNS** | registos da zona `dbenicio.com` | [DNS records](https://dash.cloudflare.com/b9715604eb59118aa4f4476a40613c39/dbenicio.com/dns/records) |
| **TLS** | certificado de borda e HTTPS (*Always Use HTTPS*, HTTP→HTTPS) | [Edge Certificates](https://dash.cloudflare.com/b9715604eb59118aa4f4476a40613c39/dbenicio.com/ssl-tls/edge-certificates) |
| **Worker `vtes-proxy`** | serve `/vtes/*` buscando o conteúdo no S3 de PROD | [vtes-proxy (production)](https://dash.cloudflare.com/b9715604eb59118aa4f4476a40613c39/workers/services/view/vtes-proxy/production) |
| **Workers Routes** | liga a rota `dbenicio.com/vtes*` ao Worker | [Workers da zona](https://dash.cloudflare.com/b9715604eb59118aa4f4476a40613c39/dbenicio.com/workers) |
| **Redirect Rules** | ex.: `/vtes` → `/vtes/`; raiz `dbenicio.com/` → LinkedIn | [Redirect Rules](https://dash.cloudflare.com/b9715604eb59118aa4f4476a40613c39/dbenicio.com/rules/redirect-rules) |
| **Cache** | purge depois de publicar em PROD | [Caching › Configuration](https://dash.cloudflare.com/b9715604eb59118aa4f4476a40613c39/dbenicio.com/caching/configuration) |

### Pontos de atenção

1. **Cache.** A Cloudflare guarda em cache os ficheiros de `/vtes/`, **incluindo o `events.json`**
   (`CF-Cache-Status: HIT`, `Cache-Control: max-age=14400` = 4 h), mesmo o S3 enviando `no-cache`.
   Depois de cada publicação em PROD, faça purge: **Caching › Configuration › Purge Cache ›
   Custom Purge** com `https://dbenicio.com/vtes/events.json` (e os outros ficheiros que mudaram),
   ou **Purge Everything**. Para conferir:
   ```bash
   curl -sI https://dbenicio.com/vtes/events.json | grep -i -E 'cf-cache-status|last-modified'
   ```
2. **O Worker aponta para o bucket de PROD.** Se o nome ou a região do bucket mudarem, atualize o
   `vtes-proxy`. Caso contrário, `https://dbenicio.com/vtes/` deixa de funcionar.
3. **Redirect Rules.** A regra que manda a raiz `dbenicio.com/` para o LinkedIn não pode apanhar
   `/vtes`. Ao criar ou alterar regras, confira a ordem e os filtros e teste `https://dbenicio.com/vtes/`.
4. **TLS.** O S3 *website* só fala HTTP, e o HTTPS termina na Cloudflare. Se mudar o modo SSL/TLS
   da zona ou as regras de HTTPS, valide que `/vtes/` continua a abrir.
5. **HML não passa pela Cloudflare.** É acedido direto no endpoint S3 (HTTP), sem cache, por isso
   o que se vê em HML é exatamente o que está no bucket.

---

## Formato do `events.json`

```json
{
  "generated_at": "2026-11-22T18:00:00+00:00",
  "sources": {
    "vekn": "https://www.vekn.net/event-calendar",
    "vtes.es": "https://vtes.es/en/league/season/5/2026-2027/tournament"
  },
  "countries": ["PT", "ES", "US", "BR"],
  "months": ["2026-09", "2026-10", "2026-11"],
  "events": [
    {
      "id": "13588",
      "title": "Sabadell 2026. Built to last",
      "date": "2026-11-15",
      "time": "09:00",
      "country": "ES",
      "type": "Standard Constructed",
      "venue": "Snafu Battlegrounds",
      "location": "Sabadell, Catalunya, Spain",
      "link": "https://www.vekn.net/event-calendar/event/13588",
      "source": "vekn"
    },
    {
      "id": "vtes.es-184",
      "title": "1ª Jornada Liga Anarcogaditana",
      "date": "2026-09-20",
      "time": "09:30",
      "country": "ES",
      "type": "Standard Constructed",
      "league": "Liga Gaditana",
      "venue": "Asociación Hijos de Caín",
      "location": "La Línea de la Concepción, Spain",
      "link": "https://vtes.es/en/league/tournament/184/1a-jornada-liga-anarcogaditana/view",
      "source": "vtes.es"
    }
  ]
}
```

- `generated_at`: data/hora da última atualização. É o valor mostrado em "Última atualização dos dados".
- `months`: janela da VEKN. O filtro de meses do site junta estes meses aos meses de todos os eventos.
- `source`: `vekn` ou `vtes.es`. Os eventos do vtes.es têm `id` com o prefixo `vtes.es-`.

---

## Frontend

- `app.js` carrega o `events.json`, preenche o filtro de meses e desenha os cartões dos eventos.
  O filtro abre no mês atual.
- As bandeiras vêm da biblioteca [flag-icons](https://github.com/lipis/flag-icons) (MIT), carregada do
  jsDelivr com versão fixa e hash SRI em `index.html`.
- Os textos que vêm de fora (VEKN, vtes.es) são sempre escapados antes de entrar no HTML, e os links
  dos eventos só podem apontar para `vekn.net` ou `vtes.es`.

### Simulador

1. O utilizador indica 2 a 5 decks:
   - upload de `.txt` (formato TWD / descrição textual)
   - colar o texto
   - link `https://vdb.im/decks/XXXX` ou `https://vtesdecks.com/deck/...`
2. O frontend faz `POST` JSON para o endpoint configurado em `app.js`:
   ```js
   const CONFIG = {
     SIMULATION_ENDPOINT: 'https://seu-n8n.../webhook/vtes-sim',
     USE_MOCK: true   // false quando o backend estiver pronto
   };
   ```
3. Backend (N8N):
   - recebe `{ decks: [ {name, url, text}, ... ], source: "vtes-sim-frontend" }`;
   - se houver `url`, busca o deck (vdb.im / vtesdecks.com);
   - monta o prompt para o modelo (Ollama);
   - devolve `{ "result": "texto do relatório da simulação..." }`.

### Publicidade

As laterais têm `<aside class="ad-sidebar">` com placeholders. Substitua o conteúdo de `.ad-placeholder`
pelo código do AdSense ou de afiliados. Em ecrãs com menos de 1100px as laterais ficam escondidas.

---

## Troubleshooting

| Sintoma | Causa provável | O que fazer |
|---|---|---|
| `sem acesso ao bucket` (403) no sync | a política IAM não inclui o bucket, ou o bucket é de outra conta | ver [Credenciais AWS](#credenciais-aws). Confirmar o dono com `aws s3api list-buckets` usando o login de admin |
| `o bucket ... não existe` no sync | bucket não criado | criar com Terraform ou o bootstrap |
| `No valid credential sources found` no Terraform | perfil não definido | `export AWS_PROFILE=vtes-sim` |
| `BucketAlreadyExists` | nome de bucket já usado noutra conta AWS | escolher outro nome |
| PROD mostra dados antigos | cache da Cloudflare | purge (ver [Pontos de atenção](#pontos-de-atenção)) |
| script de eventos termina com `events.json NÃO foi alterado` | site de origem fora do ar ou com o layout mudado | tentar mais tarde; se persistir, rever o parser do script |
| o site local abre sem eventos | `index.html` aberto como `file://` | usar `python3 -m http.server` |
