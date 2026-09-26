# 🔌 Exemplo de MCP com Google ADK

> Servidor MCP local por `stdio`, consumido por um agente ADK. Exemplo mínimo, com as três armadilhas que quebram isso.
> *A local stdio MCP server consumed by an ADK agent. Minimal example, including the three traps that break it.*

[![MCP](https://img.shields.io/badge/MCP-2.2.0-ff6b35)](https://modelcontextprotocol.io/)
[![ADK](https://img.shields.io/badge/ADK-2.9.2-blue)](https://google.github.io/adk-docs/)
[![Custo](https://img.shields.io/badge/custo-0_requests_para_as_tools-green)]()

> Inspirado em [`build-mcp-agent-google-adk`](https://github.com/smithakolan/awesome-ai-agents/tree/main/build-mcp-agent-google-adk)
> do awesome-ai-agents, que conecta um agente a um servidor MCP de Google Trends.

---

## 🇧🇷 Português

### 🎯 O que este exemplo é

Um servidor MCP em **um arquivo**, três tools, transporte `stdio`. Ele existe
para mostrar o padrão completo — não para ser útil.

O padrão tem cinco peças:

| # | Peça | Onde |
|---|---|---|
| 1 | Processo Python que fala JSON-RPC em stdin/stdout | `mcp_server/server.py` |
| 2 | Decorators `@mcp.tool()` que registram funções | `mcp_server/server.py` |
| 3 | Docstrings **em inglês** — é o que o modelo lê | `mcp_server/server.py` |
| 4 | `McpToolset` + `StdioConnectionParams` | `mcp_text_audit/agent.py` |
| 5 | O ADK sobe e derruba o processo sob demanda | automático |

### 🔁 O fluxo

```mermaid
sequenceDiagram
    autonumber
    participant U as 👤 Usuário
    participant A as 🎫 TextAuditor<br/>(ADK + Gemini)
    participant T as 🔌 McpToolset
    participant S as ⚙️ mcp_server/server.py<br/>(processo separado)

    U->>A: "conte este texto"
    A->>T: pedir tools
    Note over T,S: ADK faz spawn do processo<br/>na primeira chamada
    T->>S: stdio (JSON-RPC)
    S-->>T: lista de tools + schema
    T-->>A: schema das tools
    A->>T: contar_texto(texto=...)
    T->>S: tools/call
    S-->>T: {"caracteres": 61, "palavras": 11}
    T-->>A: resultado
    A-->>U: "61 caracteres" ✅

    Note over U,S: na próxima chamada o processo<br/>já está vivo; o ADK só encerra<br/>no fim da sessão
```

O `doctor()` do agente responde à pergunta que nenhum log responde:
**o toolset conectou?**

```python
from mcp_text_audit.agent import doctor
await doctor()
# {'conectou': True, 'tools': [...], 'faltando': []}
```

### 🛠️ As três tools

| Tool | O que faz | Por que não delegar ao LLM |
|---|---|---|
| `contar_texto` | caractere, palavra, linha, URL — exatos | ele conta errado; "cerca de 95" quebra um limite de API |
| `achar_placeholders` | acha `{LIMITE_TITULO}` não preenchido | o modelo lê o placeholder e não reclama — **ninguém pega** |
| `achar_segredos` | chave de API, token, senha hardcoded | regex conta; LLM alucina padrão |

`achar_placeholders` não é hipótese. Ela existe porque **este repositório tinha
exatamente esse bug** — um prompt de agente com `{LIMITE_TITULO}` literal, que
chegava ao modelo como texto em vez de número. Nenhum teste de "o agente
responde certo" pegaria.

### ⚠️ As três armadilhas

**1. `print()` no stdout quebra o protocolo.**
`stdout` é o canal do JSON-RPC. Um byte extra ali corrompe tudo, e o cliente vê
conexão morta sem mensagem útil. Log vai para **stderr**. E o spawn usa `-u`,
porque sem buffer desligado o log fica preso e o sintoma é o mesmo.

```python
print(msg, file=sys.stderr, flush=True)   # ✅
mcp.run(transport="stdio")
```

**2. `connection_params` como dict não falha — ele adia.**
O construtor aceita qualquer coisa. O erro só aparece ao abrir a conexão:

```
ConnectionError: Connection should be StdioServerParameters or SseServerParams
```

Isso torna um `try/except` em volta da *construção* inútil: protege a linha
errada. `linkcheck/tools.py` faz exatamente isso, e por isso a busca do Google
nunca funcionou ali — o agente sobe, roda, e simplesmente não tem a tool.

**3. `tool_filter` é um pedido, não uma garantia.**
Se o nome no filtro estiver errado, a tool não aparece e o agente fica sem ela
em silêncio. `doctor()` devolve `faltando` justamente para isso.

### 🔌 Como usar

```bash
adk run mcp_text_audit
adk web .          # UI, se preferir
```

```python
from mcp_text_audit.agent import root_agent
```

Testar o servidor sozinho, sem gastar request de modelo:

```bash
python3 tests/test_mcp.py
```

### 📋 Requisitos

| Item | Versão | Obrigatório |
|---|---|---|
| Python | ≥ 3.10 | ✅ |
| `google-adk` | 2.9.2 | ✅ |
| `mcp` | 2.2.0 | ✅ |

> ⚠️ No `mcp` 2.x, `FastMCP` foi renomeado para `MCPServer`, e no ADK 2.9
> `MCPToolset` foi renomeado para `McpToolset`. Os nomes antigos ainda funcionam
> com `DeprecationWarning`.

### 💰 Custo

**Zero** requests de modelo para as tools. Elas são código. O LLM só é chamado
para decidir *quando* chamar — e é aí que o custo aparece.

### 🧪 Testes

```bash
python3 tests/test_mcp.py
```

Sobe o servidor de verdade e fala JSON-RPC com ele — não importa as funções.
Importar passaria mesmo com o servidor quebrado, e o servidor quebra de três
jeitos que só aparecem com um cliente do outro lado do pipe: registro da tool,
transporte stdio, e spawn.

---

## 🇬🇧 English

### 🎯 What this example is

An MCP server in **one file**, three tools, `stdio` transport. It exists to show
the complete pattern — not to be useful.

The pattern has five pieces (server process, `@mcp.tool()` decorators, English
docstrings, `McpToolset` + `StdioConnectionParams`, and the ADK spawning the
process on demand) — see the table above.

### 🔁 The flow

See the Mermaid sequence diagram above. The ADK spawns the server process on
the first tool call and keeps it alive for the session. `doctor()` answers the
question no log answers: **did the toolset connect?**

### 🛠️ The three tools

| Tool | What it does | Why not delegate to the LLM |
|---|---|---|
| `contar_texto` | exact char, word, line, URL counts | it miscounts; "roughly 95" breaks an API limit |
| `achar_placeholders` | finds unfilled `{LIMITE_TITULO}` | the model reads it and never complains — **nothing catches it** |
| `achar_segredos` | hardcoded API keys, tokens, passwords | regex counts; an LLM hallucinates patterns |

`achar_placeholders` is not hypothetical: it exists because **this repository
had exactly that bug**.

### ⚠️ The three traps

**1. `print()` to stdout breaks the protocol.** `stdout` is the JSON-RPC
channel. Log to **stderr**, and spawn with `-u` so the buffer doesn't hold it.

**2. `connection_params` as a dict doesn't fail — it defers.** The constructor
accepts anything; the error surfaces only when the connection opens. That makes
a `try/except` around *construction* useless: it guards the wrong line.

**3. `tool_filter` is a request, not a guarantee.** A wrong name means the tool
simply isn't there, and the agent is silently missing it. `doctor()` returns
`faltando` for exactly this.

### 🔌 How to use

```bash
adk run mcp_text_audit
adk web .
```

### 📋 Requirements

| Item | Version | Required |
|---|---|---|
| Python | ≥ 3.10 | ✅ |
| `google-adk` | 2.9.2 | ✅ |
| `mcp` | 2.2.0 | ✅ |

> ⚠️ In `mcp` 2.x, `FastMCP` was renamed to `MCPServer`; in ADK 2.9,
> `MCPToolset` was renamed to `McpToolset`. Old names still work with a
> `DeprecationWarning`.

### 💰 Cost

**Zero** model requests for the tools themselves — they're code. The LLM is only
called to decide *when* to call them, and that's where the cost appears.

### 🧪 Tests

```bash
python3 tests/test_mcp.py
```

Spawns the real server and speaks JSON-RPC with it.

---

## 🔗 See also

| Component | What it covers |
|---|---|
| [`linkcheck/`](../linkcheck/) | the other MCP integration in this repo — and the one with the dict bug |
| [`codereview/`](../codereview/) | the same secret-scanning idea, as a local ADK tool instead of MCP |
| [`triage/`](../triage/) | the one decision where calibrated confidence matters |
