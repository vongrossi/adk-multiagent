# 🤝 Como contribuir

Obrigado por querer mexer nisso. Este repositório tem uma regra que organiza
todo o resto:

> **O que um LLM faz mal, não vai no prompt. Vai em código.**

Se você contributing um "melhoria" que só adiciona texto a um `instruction`,
vale perguntar antes se um teste em Python não seria mais barato de manter —
e mais confiável.

---

## 🚀 Rodar os testes

```bash
pip install -r requirements.txt
python3 tests/run_all.py
```

Sobe 9 suítes em ~17s, **offline**. Nenhuma chave, nenhum request, nenhum
modelo. Todos sobem um `llama-server` falso em `127.0.0.1` e o servidor MCP
local, então roda em qualquer máquina e não gasta cota.

```bash
python3 tests/run_all.py triage rag    # so as suites cujo nome casa
```

Os testes que batem na API real ficam **fora** do `run_all.py`, de propósito:

```bash
python3 tests/test_linkcheck_tool_call.py   # ~2 requests
python3 tests/benchmarks/comparar_lite.py    # 3 requests por modelo
```

Eles existem por um motivo concreto: *um modelo pode inventar o resultado de uma
tool*. Nenhuma suíte offline pega isso, porque a tool existe, o schema existe e
o agente monta — o defeito é o modelo pular a chamada.

---

## 📐 O que um PR precisa

| Item | Motivo |
|---|---|
| `python3 tests/run_all.py` passa | 17s, não custa nada |
| `pyflakes .` limpo | sem import morto |
| Bug novo → teste que **falha antes** | o teste é a prova, não a descrição |
| Mudança de agente → docstring/README em PT-BR e EN | `name`, `description` e `instruction` **sempre em inglês** |
| Mudança de tool → `Args:` no formato que o ADK entende | é isso que vira schema |

O ponto do segundo item merece destaque porque é o erro mais comum: **a
docstring de uma tool é a descrição que o modelo lê.** Uma docstring em
português ou vaga faz o agente simplesmente não chamar a tool, e o sintoma
parece "o modelo é burro".

---

## 🔑 Segredos

Nunca comite chave, log ou sessão:

- `.env` e variantes → já no `.gitignore`
- `*.log`, `blog_agent.txt`, logs de `adk run` → já no `.gitignore`
- `.adk/` (session.db) → guarda o histórico de conversa e o `state` serializado

Se vazou uma chave, **rotacionar** não é opcional. Ver
[BACKLOG.md](BACKLOG.md) item 1.

---

## 🌐 Idioma

| Onde | Idioma |
|---|---|
| README, BACKLOG, docstring de módulo | 🇧🇷 Português |
| Seção equivalente no README | 🇬🇧 Inglês (mesma página) |
| `name`, `description`, `instruction` de agente | 🇬🇧 **Inglês, só** |
| Docstring de tool, `Args:`, `Returns:` | 🇬🇧 **Inglês, só** |
| Comentário de código | 🇧🇷 Português |
| Nomes de variável/função | 🇬🇧 Inglês |

A divisão: o **código** é lido por humanos (português); o que o **modelo**
consome é inglês, porque é o idioma em que os pesos foram treinados.

---

## 🧩 Onde mexer

| Quero... | Vá para |
|---|---|
| Um agente novo | pasta com `agent.py` + `README.md` bilíngue + registro no `README.md` principal |
| Uma tool nova | `tools.py` do agente, com docstring em inglês e `Args:` |
| Um toolset MCP | `mcp_server/server.py` + um agente consumidor |
| Uma regra de negócio | função pura em Python + teste sem API |

Regra de ouro para regra de negócio: **função pura, testável offline.**
`jev.decidir()` é o exemplo — a política de roteamento inteira cabe num `for`
com um `dict` dentro, e o teste roda sem chave.

---

## 🧪 Por que não `pytest`

Cada teste é um script que roda sozinho. `requirements.txt` não ganha uma
dependência a mais, e `python3 tests/<arquivo>.py` funciona em qualquer máquina
sem install extra. Se algum dia essa decisão pesar, o `run_all.py` isola cada
suíte em subprocesso — que é o que um `pytest-xdist` faria de qualquer forma.

---

## 🐛 Achou um bug

Abra uma issue com:

1. O que você esperava
2. O que aconteceu
3. O comando exato
4. Se é determinístico ou intermitente

Se for um modelo se comportando mal, inclua **o modelo e a temperatura**. A
mesma instrução se comporta diferente em `gemini-3.1-flash-lite` e
`gemini-3.5-flash-lite` — medido neste repositório, ambos dão `retry` falso em
outlines válidos, mas por motivos diferentes.
