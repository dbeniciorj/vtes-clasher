# n8n em casa: minikube + Cloudflare Tunnel

O n8n roda no Ubuntu de casa, num minikube de nó único (control plane + worker), e fica
publicado em **https://n8n.dbenicio.com** por um **Cloudflare Tunnel**. Não precisa de IP fixo,
de abrir portas no router nem de load balancer: o `cloudflared` abre a conexão **de dentro para
fora** até a Cloudflare.

```
 Internet ──HTTPS──▶ Cloudflare  (n8n.dbenicio.com)
                       │  Access: o editor pede login; /webhook/* fica aberto
                       │
                       │  túnel (conexão de saída iniciada em casa)
                       ▼
 Ubuntu em casa ── minikube ── namespace mycoe
                                 ├─ cloudflared (2 réplicas)
                                 │      └──▶ n8n-service:5678 (ClusterIP)
                                 └─ n8n ──▶ volume hostPath /data/n8n
```

Arquivos deste diretório:

| Arquivo | O que é |
|---|---|
| `kustomization.yaml` | overlay sobre `../k8s`: só o n8n (+ ConfigMap do workflow), URL pública, Service ClusterIP, correção de permissões do volume |
| `cloudflared.yaml` | Deployment do conector do túnel (lê o token de um Secret criado à mão) |
| `minikube.service` | unit do systemd para o minikube subir no boot |

> O workflow importado (mycoe, `coe-cv-scoring-v1`) chama `ollama-service` e `dashboard-service`.
> Essas etapas falham até esses serviços existirem. Para incluir o Ollama, tire `ollama*` das listas
> de remoção do `kustomization.yaml`. O minikube vai precisar de 4 a 8 GiB de RAM.

---

## 0. Pré-requisitos (no Ubuntu)

```bash
minikube status                 # host/kubelet/apiserver: Running
minikube profile list           # anote o Driver (docker ou none), usado no passo 5
kubectl version --client        # ou use: alias kubectl="minikube kubectl --"
```

Leve os diretórios `k8s/` e `k8s-home/` para o servidor, com `git pull` ou `scp`:

```bash
# a partir do seu PC (WSL), na raiz do projeto:
scp -r k8s k8s-home SEU_USUARIO@IP_DO_UBUNTU:~/vtes-clasher/
```

---

## 1. Proteger o editor com Cloudflare Access (ANTES de publicar)

**Por quê:** no primeiro acesso, o n8n pede para criar a conta de dono (owner). Quem abrir a URL
primeiro vira administrador. Com o Access, só o seu email chega ao editor. Os webhooks
continuam públicos para poderem ser chamados de fora.

