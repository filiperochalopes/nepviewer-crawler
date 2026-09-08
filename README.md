# Solar — coletor NepViewer sem navegador

Aplicação única que coleta a potência atual diretamente da API do NepViewer e
serve o painel FastAPI. Não usa Chromium, Playwright nem APScheduler.

## Como funciona

- `web_app.py` inicia uma tarefa assíncrona no ciclo de vida do FastAPI.
- `nepviewer_client.py` autentica em `v2/sign-in` e consulta
  `v2/overview/overview`.
- O token permanece apenas em memória e um erro 401 provoca nova autenticação.
- As leituras são persistidas em `data/nepviewer.db`.
- A mesma instância Uvicorn serve o painel e executa a coleta.

## Execução local

Copie `.env.example` para `.env`, preencha `NEP_EMAIL` e `NEP_PASSWORD` e rode:

```sh
docker compose up --build
```

O compose de produção não publica portas. Para um teste local, acrescente
temporariamente `ports: ["8000:8000"]` ao serviço ou use `docker run -p`.

## Implantação no NAS

1. Armazene o projeto em `/volume1/docker/solar-filipelopes-me`.
2. Mantenha `.env` com permissão restrita e fora do Git.
3. Com o coletor antigo parado, copie a base consistente para
   `data/nepviewer.db`.
4. Importe `compose.yml` como projeto na interface Docker do NAS.
5. No Nginx Proxy Manager, encaminhe `solar.filipelopes.me` para
   `solar:8000`; ambos precisam estar na rede externa `proxy`.

O contêiner tem limite de 128 MiB de RAM, 64 PIDs e rotação de logs. Nos testes
locais, o processo completo consumiu aproximadamente 35 MiB e 3 PIDs.

## Verificação

```sh
docker compose config --quiet
docker compose ps
docker compose logs --tail=100 solar
docker compose exec solar python -c "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:8000/health').read().decode())"
```

O endpoint `/health` informa o estado da coleta, o horário da última leitura
bem-sucedida e o último erro, sem expor as credenciais.
