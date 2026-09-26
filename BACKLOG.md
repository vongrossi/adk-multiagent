# 🗒️ Backlog

Revisão de todo o diretório em 2026-09-26, depois que os 8 agentes passaram
9/9 testes. O que segue é o que **não** está pronto, com o motivo de cada
item estar aqui.

> 📌 Um item só sai daqui quando tem teste que falha antes e passa depois.
> Bug encontrado por leitura de código e não por execução fica como
> `⚠️ NÃO VERIFICADO` — porque leitura de código, sozinha, não é evidência.

---

## 🔴 Crítico — impede o repo de ser público

| # | Item | Por que é crítico |
|---|---|---|
| 1 | **Rotacionar a chave exposta** | `GOOGLE_API_KEY` apareceu em output de terminal durante o desenvolvimento. Publicar o repo com a chave viva é incidente de segurança, não um deslize. Ache em [aistudio.google.com/apikey](https://aistudio.google.com/apikey) |
| 2 | **`linkcheck`: MCP de busca nunca funcionou** | `linkcheck/tools.py` passa um **dict** onde o ADK exige `StdioConnectionParams`. O dict passa na construção e só falha na conexão — o `try/except` protege a linha errada. Resultado: o agente sobe, roda, e **não tem busca** |
| 3 | **LICENSE ausente** | Sem licença, o repositório é "todos os direitos reservados" por default. Para código publicado, isso não é o que ninguém quer |
| 4 | **`blog_agent.txt` (70 MB)** | Log de terminal. Contém uma chave hardcoded capturada em código (`API_KEY = "sk-..."`). Está no `.gitignore` agora, mas **o arquivo continua no disco** — apagar ou mover |

---

## 🟠 Funcional — o agente não cumpre o que o README promete

| # | Item | Agente | Evidência |
|---|---|---|---|
| 5 | **SEO não valida em código** | `seo/` | `validar_metadados()` existe mas **não está ligado** ao callback. O loop só lê `seo_validation` do LLM. A tabela do README diz "validação em Python" — hoje é mentira | `⚠️ NÃO VERIFICADO` |
| 6 | **`{LIMITE_*}` literal no prompt** | `seo/` | O `instruction` é string normal com `{LIMITE_TITULO}` e `{{chaves}}`. Chegam literais ao modelo. Foi exatamente o bug que motivou a tool `achar_placeholders` | `⚠️ NÃO VERIFICADO` |
| 7 | **`codereview` não recebe caminho** | `codereview/` | `ARQUIVO = None` no import, sem parsing de argv. `adk run codereview common.py` não tem como chegar o path na tool `ler_arquivo` | `⚠️ NÃO VERIFICADO` |
| 8 | **RAG: indexação real nunca rodou** | `rag/` | Plumbing testado com embedder falso (18/18). A chamada real morreu com 429. Falta um `--mock`/dry-run ou esperar a cota | Bloqueado por cota |
| 9 | **`triage`: calibração não validada** | `triage/` | A **regra** está testada (sem API). Que o limiar 0.85 faz sentido no seu dataset **não está**, e mock não prova. Precisa de mensagens reais rotuladas | Precisa de dados |
| 10 | **RAG: `chromadb` sobe opentelemetry conflitante** | `rag/` | Instalado à mão levou `opentelemetry-api` a 1.45; `google-adk 2.9.2` exige `<=1.42.1`. Já pini no `requirements.txt`, mas `pip install` do zero precisa ser testado | Fix escrito, não testado |
| 11 | **Falta `__init__.py` nos agentes** | todos | Só `blogger/` tem. Os outros funcionam por *namespace package*, o que funciona — mas `adk web` e alguns loaders esperam o arquivo. Verificar em cada ambiente | `⚠️ NÃO VERIFICADO` |

---

## 🟡 Qualidade — o repo funciona, mas não se sustenta sozinho

| # | Item | Motivo |
|---|---|---|
| 12 | **CI (GitHub Actions)** | Não existe. O `run_all.py` roda em 17s offline — barato o bastante para rodar em todo PR. Sem CI, o "9/9" é uma afirmação de uma máquina só |
| 13 | **`tests/test_mcp.py` fora do `run_all.py`** | Sobe um subprocesso MCP; roda em 2s. Hoje só entra se chamado à mão |
| 14 | **`researcher/` ainda é exemplo** | Sem ferramentas de busca, os "3 sources" podem ser inventados. O `linkcheck` foi feito para pegar isso, mas não está ligado ao `researcher` |
| 15 | **Benchmarks não rodam em CI** | Gastam cota. Correto deixá-los de fora — mas merece um README explicando o custo e quando rodar |
| 16 | **Sem `CONTRIBUTING.md`** | Como rodar os testes, estilo, o que um PR precisa |
| 17 | **`.env.example` não menciona `typesafe-sdk`** | Comentado no `triage/README.md`, ausente no `.env.example` |
| 18 | **Diagramas Mermaid não validados** | GitHub renderiza, mas o parser não foi checado. Um `flowchart` com sintaxe errada só quebra no render |

---

## 🟢 Backlog do backlog — nice to have

| # | Item |
|---|---|
| 19 | Prompt compartilhado: os 5 agentes repetem "cite a fonte / não invente" em instrução. Um template común reduz divergência |
| 20 | Métrica: quantas vezes o modelo **pula** uma tool por agente. O `linkcheck` provou que isso acontece; não há instrumentação |
| 21 | `blogger/` + `linkcheck/` + `seo/` ponta a ponta num teste só. Hoje cada um é testado isolado |
| 22 | Áudio/texto: o `triage` classifica ticket escrito. Áudio transcrito muda o problema (Ruído,PT-BR) |
| 23 | Fallback chain: se o Jev cair, o `triage` cai no LLM de texto. Testar o caminho de erro end-to-end, não só unitário |
| 24 | Rate limiter: 20/dia é apertado. Um contador no `common.py` evitaria 429 surpresa no meio de um run |

---

## ✅ O que está pronto

Não mexer sem teste que falhe:

| Item | Evidência |
|---|---|
| Cadeia de modelo com fallback e retry | `test_fallback.py`, `test_llamacpp.py` |
| Backend llama.cpp | `test_emulacao.py` (tool calling emulado), `test_blogger_local.py` |
| Loop do Blogger (`ok`/`retry`/escalate) | `test_blogger_retry.py` |
| Limites do Blogger, extração de URL, segredo, path traversal | `test_novos_agentes.py` |
| RAG: chunkagem, Chroma, reindex, similaridade | `test_rag.py` (embedder falso) |
| `triage`: limiar, runner-up, fallback | `test_triage.py` |
| MCP: spawn, registro, schema, execução | `test_mcp.py` |
| ADK puro: state, callback, LoopAgent, AgentTool | `e2e_check.py` |
| Prompt do `linkcheck` (parou de inventar status) | `test_linkcheck_tool_call.py` (API real) |
| **Total** | **9/9 suítes** em ~17s |

---

## 🎯 Ordem sugerida

1. **1-4** antes de qualquer push. Especialmente o item 1.
2. **5, 6, 7** — os três que o README promete e o código não cumpre. Um reader
   vai achá-los na primeira leitura.
3. **12, 16** — o que faz o repo se manter sozinho depois do primeiro commit.
4. **8, 9, 10** — dependem de cota/dados, não de código.
5. O resto.
