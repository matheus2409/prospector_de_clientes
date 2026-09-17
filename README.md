# Prospector Maps

Bot de prospecção que varre o Google Maps por nicho + localização (ex: "barbearia"
em "São José do Rio Preto, SP") e extrai nome, categoria, endereço, telefone, site,
avaliação e nº de avaliações de cada estabelecimento. Roda 100% local, com log em
tempo real, e exporta pra CSV, Excel, Google Sheets ou um webhook do n8n.

Leads sem site aparecem marcados (ponto âmbar + borda na tabela) — geralmente são
os alvos mais quentes pra qualquer prospecção de produto digital.

## Funcionalidades

- **Varredura com log ao vivo, retry, circuit breaker e retomada** — descrito
  em detalhe mais abaixo.
- **CRM leve por lead**: marque cada lead como *novo → contatado → interessado
  → fechado* (ou *sem interesse*) direto na tabela. Dá pra filtrar a lista por
  status e ordenar por ele.
- **Deduplicação entre buscas**: a opção **"📊 Todas as buscas (sem
  duplicatas)"** no menu de varreduras junta tudo que você já raspou,
  agrupando o mesmo estabelecimento visto em buscas diferentes (mesmo com
  URLs ligeiramente diferentes) e mostrando o registro mais completo de cada
  um, com uma contagem de quantas vezes apareceu.
- **Buscas agendadas**: repita a mesma busca automaticamente a cada N dias
  enquanto o `run.bat` estiver aberto — útil pra monitorar novos
  estabelecimentos sem site numa região com o tempo.
- **Duas fontes de dados**: scraping via Playwright (padrão, grátis) ou a
  **Google Places API** oficial (mais confiável, com custo por consulta) —
  troca com uma variável no `.env`, sem mudar nada no uso da ferramenta.
- **WhatsApp, exportação com nome dinâmico, ordenação, exclusão de lead,
  barra de estatísticas** — ver "O que mudou" abaixo.

## O que mudou

### Rodada 2 — CRM, deduplicação, agendamentos e Places API

- **CRM leve**: novo campo de status por lead (`novo`, `contatado`,
  `interessado`, `sem_interesse`, `fechado`), com filtro e ordenação por ele
  na tabela. Quem já tinha um `data/prospector.db` de antes ganha a coluna
  nova automaticamente na primeira subida do servidor (migração embutida).
- **Deduplicação entre buscas**: nova opção no menu "Varreduras anteriores"
  que agrega tudo, agrupando pelo identificador do Google embutido na URL do
  Maps (ou por nome+endereço quando isso não é possível) e escolhendo o
  registro mais completo de cada estabelecimento repetido. Funciona também
  na exportação (CSV/Excel/Sheets).
- **Buscas agendadas**: um agendador roda em segundo plano (verifica a cada
  minuto) e dispara a próxima execução vencida sozinho, respeitando a regra
  de "uma varredura por vez".
- **Google Places API como fonte alternativa**: veja a seção própria mais
  abaixo pra como configurar e quanto custa.
- **Corrigido um bug de fuso horário**: o SQLite não guarda fuso horário, e
  uma data sem fuso vira "hora local" pro JavaScript do navegador — fazendo
  as datas de "Varreduras anteriores" e "próxima execução" aparecerem com a
  hora errada (deslocada pelo fuso do usuário). Agora todo campo de data
  volta do banco com o fuso UTC explícito.

### Rodada 1 — correções e primeira leva de melhorias

**Correções:**
- Corrigido um bug real de condição de corrida na fila de log (SSE): se o
  navegador conectasse no log ao vivo bem no instante em que a varredura
  começava a rodar, o log e a tabela ficavam travados pra sempre — sem erro
  nenhum visível — mesmo com a busca rodando normalmente no fundo.
- CSV exportado agora inclui BOM UTF-8: sem isso, nomes/endereços com acento
  ("São José do Rio Preto") apareciam corrompidos ao abrir o arquivo direto
  no Excel.
- Se o servidor for fechado no meio de uma varredura (Ctrl+C, queda de luz,
  fechar a janela do `run.bat`), o job antes ficava preso com status
  "rodando" pra sempre, travando o botão de iniciar uma nova busca. Agora
  esses jobs órfãos são recuperados (viram "partial", retomáveis) assim que
  o servidor sobe de novo.
