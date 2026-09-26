"""
Blogger multi-agente (Google ADK) — pipeline Plan -> Write -> Validate.

======================================================================
MAPA DE FLUXO DE DADOS
======================================================================

O topico NAO viaja pelo state: o root_agent o repassa no argumento `request`
de cada AgentTool, e esse argumento chega ao sub-agente como MENSAGEM DE USUARIO
(confirmado no trace). O que viaja pelo state sao os artefatos intermediarios:

  state["blog_outline"]       <- BlogPlanner                (escreve)
  state["blog_outline"]       <- OutlineValidationChecker   (lê: {blog_outline?})
  state["blog_outline"]       <- BlogWriter                 (lê: {blog_outline?})
  state["blog_outline"]       <- BlogPostValidationChecker  (lê: {blog_outline?})
  state["outline_validation"] <- OutlineValidationChecker   (escreve)
  state["outline_validation"] <- BlogPlanner                (lê: iteração anterior)
  state["blog_post"]          <- BlogWriter                 (escreve) = saída final
  state["blog_post"]          <- BlogPostValidationChecker  (lê: {blog_post?})
  state["post_validation"]    <- BlogPostValidationChecker  (escreve)
  state["post_validation"]    <- BlogWriter                 (lê: iteração anterior)

  Tools do root_agent (Blogger):
    planner_tool (AgentTool -> RobustBlogPlanner)  escreve blog_outline
    writer_tool  (AgentTool -> RobustBlogWriter)   escreve blog_post

  !!! IMPORTANTE !!!
  Citar o nome da chave no prompt nao faz nada: o LLM so enxerga o valor de
  uma chave do state se ela for interpolada com {chave} no instruction.
  O `?` (ex.: {blog_outline?}) torna a interpolacao opcional — sem a chave
  no state, entra string vazia em vez de KeyError. Todo agente que precisa
  ler algo do state tem que ter o placeholder correspondente no instruction.

======================================================================
COMO O LOOP DE VALIDAÇÃO FUNCIONA
======================================================================

  LoopAgent = [Gerador, Validador] repetido até max_iterations.

  - O Gerador lê o veredito do Validador da iteração ANTERIOR
    ({outline_validation?} / {post_validation?}) e só refaz o trabalho
    se o veredito foi "retry".
  - O Validador grava "ok" ou "retry: <problemas>" na sua chave.
  - Quando o veredito é "ok", o callback escalate_when_approved marca o
    evento com escalate=True e o LoopAgent encerra na hora.

  Por que callback e não a tool `exit_loop`? A tool escalata no evento de
  function_call, o que mata o turno do validador antes dele emitir o texto
  "ok" — e aí o veredito nunca é gravado no state. O callback roda depois
  do turno, com o texto já salvo via output_key.

  AVISO: no ADK 2.9.2 o LoopAgent está depreciado em favor de `Workflow`,
  mas ainda é a única forma de loop compatível com sub-agent de LlmAgent
  (é exatamente o caso aqui: os loops são chamados via AgentTool).

======================================================================
COMO RODAR
======================================================================

  adk run blogger "escreva sobre asyncio structured concurrency"
  adk web .                                 -> http://127.0.0.1:8000

  O ADK 2.9.2 espera a PASTA do agente, nunca o arquivo `agent.py`. E o
  agente raiz tem que se chamar `root_agent` (nome exigido pelo framework).
  Como este agente mora em `blogger/`, o comando aponta para a pasta.

  A pasta raiz do projeto é lida assim: o ADK sobe a árvore de diretórios a
  partir de `blogger/` até achar o `.env`, e também a insere no `sys.path`
  — por isso `from common import model` funciona daqui.
"""

import datetime

from common import model
from google.adk.agents import Agent, LoopAgent
from google.adk.tools import agent_tool
from google.genai import types

# Modelo, MODEL e o .env vêm de `common.py` (na raiz do projeto) para que
# todos os agentes compartilhem a mesma instância com retry configurado.


def escalate_when_approved(verdict_key: str):
    """Cria um after_agent_callback que sai do LoopAgent quando o veredito e "ok".

    Recebe de: state[verdict_key], que o proprio agente acabou de gravar
               atraves do output_key.
    Envia para: event.actions.escalate=True -> o LoopAgent pai interrompe o
                 ciclo sem consumir as iteracoes restantes.
    """

    def callback(callback_context) -> "types.Content | None":
        verdict = str(callback_context.state.get(verdict_key, "")).strip()
        if verdict.lower().startswith("ok"):
            callback_context.actions.escalate = True
            return types.Content(
                role="model",
                parts=[
                    types.Part(
                        text=f"{callback_context.agent_name}: aprovado, "
                        "saindo do loop."
                    )
                ],
            )
        return None

    return callback


# --Sub-Agent 1: Planner ---------------------------------------------------
# Funcao:     transforma um topico em outline Markdown (H2/H3).
# Recebe de:  {outline_validation?} -> state["outline_validation"], escrito na
#             iteracao anterior pelo OutlineValidationChecker. Vazio na 1a.
#             (state["topic"] foi escrito pelo root_agent a partir da pergunta.)
# Envia para: state["blog_outline"], via output_key.
blog_planner = Agent(
    name="BlogPlanner",
    model=model,
    description="Creates a pratical, skimmable outline in Markdown.",
    instruction="""
    You are a technical content strategist. Produce a clear Markdown outline with:
    - Title
    - Short intro
    - 4-6 main sections (each with 2-3 bullets)
    - Conclusion

    ## Review loop
    The validator verdict from the previous iteration is below:

    {outline_validation?}

    - If it is EMPTY, this is the first pass: create the outline.
    - If it starts with "retry", do NOT start over. Fix exactly the missing
      pieces it listed, keeping the parts that already passed.
    - If it is "ok", the previous outline was approved: re-emit it unchanged.

    If a `codebase_context` key ever exists in state, weave in its specific
    sections and snippets. Today no tool writes that key, so ignore it.

    Return only the outline in Markdown.
    """,
    output_key="blog_outline",
)

