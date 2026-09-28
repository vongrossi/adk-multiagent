# 🗒️ Backlog

Revisão de todo o diretório em 2026-09-26, depois que os 8 agentes passaram
9/9 testes. O que segue é o que **não** está pronto, com o motivo de cada
item estar aqui.

> 📌 Um item só sai daqui quando tem teste que falha antes e passa depois.
> Bug encontrado por leitura de código e não por execução fica como
> `⚠️ NÃO VERIFICADO` — porque leitura de código, sozinha, não é evidência.

---

| 8 | **SSRF em `checar_url`** | `linkcheck/tools.py` | CRÍTICO, e explorável, não teórico. `checar_url("http://localhost:22/")` devolvia a banner `SSH-2.0-OpenSSH_9.6p1`; `169.254.169.254` (metadata de GCP/AWS/Azure) devolveria a credencial da instância. O modelo escolhe a URL a partir do texto do post, que vem da web. `file://` e `gopher://` já eram barrados pelo filtro de esquema — o buraco era só HTTP para rede interna. Corrigido: resolve o DNS e recusa loopback, privado, link-local, CGNAT e multicast, **antes** do request e **a cada redirect** (o `urlopen` segue redirect, então checar só a URL inicial deixava a porta aberta via 302). `100.64.0.0/10` exigiu checagem explícita: nenhuma flag do `ipaddress` marca CGNAT. Escape hatch com `LINKCHECK_PERMITIR_REDE_LOCAL=1` | ✅ `tests/test_ssrf.py` (11 casos) |

## 🔴 Crítico — impede o repo de ser público

