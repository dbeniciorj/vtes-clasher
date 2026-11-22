# VTES Simulator & Calendar (Static Site for S3)

Site estático serverless pronto para hospedar no **Amazon S3** (modo website) com tema escuro, duas abas e espaços de publicidade laterais.

## Estrutura

```
vtes-sim-site/
├── index.html      # Página principal com tabs
├── styles.css      # Tema escuro
├── app.js          # Lógica (calendário + formulário de simulação)
├── events.json     # Dados do calendário (atualizado por Lambda)
└── README.md
```

## Deploy no S3 (Website Hosting)

1. Cria um bucket S3 (ex: `vtes-sim.example.com`).
2. Ativa **Static website hosting**.
3. Faz upload de todos os ficheiros.
4. (Opcional) CloudFront + certificado ACM para HTTPS e domínio personalizado.
5. Configura política de bucket pública para leitura dos objetos.

## Calendário VEKN

- O frontend carrega `events.json`.
- Filtros por país (PT / ES / Todos) e mês.
- **Recomendado**: Lambda diário que faz scrape de:

  ```
  https://www.vekn.net/event-calendar?country=PT&date=YYYY-MM
  https://www.vekn.net/event-calendar?country=ES&date=YYYY-MM
  ```

  e grava o JSON no S3 (sobrescreve `events.json`).

### Exemplo de scraper (Python / Lambda)

```python
import requests
from bs4 import BeautifulSoup
import json
from datetime import datetime
from dateutil.relativedelta import relativedelta

def scrape_country(country, months=3):
    events = []
    now = datetime.now()
    for n in range(months):
        d = now + relativedelta(months=n)
        date_str = d.strftime("%Y-%m")
        url = f"https://www.vekn.net/event-calendar?country={country}&date={date_str}"
        resp = requests.get(url, timeout=15)
        soup = BeautifulSoup(resp.text, "lxml")
        # Ajustar seletores conforme a estrutura atual da tabela .vekn-cal
        # (ver gist: https://gist.github.com/Stolas/b99790cb104154837a311f5c1124e3c6)
        # ...
    return events

# Unir PT + ES, deduplicar, gravar no S3
```

## Simulador

1. O utilizador indica 2–5 decks de uma das formas:
   - Upload de `.txt` (formato TWD / descrição textual)
   - Colar texto
   - Link `https://vdb.im/decks/XXXX` ou `https://vtesdecks.com/deck/...`

2. O frontend faz `POST` JSON para o endpoint configurado em `app.js`:

   ```js
   const CONFIG = {
     SIMULATION_ENDPOINT: 'https://seu-n8n.../webhook/vtes-sim',
     USE_MOCK: false   // muda para false quando o N8N estiver pronto
   };
   ```

3. No **N8N**:
   - Recebe o payload `{ decks: [ {name, url, text}, ... ] }`
   - Se houver `url`, faz fetch / scrape do deck (vdb.im e vtesdecks.com)
   - Monta o prompt para o **Ollama (Bert)**
   - Devolve `{ "result": "texto do relatório da simulação..." }`

### Payload de exemplo enviado ao N8N

```json
{
  "decks": [
    {
      "name": "Tzimise nac",
      "url": null,
      "text": "Deck Name: Tzimise nac\nAuthor: Mark Figuerola\n..."
    },
    {
      "name": "Deck 2",
      "url": "https://vdb.im/decks/13317",
      "text": null
    }
  ],
  "source": "vtes-sim-frontend"
}
```

## Publicidade (monetização)

Nos laterais existem `<aside class="ad-sidebar">` com placeholders.

Substitui o conteúdo de `.ad-placeholder` pelo teu código AdSense / Media.net / afiliados:

```html
<script async src="https://pagead2.googlesyndication.com/pagead/js/adsbygoogle.js?client=ca-pub-XXXX" crossorigin="anonymous"></script>
<ins class="adsbygoogle"
     style="display:block"
     data-ad-client="ca-pub-XXXX"
     data-ad-slot="YYYY"
     data-ad-format="auto"
     data-full-width-responsive="true"></ins>
<script>
     (adsbygoogle = window.adsbygoogle || []).push({});
</script>
```

Em ecrãs < 1100px as laterais são escondidas (mobile-friendly).

## Tema

Fundo escuro (`#0d0d12`), painéis `#16161f`, acento vermelho sangue (`#c0392b`) — típico da comunidade VTES.

## Próximos passos sugeridos

1. Implementar o Lambda de scrape + EventBridge (cron diário).
2. Configurar o webhook N8N + Ollama (Bert).
3. Adicionar tratamento de CORS no N8N se necessário.
4. (Opcional) Cloudflare Workers em vez de Lambda para o scrape (mais barato para tráfego baixo).
5. Adicionar mais meses no filtro ou deteção automática do mês corrente.

---

Feito para a comunidade VTES. Bom jogo!