# Validador do outline. Nao consome tool: apenas julga e grava o veredito.
# O after_agent_callback e o que faz o LoopAgent parar quando o veredito e "ok".
# Recebe de: state["blog_outline"].
# Envia para: state["outline_validation"] (output_key) + escalate (encerra o loop).
OutlineValidationChecker = Agent(
    name="OutlineValidationChecker",
    model=model,
    description="Validates that outline is usable.",
    instruction="""
    Check the outline below. It must have a title, an intro, 4-6 sections,
    and a conclusion.

    <outline>
    {blog_outline?}
    </outline>

    Respond with EXACTLY one of these two forms and nothing else:
    - `ok`                                          (if it is valid)
    - `retry: <comma separated list of problems>`   (if it is not)
    """,
    output_key="outline_validation",
    after_agent_callback=escalate_when_approved("outline_validation"),
)

# Agrupa planner + validador num loop de ate 3 passes.
# Recebe de: state["topic"]. Envia para: state["blog_outline"].
robust_blog_planner = LoopAgent(
    name="RobustBlogPlanner",
    description="Retries planning if validation fails.",
    sub_agents=[blog_planner, OutlineValidationChecker],
    max_iterations=3,
)


# --Sub-Agent 2: Writer ----------------------------------------------------
# Funcao:     transforma o outline em artigo Markdown completo.
# Recebe de:  {blog_outline?} -> state["blog_outline"], gravado pelo
#             BlogPlanner. Os dois loops compartilham o mesmo state da
#             invocacao, entao o writer ve o outline sem precisar de tool.
#             {post_validation?} -> veredito do BlogPostValidationChecker da
#             iteracao anterior. Vazio na 1a.
# Envia para: state["blog_post"], via output_key.
blog_writer = Agent(
    name="BlogWriter",
    model=model,
    description="Writes a techinal blog post from the outline.",
    instruction="""
    Write a complete Markdown article from the outline below.

    <outline>
    {blog_outline?}
    </outline>

    Guidelines:
    - Audience: software engineers; skip basics and focus on pratical insight.
    - Explain both the 'how' and 'why'.
    - Include concise code snippets when helpful.
    - Follow the outline's structure (H2/H3).
    - Output only the final article in Markdown (no text around the whole post).

    ## Review loop
    The validator verdict from the previous iteration is below:

    <verdict>
    {post_validation?}
    </verdict>

    - If it is EMPTY, this is the first pass: write the article.
    - If it starts with "retry", rewrite the post fixing exactly the listed
      issues instead of starting from scratch.
    - If it is "ok", the previous post was approved: re-emit it unchanged.
    """,
    output_key="blog_post",
)

# Validador do post: mesma mecanica do validador do outline.
# Recebe de: state["blog_post"]. Envia: state["post_validation"] + escalate.
BlogPostValidationChecker = Agent(
    name="BlogPostValidationChecker",
    model=model,
    description="Validates the final post.",
    instruction="""
    Check the post below for: an intro, clear sections matching the outline,
    a conclusion, and technical clarity.

    <outline>
    {blog_outline?}
    </outline>

    <post>
    {blog_post?}
    </post>

    Respond with EXACTLY one of these two forms and nothing else:
    - `ok`                                          (if it passes)
    - `retry: <comma separated list of fixes>`      (if it does not)
    """,
    output_key="post_validation",
    after_agent_callback=escalate_when_approved("post_validation"),
)

# Agrupa writer + validador num loop de ate 3 passes.
# Recebe de: state["blog_outline"] (do loop do planner).
# Envia para: state["blog_post"].
robust_blog_writer = LoopAgent(
    name="RobustBlogWriter",
    description="Retries writing if validation fails.",
    sub_agents=[blog_writer, BlogPostValidationChecker],
    max_iterations=3,
)


# Expoe os dois loops como tools que o root_agent pode chamar.
# Sem essa camada o root veria um unico agente e nao conseguiria ordenar
# plan -> write; com ela, ele os chama em sequencia via function call e o
# state gravado por um fica disponivel para o outro.
planner_tool = agent_tool.AgentTool(agent=robust_blog_planner)
writer_tool = agent_tool.AgentTool(agent=robust_blog_writer)


# -- Root Agent: orquestrador ---------------------------------------------
# Funcao:     coordena plan -> write e formata a resposta final.
# Recebe de:  a mensagem do usuario (o topico). E o unico agente que ve o
#             usuario; os sub-agentes nunca falam com ele diretamente.
# Envia para: planner_tool e writer_tool (tools), que preenchem o state.
#             A resposta final (titulos alternativos + hooks) volta direto
#             no chat, sem passar pelo state.
root_agent = Agent(
    name="Blogger",
    model=model,
    description="Minimal multi-agent blogger that plans and writes.",
    instruction=f"""
    If the user gives a topic:
    1) Call the planner tool to generate the outline. It retries internally
       until its validator approves.
    2) Call the writer tool to produce the full draft. It retries internally
       until its validator approves.
    3) End with 3 alternate titles and 2 tweet-length hooks.

    Always call planner before writer, and never invent an outline yourself.

    A trends/search tool can be added to the `tools` list below later; if it
    times out, continue with the outline and draft you already have.

    Date: {datetime.datetime.now().strftime("%Y-%m-%d")}
    """,
    tools=[
        planner_tool,  # -> RobustBlogPlanner -> state["blog_outline"]
        writer_tool,  # -> RobustBlogWriter  -> state["blog_post"]
    ],
)