| # | Item | Por que é crítico |
|---|---|---|
| 1 | **Rotacionar a chave exposta** | `GOOGLE_API_KEY` apareceu em output de terminal durante o desenvolvimento. Publicar o repo com a chave viva é incidente de segurança, não um deslize. Ache em [aistudio.google.com/apikey](https://aistudio.google.com/apikey) |
| 2 | ~~**`linkcheck`: MCP de busca nunca funcionou**~~ | `linkcheck/tools.py` | **Corrigido em duas etapas, e a segunda é a que importa.** *1ª:* QUATRO defeitos, todos mascarados pelo mesmo `except Exception` que imprimia "busca indisponível" e seguia: `MCPToolset` não existe no ADK 2.9 (é `McpToolset`, em outro módulo); `connection_params` como `dict` quebra só em `get_tools()`; `@modelcontextprotocol/server-gemini` dá 404 na npm; env e `tool_filter` do pacote errado. *2ª:* corrigi tudo, mas a **API por trás está fechada** — a Custom Search JSON API não aceita novos clientes desde jan/2026 (fim de vida 1/jan/2027), o PSE novo é obrigado a "Sites to search" (o `/create/new` dá 404) e a chave do AI Studio é recusada com `401 API keys are not supported by this API`. A fiação ficava certa apontando para porta trancada. *3ª:* **Brave Search** — índice próprio, self-serve, MCP de verdade. `BRAVE_API_KEY` no lugar das duas variáveis do Google. Lição: eu confirmei que o pacote subia e nunca fiz uma busca real — por isso passei dois turnos recomendando uma API morta | ✅ `tests/test_linkcheck_mcp.py` (sobe o npm e checa a tool; busca real quando há chave) |
| 3 | ~~**LICENSE ausente**~~ | **Resolvido.** `LICENSE` (MIT, © 2026 vongr) já está commitado e no working tree. Sem licença, o repositório é "todos os direitos reservados" por default. Para código publicado, isso não é o que ninguém quer |
| 4 | ~~**`blog_agent.txt` (148 MB)**~~ | Log de terminal (`script`), captura de 2026-09-25, 2.860 linhas. Contém **chaves reais**, não só placeholder: um `KAGGLE_API_TOKEN` (`KGAT_...`, 37 chars, formato válido) que **nem está no `.env`**, o `OPENROUTER_API_KEY` real em 8 lugares e o `GOOGLE_API_KEY` real (prefixo `AQ.Ab8RN...`) além dos placeholders (`sk-noop`, `process.env.*`, `sua_chave`). Estava no `.gitignore` desde 26/09 e **nunca entrou em nenhum commit** (6 commits auditados, 0 ocorrências). **Movido em 2026-09-28 para `~/blog_agent_2026-09-25.txt`**, fora do repo. O token e as chaves reais que ele continha continuam válidos → **item 1 cobre a rotação de todos eles** | ✅ movido para fora do repo; a chave exposta que o motivou segue no item 1 |

---

## 🟠 Funcional — o agente não cumpre o que o README promete

| # | Item | Agente | Evidência |
|---|---|---|---|
| 5 | ~~**SEO não valida em código**~~ | `seo/` | **Corrigido.** O callback agora chama `validar_metadados` e o Python tem a última palavra: se o LLM disser `ok` e o Python reprovar, o `ok` é sobrescrito por `retry: <erros>`. De quebra achou um furo no portão — `_slug_valido` usava `isalnum()`, que aceita `ã`/`ç` em Python, então slug acentuado passava; agora exige `isascii()`. Tabela do README corrigida (os campos `title`/`description`/`alt` e os limites 200/60/125 nunca existiram no código) | ✅ `tests/test_seo_gate.py` (13 casos de bloqueio + hierarquia de autoridade) |
| 6 | ~~**`{LIMITE_*}` literal no prompt**~~ | `seo/` | **Corrigido, e era pior do que parecia.** Não era "o modelo lia texto estranho": como `LIMITE_TITULO` é identificador Python válido, o ADK o tratava como placeholder de state, não achava no state e levantava **`KeyError`**. O `SeoGenerator` não degradava — quebrava antes do modelo rodar. O `instruction` virou f-string (os `{LIMITE_*}` interpolam, os `{blog_post?}` ficaram `{{...}}`). O teste de régua achou o **mesmo bug em `mcp_text_audit`**, escrito por mim no mesmo dia | ✅ `tests/test_placeholders.py` |
| 7 | ~~**`codereview` não recebe caminho**~~ | `codereview/` | **Corrigido.** `_resolver_alvo()` lê o `sys.argv`: `adk run codereview common.py` passa o caminho como *query* do click, e essa é a única fonte no momento do import. Suporta `--file X` / `-f X` / `--file=X`. Dois achados no caminho: o `--file` que o docstring prometia **não existe no CLI do adk** (o click aborta), e `adk web` passa `.` e `codereview` no argv — um parser ingenuo leria o código do agente. O filtro `os.path.isfile` barra os dois. A tool segue sem parâmetro: o caminho vem do shell, nunca do modelo | ✅ `tests/test_codereview_alvo.py` |
| 8 | **RAG: indexação real nunca rodou** | `rag/` | Plumbing testado com embedder falso (18/18). A chamada real morreu com 429. Falta um `--mock`/dry-run ou esperar a cota | Bloqueado por cota |
| 9 | **`triage`: calibração e resposta real nunca rodaram** | `triage/` | A **regra** está testada (sem API): 14 checks de limiar, runner-up, precedência de provedor. O que nunca rodou é a **chamada real**. Dois bloqueios, nesta ordem: (a) a API direta da TypeSafe passou por waitlist — resolvido, o mesmo System One é servido pelo OpenRouter, sem waitlist e sem conta extra, com o payload idêntico; (b) o Jev **não é um modelo `:free`**, então a cota de conta nova do OpenRouter não cobre e a chamada morre com `402 Insufficient credits`. A autenticação e o endpoint já foram validados de verdade (o 402 vem depois do auth, o que prova que o payload está no formato certo). Falta saldo para o `triage` classificar tickets reais, e ainda falta o item mais importante: mensagens **rotuladas** para conferir se o limiar 0.85 faz sentido no nosso dataset. Mock não prova calibração | 🔶 `tests/test_triage.py` (caminho do provedor, sem rede) — resposta real: bloqueado por cota |
| 9b | **Eval só mede modelo do Google** | `tests/benchmarks/` | `comparar_lite.py` tem a URL da API do Google e `KEY = os.environ["GOOGLE_API_KEY"]` **hardcoded**: ele não roda contra o llama.cpp local nem contra o Jev, e o `KeyError` é cru se a máquina não tiver chave do Google. Os candidatos `gemma-4-26b-a4b-it` / `gemma-4-31b-it` são Gemma **hospedado no Google**, não o GGUF local — então "comparar local vs nuvem" não é possível com ele. Sem isso não dá para responder com dado a pergunta que importa agora: o Gemma local é bom o bastante para ser o padrão? Ver item 15 | ⚠️ mede só o que já medíamos |
| 10 | ~~**RAG: `chromadb` sobe opentelemetry conflitante**~~ | `rag/` | **Corrigido, e o `requirements.txt` estava incompleto.** O aviso de conflito se confirmou inteiro: `pip install chromadb` derrubou o `opentelemetry-api` de 1.42.1 para 1.45.0, e o google-adk 2.9.2 exige `<=1.42.1`. Os pins do `requirements.txt` resolvem isso — mas o instalava **deixava `pip check` vermelho mesmo assim**, por um motivo que o arquivo não previa: o chromadb também traz o `opentelemetry-exporter-otlp-common` 0.66b0, que exige `opentelemetry-sdk~=1.45.0`. Não existe versão dele compatível com o SDK pinado (o índice só tem 0.65b0 e 0.66b0), então não dá para resolver por upgrade. Removido — `pip show` acusa `Required-by:` vazio e o chromadb não referencia o módulo; ele só existe no caminho de exportação OTLP, que este projeto não usa. `pip check` fecha limpo. Registrado no `CONTRIBUTING.md` | ✅ `pip check` limpo, 15/15 suítes |
| 11 | ~~**Falta `__init__.py` nos agentes**~~ | **Resolvido em 2026-09-28.** Nenhum agente tinha — nem o `blogger/`. Criei `__init__.py` vazio em `blogger/`, `linkcheck/`, `seo/`, `triage/`, `rag/`, `codereview/`, `researcher/`, `mcp_text_audit/` e a suíte seguiu 15/15 (o loader do ADK não depende do arquivo). `adk web` e loaders que esperam pacote explícito agora acham | ✅ 15/15 suítes depois da criação; `git ls-files` confirma os 8 |

---

## 🟡 Qualidade — o repo funciona, mas não se sustenta sozinho

| # | Item | Motivo |
|---|---|---|
| 12 | ~~**CI (GitHub Actions)**~~ | **Resolvido em 2026-09-28.** `.github/workflows/tests.yml` roda a suíte offline em todo push/PR. A 2ª rodada real travou no timeout de 5s do handshake MCP do Brave; o conserto virou o `MCP_TIMEOUT` no `StdioConnectionParams` (default 60s, `180` no CI) | ✅ 15/15 em ~42s no runner |
| 13 | ~~**`tests/test_mcp.py` fora do `run_all.py`**~~ | **Resolvido.** Sobe um subprocesso MCP | ✅ já entra no `run_all.py` (15/15) |
| 14 | **`researcher/` ainda é exemplo** | Sem ferramentas de busca, os "3 sources" podem ser inventados. O `linkcheck` foi feito para pegar isso, mas não está ligado ao `researcher` |
| 15 | **Benchmarks não rodam em CI** | Gastam cota. Correto deixá-los de fora — mas merece um README explicando o custo e quando rodar |
| 16 | ~~**Sem `CONTRIBUTING.md`**~~ | **Resolvido em 2026-09-28**, junto com o `ba0d48d`. Como rodar os testes, o estilo e o que um PR precisa | ✅ commitado |
| 17 | ~~**`.env.example` não menciona `typesafe-sdk`**~~ | **Resolvido.** O `.env.example` commitado já tem a seção completa do Jev: `OPENROUTER_API_KEY`, `TYPESAFE_API_KEY`, `JEV_PROVEDOR` (com preferência de provedor) e o aviso de que o Jev não é modelo `:free` | ✅ `git show HEAD:.env.example` tem as 4 variáveis |
| 18 | **Diagramas Mermaid não validados** | GitHub renderiza, mas o parser não foi checado. Um `flowchart` com sintaxe errada só quebra no render |
| 18b | **`cannot import name 'StdioConnectionParams'` aponta para o pacote errado** | O `google/adk/tools/mcp_tool/__init__.py` (linhas 42-46) envolve os imports num `try/except ImportError` e só loga em `debug`. Sem o `mcp` instalado, `__all__` fica vazio e a classe — que existe em `mcp_session_manager.py:233` — não é re-exportada. O sintoma é `ImportError: cannot import name`, que manda procurar problema de *upgrade* de dependência, quando o conserto é o oposto: `pip install -r requirements.txt`. Como o `mcp` **já é** obrigatório no `requirements.txt`, isso é venv montado pela metade, não bug do repo — mas a mensagem não diz isso, e diagnosticar custa um tempo. Afeta `test_mcp.py`, `test_linkcheck_mcp.py` e, por tabela, `test_placeholders.py`. Enquanto o ADK não corrigir, o `import mcp` direto no topo dos dois testes que dependem dele daria o erro certo 🪤 |

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
| Busca Brave: pacote, tool, filtro, conexão real | `test_linkcheck_mcp.py` (pula a busca sem `BRAVE_API_KEY`) |
| SSRF: a URL do post não vira scanner de rede interna | `test_ssrf.py` (11 casos) |
| Portão de SEO em Python, não no prompt | `test_seo_gate.py` |
| `codereview` só lê dentro da raiz do repo | `test_codereview_alvo.py` |
| **Total** | **15/15 suítes** em ~196s local (~42s no CI GitHub Actions), sem rede e sem chave |

### Validação com modelo de verdade

A suite acima sobe um `llama-server` **falso**, que aceita qualquer payload.
Isso prova a fiação do ADK, não que um modelo real obedece o prompt. Com um
GGUF de verdade (`tests/test_local_e2e.py`, fora da suite porque exige 806 MB
ou 2,4 GB de download): **23/23** com `gemma-3-4b-it-Q4_K_M` em CPU, sem
nenhuma chave no ambiente.

Foi essa validação que encontrou o bug do `_normalizar_roles()`: o template
Jinja do Gemma 3 exige alternância estrita de `role` e levanta 400 em
`Conversation roles must alternate...`, porque o ADK transforma cada
`function_response` num `Content(role='user')` próprio. Nenhum teste com
servidor falso pegaria isso. Registrado como item 8 da seção "O que está
pronto" abaixo.

---

## 🎯 Ordem sugerida

1. **1-4** antes de qualquer push. Especialmente o item 1.
2. **5, 6, 7** — os três que o README promete e o código não cumpre. Um reader
   vai achá-los na primeira leitura.
3. **12, 16** — o que faz o repo se manter sozinho depois do primeiro commit.
4. **8, 9, 10** — dependem de cota/dados, não de código.
5. O resto.