- O evento de log ao vivo de cada lead agora carrega o registro completo
  (incluindo o id salvo no banco), corrigindo uma inconsistência de formato
  entre o que aparece ao vivo e o que aparece ao recarregar uma busca salva.

**Novidades:**
- **Botão de WhatsApp** ao lado do telefone de cada lead — abre a conversa
  direto (`wa.me`), sem precisar copiar e colar o número.
- **Colunas ordenáveis** (Nome, Categoria, Avaliação) — clique no cabeçalho.
- **Excluir lead** direto na tabela, pra tirar um resultado errado ou
  duplicado antes de exportar.
- **Barra de estatísticas** acima da tabela: total de leads, quantos/quantos
  % estão sem site, e a nota média.
- **Nome de arquivo dinâmico** na exportação: `barbearia_sao-jose-do-rio-
  preto_2026-08-28.csv` em vez do genérico `leads.csv`.
- **Bloqueio de varreduras simultâneas**: só dá pra rodar uma busca por vez
  agora (a segunda tentativa retorna um aviso claro). Isso é proposital —
  rodar dois Chromiums ao mesmo tempo da mesma máquina/IP só aumenta a
  chance do Google bloquear.
- Se a página for recarregada (ou aberta numa aba nova) enquanto uma
  varredura ainda está rodando, ela reconecta automaticamente no log ao
  vivo em vez de ficar parada sem mostrar nada.
- Links de "visitar site" agora só aparecem clicáveis se o valor extraído
  for mesmo uma URL http(s) — proteção extra contra um dado malformado virar
  link executável.

Todo esse trabalho (backend inteiro, API, fluxo de retry/circuit-breaker/
resume, exportações, CRM, dedup, agendador) foi instalado e testado de ponta
a ponta neste ambiente, incluindo subir o servidor de verdade e bater nas
rotas — não só lido. A única coisa que **continua não verificada contra o
Google Maps ao vivo** são os seletores CSS do `scraper.py` em si, e contra a
Places API real são as chamadas em `places_api.py` (veja as seções
correspondentes abaixo) — esse ambiente não tem acesso à internet pública
pra nenhuma das duas.

## Requisitos

