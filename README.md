# NepViewer Scraper (Dashboard -> SQLite -> CSV)

Coletor de “Potência(W)” do dashboard do NepViewer a cada 1 minuto, salvando em SQLite com timestamp no fuso America/Bahia.
Implementa login automático (quando necessário), sessão persistida (cookies) e reinício periódico do browser para evitar vazamento de memória.

## Por que Playwright (e não BeautifulSoup)?
O dashboard é renderizado via JavaScript (Vue/Ant Design), então o HTML final com os valores normalmente só existe após o JS rodar.
BeautifulSoup não executa JavaScript. Para evitar engenharia reversa de API/JSON, usamos Playwright headless.

## O que é coletado
* Campo: Potência(W)
* Fonte: dashboard https://user.nepviewer.com/dashboard
* Persistência: SQLite (nepviewer.db)
* Tabela: nep_power
  - id (INTEGER)
  - ts_local (TEXT ISO8601 no fuso America/Bahia)
  - power_w (REAL)

## Seletor do valor (XPath robusto)
Usado para pegar o .value do mesmo bloco que contém o label "Potência(W)":

//div[contains(@class,'item')][.//div[contains(@class,'label') and normalize-space(.)='Potência(W)']]//div[contains(@class,'value')]

## Seletores de login
A tela de login possui ids fixos:
* Email: #form_item_account
* Senha: #form_item_password
* Submit: button[type="submit"]

## Sessão persistida
O script salva cookies/estado em:
* state.json

Assim evita logar a cada execução. Se a sessão expirar, ele re-logará automaticamente.

## Consumo de memória: browser aberto
Headless Chromium (1 contexto, 1 aba) tipicamente:
* ~120–250 MB de RAM “base”
* pode estabilizar ~200–400 MB
* em páginas com gráficos/canvas pode subir (às vezes ~300–600 MB)

Para manter estável, o script reinicia o browser periodicamente (padrão: a cada 6h).

## Rodar local (sem Docker)

1) Instalar dependências
pip install -r requirements.txt
playwright install chromium

2) Configurar credenciais
export NEP_EMAIL="seu-email"
export NEP_PASSWORD="sua-senha"

3) Rodar
python nepviewer_daemon.py

## Rodar com Docker + docker-compose

1) Copie o .env.example para .env
cp .env.example .env
Edite NEP_EMAIL e NEP_PASSWORD.

2) Suba
docker compose up -d --build

3) Ver logs
docker compose logs -f

## Exportar SQLite para CSV

### Via sqlite3 (CLI)
sqlite3 nepviewer.db -header -csv "SELECT * FROM nep_power ORDER BY ts_local;" > nep_power.csv

### Filtrar por período (ex.: últimas 24h)
sqlite3 nepviewer.db -header -csv "SELECT * FROM nep_power WHERE ts_local >= datetime('now','-1 day') ORDER BY ts_local;" > nep_power_24h.csv

### Via Python (pandas)
python -c "import sqlite3,pandas as pd; c=sqlite3.connect('nepviewer.db'); df=pd.read_sql_query('SELECT * FROM nep_power ORDER BY ts_local', c); df.to_csv('nep_power.csv', index=False); c.close()"

## Observações
* Se o site adicionar anti-bot (CAPTCHA/Cloudflare), pode ser necessário ajustar estratégia (ex.: login manual 1x para gerar state.json).
* Se o label mudar (idioma), ajuste o XPath do POWER_XPATH.
* O timestamp é salvo já em America/Bahia (ISO8601).
