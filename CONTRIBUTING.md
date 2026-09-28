# 🤝 Como contribuir

Obrigado por querer mexer nisso. Este repositório tem uma regra que organiza
todo o resto:

> **O que um LLM faz mal, não vai no prompt. Vai em código.**

Se você contributing um "melhoria" que só adiciona texto a um `instruction`,
vale perguntar antes se um teste em Python não seria mais barato de manter —
e mais confiável.

---

## 🚀 Rodar os testes

Rode com o interpretador do virtualenv, não com o `python3` do PATH — é ele que
o `run_all.py` propaga para os subprocessos (`sys.executable`, `run_all.py:69`),
então misturar os dois dá suíte passando e falhando no mesmo run:

```bash
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python tests/run_all.py
```

Sobe 15 suítes em ~3min, **offline**. Nenhuma chave, nenhum request, nenhum
modelo. Todos sobem um `llama-server` falso em `127.0.0.1` e o servidor MCP
local, então roda em qualquer máquina e não gasta cota.

```bash
.venv/bin/python tests/run_all.py triage rag    # so as suites cujo nome casa
```

Os testes que batem na API real ficam **fora** do `run_all.py`, de propósito:

```bash
.venv/bin/python tests/test_linkcheck_tool_call.py   # ~2 requests
.venv/bin/python tests/benchmarks/comparar_lite.py    # 3 requests por modelo
```

Eles existem por um motivo concreto: *um modelo pode inventar o resultado de uma
tool*. Nenhuma suíte offline pega isso, porque a tool existe, o schema existe e o
agente monta — o defeito é o modelo pular a chamada.

---

## 🪤 "cannot import name 'StdioConnectionParams'"

A mensagem mente sobre a causa. Se ela aparece, **falta o pacote `mcp`** — o ADK
não quebrou e a classe existe.

```python
ImportError: cannot import name 'StdioConnectionParams'
              from 'google.adk.tools.mcp_tool'
```

O que acontece: `google/adk/tools/mcp_tool/__init__.py` envolve todos os imports
num `try/except ImportError` e só registra em `logger.debug` (linhas 42-46),
deixando `__all__` vazio. Sem o `mcp` instalado, o bloco aborta na primeira linha
e o módulo exporta apenas `logger` e `logging`. Aí `StdioConnectionParams` — que
está em `mcp_session_manager.py:233` e sempre esteve lá — simplesmente não é
re-exportado, e o Python reporta "nome inexistente" em vez de "dependência
faltando".

A conta não fecha: um nome que sumiu de um módulo costuma ser *upgrade* de
dependência, e o conserto seria `pip install -U`. Aqui o conserto é o oposto.
Quando a mensagem aponta para o pacote instalado em vez do `requirements.txt`,
confira primeiro o que está faltando:

```bash
.venv/bin/python -c "import mcp"                      # a causa real
.venv/bin/python -c "from google.adk.tools.mcp_tool import StdioConnectionParams"
.venv/bin/python -m pip check
```

Como o `mcp` é declarado no `requirements.txt`, isso é quase sempre um venv
montado no meio do caminho — não um bug do repositório.

O mesmo mecanismo derruba `tests/test_placeholders.py`, que importa `rag` e
`mcp_text_audit`: o primeiro `ModuleNotFoundError` que aparecer é a causa raiz,
e os testes que dependem dele só voltam a passar depois.

---

## 🪤 O `pip install` do `chromadb` deixa o venv incoerente

O `requirements.txt` avisa do conflito de opentelemetry, e o aviso é completo
— com uma exceção que ele não menciona. Depois de `pip install chromadb`, o
chromadb traz o `opentelemetry-exporter-otlp-common` 0.66b0, que exige
`opentelemetry-sdk~=1.45.0` — incompatível com o `1.42.1` que o google-adk
exige e que está pinado.

Esse pacote não tem versão compatível disponível no índice: só existem a 0.65b0 e
a 0.66b0, e ambas exigem o SDK 1.45. Como ele é resíduo do install, a saída é
remover:

```bash
.venv/bin/python -m pip uninstall -y opentelemetry-exporter-otlp-common
.venv/bin/python -m pip check    # tem que responder "No broken requirements found"
```

Seguro porque nada depende dele — `pip show` reporta `Required-by:` vazio e o
chromadb não referencia o módulo em lugar nenhum. Ele só existe no caminho de
exportação OTLP, que este projeto não usa (`chromadb.PersistentClient`
embarcado, sem servidor exposto).

Ordem que funciona, do zero:

```bash
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m pip install "chromadb==1.5.9"
.venv/bin/python -m pip install "opentelemetry-api==1.42.1" "opentelemetry-sdk==1.42.1"
.venv/bin/python -m pip uninstall -y opentelemetry-exporter-otlp-common
.venv/bin/python -m pip check
```

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