- Windows 10/11
- [Python 3.10+](https://python.org) instalado, com "Add Python to PATH" marcado no instalador
- ~500 MB livres (o Chromium do Playwright ocupa a maior parte disso)

## Instalação

1. Extraia esta pasta em qualquer lugar (ex: `C:\Ferramentas\prospector-maps`)
2. Dê duplo clique em **`setup.bat`**
   - Cria o ambiente virtual Python
   - Instala as dependências (`backend/requirements.txt`)
   - Baixa o Chromium do Playwright (só a primeira vez, pode demorar alguns minutos)
   - Cria o arquivo `.env` a partir do `.env.example`
3. (Opcional) Edite o `.env` — veja "Configuração" abaixo
4. Dê duplo clique em **`run.bat`**
5. Abra **http://127.0.0.1:8000** no navegador

Nas próximas vezes, só precisa rodar `run.bat` (não precisa rodar `setup.bat` de novo).

## Configuração (tudo opcional)

Abra o `.env` num editor de texto:

| Variável | Pra que serve |
|---|---|
| `N8N_WEBHOOK_URL` | Se preenchida, envia os leads automaticamente (POST JSON) pro n8n ao fim de cada varredura |
| `GOOGLE_SERVICE_ACCOUNT_FILE` | Caminho do JSON da service account, pra exportar direto pro Google Sheets (passo a passo abaixo) |
| `GOOGLE_PLACES_API_KEY` | Se preenchida, troca o motor de busca de scraping pra Google Places API oficial (passo a passo e custos abaixo) |
| `API_KEY` | Se preenchida, protege a API com o header `X-API-Key`. Deixe em branco se só você usa a máquina |
| `HEADLESS` | `false` abre a janela do Chromium visível — útil pra ver o que está acontecendo se algo travar |
| `SCRAPE_DELAY_MIN` / `MAX` | Intervalo aleatório entre ações, pra reduzir risco de bloqueio |
| `MAX_RESULTS_HARD_CAP` | Teto máximo de resultados por busca (proteção contra varreduras gigantes acidentais) |
| `MAX_RETRIES_PER_LISTING` | Quantas tentativas extras por estabelecimento antes de desistir dele |
| `CIRCUIT_BREAKER_THRESHOLD` | Quantas falhas seguidas pausam a varredura inteira (status `partial`, retomável) |

### Configurando a Google Places API (opcional — alternativa ao scraping)

Por padrão a ferramenta raspa o Google Maps com Playwright (grátis, mas
depende de seletores CSS que o Google pode mudar — veja a seção
"⚠️ Sobre a raspagem" abaixo). Preenchendo `GOOGLE_PLACES_API_KEY` no `.env`,
a varredura passa a usar a **Places API (New)** oficial do Google em vez
disso: mais confiável (não quebra por causa de uma classe CSS renomeada,
está dentro dos Termos de Serviço), mas com custo por consulta.

**Como conseguir a chave:**
1. Acesse o [Google Cloud Console](https://console.cloud.google.com/) e crie
   (ou reuse) um projeto
2. Ative a **"Places API (New)"** (menu "APIs e Serviços" → "Ativar APIs e
   Serviços" → busque por "Places API (New)")
   — repare no "(New)": a Places API antiga (legada) não pode mais ser
   ativada em projetos novos, então use essa mesmo
3. Habilite o faturamento do projeto (a API exige isso mesmo dentro da cota
   grátis mensal)
4. Crie uma **chave de API** em "Credenciais" → "Criar Credenciais" → "Chave
   de API", e cole em `GOOGLE_PLACES_API_KEY` no `.env`
5. (Recomendado) Restrinja a chave pra só poder chamar a Places API, em
   "Restrições de API" na tela da própria chave

**Quanto custa:** a Google cobra pelo conjunto de campos pedido em cada
chamada, sempre no nível mais caro entre os campos incluídos. Como esta
ferramenta pede avaliação/nº de avaliações (pra você poder priorizar os
melhores leads), cada busca cai no nível **"Pro"**, na faixa de uns
**US$32 a cada 1.000 chamadas**, com **1.000 chamadas grátis por mês**
(a cota grátis é por SKU e reseta todo mês — não é mais um crédito único de
conta como era antes de março de 2025). Cada página de resultados (até 20
estabelecimentos) consome 1 chamada — ou seja, uma busca com `max_results=60`
gasta ~3 chamadas. Os valores exatos podem ter mudado; confira a
[página oficial de preços](https://developers.google.com/maps/billing-and-pricing/pricing)
antes de decidir, e monitore o uso no Cloud Console pra não ter surpresa.

**Diferenças em relação ao scraping** (`backend/places_api.py`):
- Não existe "retomar" (resume) pra buscas feitas pela Places API — o
  botão simplesmente não aparece pra esses jobs. Como não há sessão de
  navegador pra manter viva, rodar a busca de novo do zero é barato o
  bastante pra não precisar dessa complexidade.
- Não existe risco de "bloqueio/captcha" (isso é uma particularidade do
  scraping) — os erros possíveis são de rede, chave inválida/sem permissão,
  ou cota mensal excedida, e aparecem no log da varredura.
- Pra voltar a usar o scraper, basta apagar (ou comentar) a linha
  `GOOGLE_PLACES_API_KEY` no `.env` e reiniciar o servidor. O chip no topo
  da página mostra qual motor está ativo no momento.

### Configurando o Google Sheets (opcional)

1. Acesse o [Google Cloud Console](https://console.cloud.google.com/) e crie (ou reuse) um projeto
2. Ative a **Google Sheets API** (menu "APIs e Serviços" → "Ativar APIs e Serviços")
3. Crie uma **Service Account** ("Credenciais" → "Criar Credenciais" → "Conta de serviço")
4. Nela, crie uma **chave** em formato JSON e baixe o arquivo
5. Coloque o arquivo em algum lugar (ex: `C:\Ferramentas\prospector-maps\service-account.json`)
   e aponte `GOOGLE_SERVICE_ACCOUNT_FILE` pra esse caminho no `.env`
6. Se for exportar pra uma planilha **já existente**, compartilhe essa planilha com o
   e-mail da service account (algo como `xxx@yyy.iam.gserviceaccount.com`, com permissão de Editor).
   Se não passar um ID de planilha na exportação, o app cria uma nova automaticamente.

### Configurando o webhook do n8n (opcional)

No n8n, crie um workflow com um nó **Webhook** (método POST) e cole a URL gerada em
`N8N_WEBHOOK_URL`. O payload enviado tem esse formato:

```json
{
  "job_id": "a1b2c3d4e5f6",
  "query": "barbearia",
  "location": "São José do Rio Preto, SP",
  "total_found": 23,
  "leads": [
    { "name": "...", "category": "...", "phone": "...", "website": null,
      "address": "...", "rating": 4.7, "review_count": 120, "maps_url": "...",
      "status": "novo" }
  ]
}
```

## Como usar

1. Preencha **nicho/categoria** (ex: "barbearia", "salão de beleza") e **localização**
   (ex: "São José do Rio Preto, SP")
2. Ajuste o **máx. resultados** se quiser (padrão: 60)
3. Clique em **Iniciar Varredura** — o log à esquerda mostra o progresso em tempo real,
   e a tabela à direita vai populando conforme os leads são encontrados. Só dá pra ter
   uma varredura rodando por vez.
4. Marque **"Somente sem site"** e/ou escolha um **status** no filtro pra focar num
   subconjunto dos leads. Clique nos cabeçalhos **Nome**, **Categoria**, **Avaliação**
   ou **Status** pra ordenar a tabela.
5. Use o dropdown de **status** em cada linha pra marcar o andamento do contato
   (novo → contatado → interessado → fechado, ou sem interesse). O ícone de
   **WhatsApp** ao lado do telefone abre a conversa direto, e o de **excluir**
   tira um lead errado/duplicado antes de exportar.
6. Use os botões de exportação (CSV, Excel, Sheets, Webhook) quando terminar — o
   arquivo já sai com um nome baseado na busca (ex: `barbearia_sao-jose-do-rio-
   preto_2026-08-28.csv`).
7. O menu **"Varreduras anteriores"** deixa revisitar qualquer busca já feita, ou
   escolher **"📊 Todas as buscas (sem duplicatas)"** pra ver tudo junto, sem repetir
   o mesmo estabelecimento achado em buscas diferentes. Se uma busca foi cancelada,
   pausada ou bloqueada no meio, um botão **"Retomar"** aparece ao carregá-la.
8. Pra repetir a mesma busca sozinha de tempos em tempos, escolha uma frequência
   em **"Repetir"** e clique em **"+ Agendar"**. O painel **"Buscas agendadas"**
   (logo abaixo) lista, pausa/retoma e remove os agendamentos ativos.

## ⚠️ Sobre a raspagem em si — leia antes de usar

Este projeto foi montado num ambiente sem acesso à internet pública, então o
código do scraper (`backend/scraper.py`) **não foi testado contra o Google Maps
ao vivo**. A lógica e a API do Playwright estão corretas, mas:

- **O Google troca os nomes de classe do Maps periodicamente.** Todos os
  seletores CSS ficam centralizados no dicionário `SELECTORS`, no topo do
  `scraper.py`. Se a raspagem parar de achar nome/telefone/site/etc, abra o
  DevTools (F12) numa página de estabelecimento no Maps, ache o elemento
  correspondente, e atualize o seletor ali.
- **Risco de bloqueio/captcha existe.** O scraper já usa delays aleatórios e
  um user-agent realista, mas não há garantia. Se isso acontecer, rode com
  `HEADLESS=false` no `.env` pra ver o que está na tela, e considere aumentar
  `SCRAPE_DELAY_MIN`/`MAX` ou reduzir o `max_results` por busca.
- **Isso está fora dos Termos de Serviço do Google** (que proíbem scraping
  automatizado do Maps). Na prática é uma técnica muito usada pra geração de
  leads, mas a responsabilidade de uso é sua — o risco prático é o Google
  bloquear temporariamente o IP que estiver fazendo as buscas, não algo pior.
- Se isso pesar, a **Google Places API** já está implementada como fonte de
  dados alternativa (sem quebra de seletor, sem risco de bloqueio, dentro dos
  termos, em troca de custo por consulta) — veja "Configurando a Google
  Places API" mais acima pra ativar.

### Camadas de robustez já implementadas

- **Retry com backoff**: cada estabelecimento que falhar na extração é
  tentado de novo (`MAX_RETRIES_PER_LISTING`, padrão 2 tentativas extras),
  com espera crescente entre tentativas.
- **Detecção de bloqueio/captcha**: se a tela de "tráfego incomum" do Google
  aparecer, a varredura para imediatamente (status `blocked`) em vez de
  continuar tentando e piorar a situação.
- **Circuit breaker**: se `CIRCUIT_BREAKER_THRESHOLD` estabelecimentos
  seguidos falharem (padrão 5, já contando as tentativas extras de cada um),
  a varredura pausa sozinha (status `partial`) em vez de queimar o resto da
  lista — geralmente sinal de que algo mudou (seletor quebrado ou bloqueio
  parcial).
- **Retomar de onde parou**: toda varredura persiste a lista completa de
  links coletados (tabela `JobLink`) antes de processá-los. Se ela for
  cancelada, pausada pelo circuit breaker, ou bloqueada, os itens ainda não
  processados continuam salvos como "pendentes". No menu **"Varreduras
  anteriores"**, carregar um job com pendências mostra um botão **"Retomar
  (N pendentes)"** que continua exatamente de onde parou — sem refazer a
  busca nem re-rolar os resultados no Maps.

## Estrutura do projeto

```
prospector-maps/
├── backend/
│   ├── main.py         # rotas FastAPI
│   ├── scraper.py       # motor de raspagem (Playwright) + retry/circuit breaker/resume
│   ├── places_api.py     # motor alternativo via Google Places API (New)
│   ├── jobs.py           # orquestra jobs em background + fila SSE + agendador
│   ├── models.py         # modelos SQLModel (Job, Lead, Schedule, JobLink)
│   ├── database.py       # engine SQLite + tipo UTCDateTime + migração leve
│   ├── exporters.py      # CSV / Excel / Google Sheets + deduplicação
│   ├── webhook.py        # envio pro n8n
│   ├── config.py         # leitura do .env
│   └── requirements.txt
├── frontend/
│   ├── index.html
│   ├── style.css
│   └── app.js
├── data/                 # banco SQLite fica aqui (criado automaticamente)
├── .env.example
├── setup.bat
├── run.bat
└── test_smoke.py         # testes automatizados (banco, exportação, API, retry/circuit
                           # breaker/resume, CRM, dedup, agendador, places_api) com dados
                           # simulados
```

## Troubleshooting

**"Python não encontrado"** — reinstale o Python marcando "Add Python to PATH".

**A varredura fica presa em "Coletando resultados..." sem avançar** — provavelmente
o seletor `feed` ou `result_link` em `scraper.py` mudou. Rode com `HEADLESS=false`
pra ver a página ao vivo e comparar com os seletores no código.

**Telefone/site vêm sempre vazios** — os seletores `phone_button` / `website_link`
provavelmente mudaram. Inspecione um card de estabelecimento no Maps (F12) e ajuste.

**Erro ao exportar pro Sheets** — confira se `GOOGLE_SERVICE_ACCOUNT_FILE` aponta pro
caminho certo e se a planilha (quando reaproveitada) foi compartilhada com o e-mail
da service account.

**Places API retornando erro 403** — confira se a chave em `GOOGLE_PLACES_API_KEY`
está certa, se a **"Places API (New)"** (com o "(New)" mesmo) está ativada no projeto
do Google Cloud, e se o faturamento do projeto está habilitado.

**Quero rodar isso num servidor/VPS, não só localmente** — dá, mas o Chromium do
Playwright precisa de mais RAM que uma raspagem comum (recomendo 1 GB+ livre), e
convém manter `HEADLESS=true`. Se quiser esse setup, me chama que ajudo a adaptar.

## Rodar os testes automatizados

```
venv\Scripts\python.exe -m pip install -r backend\requirements.txt
venv\Scripts\python.exe test_smoke.py
```

Esses testes cobrem banco de dados, exportação CSV/Excel, filtro "sem site",
fluxo completo da API, proteção por API key, retry com backoff, detecção de
bloqueio, circuit breaker, o fluxo de retomar uma varredura pausada, a
migração leve de schema, o CRM leve (status), a deduplicação entre buscas, o
CRUD e disparo do agendador, e o motor da Places API (paginação, mapeamento
de campos, tratamento de erros) — tudo com dados/transporte HTTP simulados,
então não dependem de internet nem do Google.