No painel **Zero Trust** (https://one.dash.cloudflare.com):

1. Se for a primeira vez: escolha um *team name* e o plano **Free** (até 50 utilizadores).
2. **Access › Applications › Add an application › Self-hosted**
   - Name: `n8n editor`
   - Domain: `n8n.dbenicio.com` (path vazio)
   - Policy: **Allow** › Include › **Emails** › o seu email
   - Login: *One-time PIN* (vem por padrão; recebe um código por email)
3. **Add an application › Self-hosted** de novo:
   - Name: `n8n webhooks`
   - Domain: `n8n.dbenicio.com`, path `webhook/*`
   - Policy: action **Bypass** › Include › **Everyone**

A Cloudflare aplica primeiro a regra do path mais específico. Por isso `/webhook/*` fica aberto
e o resto pede login.

---

## 2. Criar o túnel e a rota

Em **Zero Trust › Networks › Tunnels**:

1. **Create a tunnel** › tipo **Cloudflared** › nome `casa-minikube` › Save.
2. Na tela *Install and run a connector*, **copie só o token**: a string longa depois de
   `--token` ou de `service install`. **Não rode** o comando de instalação mostrado ali,
   porque o conector vai rodar dentro do Kubernetes.
3. **Next** › **Public Hostname** (em painéis mais novos: *Published application routes*):
   - Subdomain: `n8n` · Domain: `dbenicio.com` · Path: vazio
   - Service: Type **HTTP** · URL: `n8n-service.mycoe.svc.cluster.local:5678`
4. **Save**. A Cloudflare cria sozinha o registo DNS `n8n` › `<id>.cfargotunnel.com` (proxied).

---

## 3. Fazer o deploy (no Ubuntu)

```bash
cd ~/vtes-clasher

# namespace + Secret com o token do túnel (o token não fica no histórico do shell)
kubectl create namespace mycoe --dry-run=client -o yaml | kubectl apply -f -
read -rsp 'Token do túnel: ' TUNNEL_TOKEN; echo
kubectl -n mycoe create secret generic cloudflared-token --from-literal=token="$TUNNEL_TOKEN"
unset TUNNEL_TOKEN

# n8n + cloudflared
kubectl apply -k k8s-home
kubectl -n mycoe rollout status deploy/n8n --timeout=5m
kubectl -n mycoe rollout status deploy/cloudflared --timeout=2m
```

---

## 4. Verificar

```bash
kubectl -n mycoe get pods
# n8n-...          1/1 Running
# cloudflared-...  1/1 Running   (x2)

kubectl -n mycoe logs deploy/cloudflared | grep -i "registered tunnel connection"

# editor protegido: deve responder 302 para o login do Access (cloudflareaccess.com)
curl -sI https://n8n.dbenicio.com/ | grep -i -E '^HTTP|^location'

# webhook liberado: NÃO pode ser 302 (qualquer resposta do n8n, ex.: 404, mostra que o bypass funciona)
curl -s -o /dev/null -w '%{http_code}\n' https://n8n.dbenicio.com/webhook/mycoe-match
```

O painel em **Networks › Tunnels** deve mostrar o túnel como **Healthy**.

Depois, abra https://n8n.dbenicio.com, entre com o PIN que chega por email e **crie logo a conta
de dono do n8n**.

---

## 5. Continuar funcionando depois de reiniciar

```bash
# minikube sobe no boot (os pods voltam sozinhos com ele)
sudo cp k8s-home/minikube.service /etc/systemd/system/minikube.service
sudo sed -i "s/SEU_USUARIO/$USER/" /etc/systemd/system/minikube.service   # driver none: use root
command -v minikube        # se não for /usr/local/bin/minikube, corrija ExecStart/ExecStop
sudo systemctl daemon-reload && sudo systemctl enable minikube

# o servidor não pode suspender nem hibernar
sudo systemctl mask sleep.target suspend.target hibernate.target hybrid-sleep.target
```

Para testar: `sudo reboot` e, depois, repetir o passo 4.

---

## 6. Backup

Os dados do n8n (workflows, credenciais e a **chave de criptografia** em `config`) ficam em
`/data/n8n`, dentro do nó do minikube. **`minikube delete` apaga tudo.**

```bash
kubectl -n mycoe exec deploy/n8n -c n8n -- tar czf - -C /home/node/.n8n . > n8n-backup-$(date +%F).tgz
```

Guarde o backup fora do servidor. Sem o ficheiro `config`, as credenciais salvas no n8n não podem
ser recuperadas.

---

## Pontos de atenção

- **Redirect Rule da raiz.** A regra que manda `dbenicio.com/` para o LinkedIn tem de filtrar o
  hostname (`dbenicio.com`/`www`). Se apanhar qualquer host, também vai redirecionar
  `n8n.dbenicio.com/`. O teste do passo 4 mostra isso: o `location` deve ser o Access, não o LinkedIn.
- **Trocar o token do túnel:**
  `kubectl -n mycoe create secret generic cloudflared-token --from-literal=token="$NOVO" --dry-run=client -o yaml | kubectl apply -f - && kubectl -n mycoe rollout restart deploy/cloudflared`
- **Imagem do n8n.** A base usa `n8nio/n8n:latest` com `IfNotPresent`, por isso a versão só muda
  quando a imagem local é removida. Para atualizações controladas, fixe uma versão na base.
- **Site VTES.** Quando existir o workflow do simulador, o `SIMULATION_ENDPOINT` do `app.js` será
  `https://n8n.dbenicio.com/webhook/<path>`. Configure *Allowed Origins (CORS)* no nó Webhook
  com `https://dbenicio.com`.
- **Internet de casa.** O túnel só precisa de saída na porta 7844 (QUIC/UDP, com HTTP/2 como
  alternativa automática). Se a internet cair, o site continua no ar, mas o n8n fica fora até a
  ligação voltar.
