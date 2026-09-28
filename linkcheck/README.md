# 🔗 LinkChecker

> Verifica se as URLs de um texto realmente existem — e barra o texto se não.
> *Verifies whether URLs in a text actually exist — and blocks the text if they don't.*

[![ADK](https://img.shields.io/badge/ADK-2.9.2-blue)](https://google.github.io/adk-docs/)
[![Tool calling](https://img.shields.io/badge/tool_calling-obrigatório-orange)]()
[![Custo](https://img.shields.io/badge/custo-1–2_chamadas%20%2B%20HTTP-green)]()

---

## 🇧🇷 Português

### 🎯 O que ele resolve

O `BlogWriter` pede *"3 sources"* no outline, e o modelo cumpre: escreve três
URLs em Markdown. Só que o modelo **não tem como saber se elas existem** — ele
não navega. O resultado são links plausíveis e falsos, como
`https://docs.python.org/3/asyncio-task.html`: o padrão bate com a documentação
real, o domínio é verdadeiro, mas a página não existe.

Como o `BlogWriter` não tem tool nenhuma, nada no pipeline pegava isso. Este
agente fecha o buraco.

### 🧠 Por que duas ferramentas, e não uma

| Ferramenta | Pergunta que responde | O que pega | O que **não** pega |
|---|---|---|---|
| `checar_url` | *A URL responde?* | link morto, typo de domínio, 404 | URL inventada cujo padrão está certo |
| `buscar_fontes` | *Esse assunto existe na web?* | URL inventada, fonte duplicada | página real mas fora do assunto |

Só um dos dois deixa passar um defeito diferente. Por isso os dois precisam
concordar — "respondeu" e "confirmado na busca" são critérios independentes no
veredito.

### 🔁 O loop

```mermaid
flowchart TD
    A["📥 Texto com URLs<br/>(do BlogWriter)"] --> B["🛠️ LinkRewriter<br/>AgentTool: extrai e corrige"]
    B --> C{"🔎 busca<br/>disponível?"}
    C -->|✅ com chave| D["🌐 checar_url<br/>HTTP HEAD + GET"]
    C -->|🌐 buscar_fontes| E["🔍 busca Google<br/>MCP stdio"]
    C -->|❌ sem chave| F["⚠️ só HTTP"]
    D --> G["🧮 LinkValidation<br/>JSON no state"]
    E --> G
    F --> G
    G --> H{"⚖️ LinkValidator<br/>todas confirmadas?"}
    H -->|✅ sim| I["🟢 ok → publica"]
    H -->|❌ retry| J["🔁 Loop: reescreve<br/>URLs rejeitadas"]
    J --> B
    H -.->|"⏱️ 3 voltas"| K["🟡 escalate"]
    K --> L["⚠️ sai sem publicar<br/>por segurança"]

    style I fill:#d4edda,stroke:#28a745
    style K fill:#fff3cd,stroke:#ffc107
    style L fill:#f8d7da,stroke:#dc3545
    style F fill:#e2e3e5,stroke:#6c757d
```

### ✅ Critérios de aprovação

O texto só passa se **todas** as três condições forem verdadeiras:

1. Toda URL foi checada com `checar_url` e está viva
2. Toda URL foi confirmada com `buscar_fontes`
3. Nenhuma URL está no `LinkValidation` de uma iteração anterior

Sem `BRAVE_API_KEY`, o critério 2 não pode ser satisfeito — e o agente
**diz isso no veredito em vez de aprovar por omissão**. Aprovar sem provar seria
exatamente o defeito que ele existe para evitar.

### 🔌 Como usar

```bash
# com busca (recomendado)
echo 'BRAVE_API_KEY=...' >> .env    # https://api.search.brave.com/app/keys
adk run linkcheck

# só HTTP — o agente avisa que não confirma a existência do assunto
adk run linkcheck
```

> `TYPESAFE_API_KEY` **não** tem nada a ver com a busca: é o Jev, outro
> fornecedor (`api.typesafe.ai`). Uma chave de busca ali seria mandada para a
> TypeSafe num header `Authorization`.

#### Por que Brave, e não a busca do Google

Vale registrar, porque a escolha parece arbitrária e não é. A busca do Google
era a primeira opção e **está trancada**:

- A [Custom Search JSON API](https://developers.google.com/custom-search/v1/overview)
  está **fechada para novos clientes** desde janeiro de 2026. Quem já tinha
  acesso migra até 1º de janeiro de 2027; quem não tinha, não consegue chave.
- Motores novos do Programmable Search Engine são obrigados a usar “Sites to
  search”, no máximo 50 domínios. “Search the entire web” saiu, e o formulário
  de criação ([`/create/new`](https://programmablesearchengine.google.com/create/new))
  responde **404**.
- A chave do AI Studio não substitui: a API devolve
  `401 UNAUTHENTICATED — API keys are not supported by this API`.

O Google oferece Vertex/Agent AI Search como alternativa, mas isso busca o
*seu* corpus, com mínimo de ~1.000 QPM e 50 GiB — não é "me devolve a web em
JSON". A Brave usa o índice próprio dela, é self-serve, e tem servidor MCP.

**Custo:** $5 de crédito por mês, renováveis, mais $5 por 1.000 requisições —
dá para ~1.000 buscas/mês de graça. O crédito exige cartão no cadastro (a Brave
diz que é anti-fraude e não cobra se você não estourar); dá para pôr limite de
gasto no painel. A busca do Google dava 100/dia, ou ~3.000/mês, mas só para
quem já era cliente.

```python
from linkcheck.agent import root_agent
```

O agente lê o texto da mensagem. Se você chamar direto a tool:

```python
from linkcheck.tools import checar_url

checar_url("https://docs.python.org/3/library/asyncio.html")
# {"ok": True, "status": 200, ...}
```

### 📋 Requisitos

| Item | Versão | Obrigatório |
|---|---|---|
| Python | ≥ 3.10 | ✅ |
| `google-adk` | 2.9.2 | ✅ |
| `requests` ou `urllib` | stdlib | ✅ |
| `GOOGLE_API_KEY` | — | ✅ (modelo) |
| `BRAVE_API_KEY` | — | 🔶 recomendado |

### 💰 Custo

1 a 2 chamadas de modelo por execução, mais requisições HTTP diretas (grátis).
Sem chave de busca, 1 chamada a menos.

### 🧪 Testes

```bash
python3 tests/test_novos_agentes.py       # extração de URL, sem API
python3 tests/test_linkcheck_tool_call.py # bate na API: 2 requests
```

> ⚠️ O segundo teste existe por um motivo concreto. **Um modelo pode inventar o
> resultado de uma tool.** Medido com `gemini-3.1-flash-lite`: ao pedir para
> checar dois links, o modelo respondeu *"ambos responderam 200"* **sem chamar a
> tool uma vez** — e um dos URLs dava 404 de verdade. Nenhum teste offline pegaria
> isso: a tool existe, o schema existe, o agente monta. O defeito é o modelo
> pulando a chamada. Por isso ele fica fora do `run_all.py` e só roda quando você
> pedir.

---

## 🇬🇧 English

### 🎯 What it solves

The `BlogWriter` asks for *"3 sources"* in the outline, and the model complies:
it writes three URLs in Markdown. But the model **has no way to know they
exist** — it doesn't browse. The result is plausible, fake links like
`https://docs.python.org/3/asyncio-task.html`: the pattern matches real docs, the
domain is genuine, but the page does not exist.

Since `BlogWriter` has no tools at all, nothing in the pipeline caught it. This
agent closes the gap.

### 🧠 Why two tools, not one

| Tool | Question it answers | What it catches | What it **misses** |
|---|---|---|---|
| `checar_url` | *Does the URL respond?* | dead links, domain typos, 404 | invented URLs with a correct pattern |
| `buscar_fontes` | *Does this topic exist on the web?* | invented URLs, duplicate sources | real pages that miss the topic |

Each one alone lets a different defect through. That's why both must agree —
"responds" and "confirmed by search" are independent criteria in the verdict.

### 🔁 The loop

See the Mermaid diagram above — the flow is identical: `LinkRewriter` extracts
and fixes URLs, `checar_url` (HTTP HEAD + GET) and `buscar_fontes` (MCP stdio)
both feed a `LinkValidation` JSON in state, and `LinkValidator` escalates only
when *all three* approval conditions hold.

### ✅ Approval criteria

The text passes only if **all three** are true:

1. Every URL was checked with `checar_url` and is alive
2. Every URL was confirmed with `buscar_fontes`
3. No URL appears in `LinkValidation` from a previous iteration

Without `BRAVE_API_KEY`, criterion 2 can't be satisfied — and the agent **says
so in the verdict instead of approving by omission**. Approving without proof is
exactly the defect this agent exists to prevent.

The search runs over MCP stdio, spawned from `npx brave-search-mcp`. That server
exposes six tools; only `brave_web_search` is exposed to the agent. The rest
are filtered out on purpose — `brave_image_search` and `brave_video_search` do
not tell you whether a link exists, `brave_local_search` is about places, and
`brave_llm_context_search` returns pre-extracted text, which would be a second
source of truth arguing with the validator's verdict about what the page said.

#### Why Brave, and not Google Search

The choice is not arbitrary. Google's search API was the first option and is
**locked down**:

- The [Custom Search JSON API](https://developers.google.com/custom-search/v1/overview)
  is **closed to new customers** since January 2026. Existing customers
  transition by 1 January 2027; if you did not have access, you cannot get a key.
- New Programmable Search Engines must use "Sites to search", capped at 50
  domains. "Search the entire web" is gone, and the creation form
  ([`/create/new`](https://programmablesearchengine.google.com/create/new))
  returns **404**.
- The AI Studio key does not substitute: the API answers
  `401 UNAUTHENTICATED — API keys are not supported by this API`.

Google offers Vertex/Agent AI Search instead, but that searches *your* corpus
with a floor of ~1,000 QPM and 50 GiB — it is not "give me the web as JSON".
Brave runs its own index, is self-serve, and ships an MCP server.

**Cost:** $5 of monthly credit, renewable, plus $5 per 1,000 requests — around
1,000 free searches a month. The credit requires a card at signup (Brave calls
it anti-fraud and does not charge unless you exceed it); a spending cap in the
dashboard keeps it at that. Google's search API allowed 100/day, roughly
3,000/month, but only for existing customers.

### 🔌 How to use

```bash
echo 'BRAVE_API_KEY=...' >> .env   # https://api.search.brave.com/app/keys
adk run linkcheck
```

```python
from linkcheck.agent import root_agent
```

### 📋 Requirements

| Item | Version | Required |
|---|---|---|
| Python | ≥ 3.10 | ✅ |
| `google-adk` | 2.9.2 | ✅ |
| `GOOGLE_API_KEY` | — | ✅ (model) |
| `BRAVE_API_KEY` | — | 🔶 recommended |

### 💰 Cost

1 to 2 model calls per run, plus direct HTTP requests (free).

### 🧪 Tests

```bash
python3 tests/test_novos_agentes.py       # URL extraction, no API
python3 tests/test_linkcheck_tool_call.py # hits the API: 2 requests
```

> ⚠️ The second test exists for a concrete reason. **A model can invent a tool's
> result.** Measured with `gemini-3.1-flash-lite`: asked to check two links, it
> answered *"both returned 200"* **without calling the tool once** — and one of
> the URLs genuinely 404s. No offline test would catch this. It stays out of
> `run_all.py` for that reason.

---

## 🔗 See also

| Agent | What it covers |
|---|---|
| [`blogger/`](../blogger/) | the pipeline that produces the text this agent checks |
| [`seo/`](../seo/) | metadata limits, the other half of Blogger's requirements |
| [`codereview/`](../codereview/) | reviews code the same way — evidence before approval |
