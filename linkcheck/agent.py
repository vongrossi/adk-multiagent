"""
LinkChecker — verifica se as URLs de um texto realmente existem.

=====================================================================
POR QUE ESTE AGENTE EXISTE
=====================================================================

O `BlogWriter` pede "3 sources" no outline, e o modelo cumpre: escreve três
URLs em Markdown. Mas o modelo **nao tem como saber se elas existem** — ele
nao navega. O resultado sao links plausiveis e falsos, como
`https://docs.python.org/3/asyncio-task.html`: o padrao bate com a dokumentacao
real, o dominio e verdadeiro, mas a pagina nao existe. Como o Writer nao tem
tool nenhuma, nada no pipeline pegava isso.

Este agente fecha o buraco. Ele le as URLs do texto, pede prova de duas formas
independentes (HTTP + busca) e so aprova o texto quando as duas concordam.

=====================================================================
POR QUE DUAS FERRAMENTAS, E NAO UMA
=====================================================================

  - `checar_url` diz que a URL *responde*. Mata link morto, typo de dominio e
    pagina 404 — mas nao prova que a pagina seja sobre o assunto.
  - `buscar_fontes` diz que o *assunto existe* na web. E o que pega a URL
    inventada: o modelo procuraria a pagina e nao acharia, mesmo que o padrao
    do link esteja certo.

So um dos dois deixa passar defeito diferente. A regra do veredito abaixo
trata "respondeu" e "confirmado na busca" como criterios independentes.

=====================================================================
VEREDITO
=====================================================================

O loop segue a mecanica do Blogger: o gerador so reescreve se o veredito foi
"retry", e um `after_agent_callback` escalona no "ok".

O que muda aqui e o criterio de aprovacao. O texto so passa se:

  1. toda URL do post tiver sido checada com `checar_url` e estar viva; e
  2. toda URL tiver sido confirmada com `buscar_fontes`; e
  3. nenhuma URL estar no `LinkValidation` state de uma iteracao anterior.

Se nao houver chave de busca (`BRAVE_API_KEY`), o criterio 2 nao pode
ser satisfeito e o agente diz isso no veredito em vez de aprovar por omissao —
aprovar sem provar seria exatamente o defeito que ele existe para evitar.

=====================================================================
COMO RODAR

    adk run linkcheck "https://exemplo.com/a https://exemplo.org/b"
    adk run linkcheck --artifact post.md      # le um arquivo do post inteiro
    adk web .                                 # aparece no dropdown
"""

from common import model
from google.adk.agents import Agent, LoopAgent
from google.adk.tools import agent_tool
from google.genai import types

try:  # caminho normal: adk run / adk web a partir da raiz
    from linkcheck.tools import checar_url, search_tool
except ImportError:  # caminho de teste, com tests/ no sys.path
    from tools import checar_url, search_tool

TOOLS = [checar_url]
if search_tool is not None:
    TOOLS.append(search_tool)

# Sem a tool de busca o agente nao consegue cumprir o criterio 2, e ele avisa
# em vez de fingir que verificou.
SEM_BUSCA = "" if search_tool else (
    "\n\nATENCAO: a busca nao esta disponivel nesta sessao (falta "
    "BRAVE_API_KEY). Voce NAO consegue confirmar se a fonte existe, "
    "so se a URL responde. Se todos os links responderem 200, escreva "
    "`retry: nao foi possivel confirmar a existencia das fontes sem busca` "
    "em vez de dizer `ok`."
)


def escalate_when_checked(key: str):
    """Sai do LoopAgent quando o veredito e "ok".

    Callback e nao a tool `exit_loop` pelo mesmo motivo do Blogger: a tool
    escalata no evento de function_call, o que mata o turno do agente antes de
    ele gravar o veredito via output_key.
    """

    def callback(callback_context) -> "types.Content | None":
        if str(callback_context.state.get(key, "")).strip().lower().startswith("ok"):
            callback_context.actions.escalate = True
            return types.Content(
                role="model",
                parts=[
                    types.Part(
                        text=f"{callback_context.agent_name}: todos os links "
                        "confirmados, saindo do loop."
                    )
                ],
            )
        return None

    return callback


# --Sub-Agent 1: Gerador ---------------------------------------------------
# Recebe de: {link_validation?} (veredito da iteracao anterior) e o texto do
# post. Envia para: state["linkchecked_post"].
link_rewriter = Agent(
    name="LinkRewriter",
    model=model,
    description="Fixes or removes dead and invented URLs in a post.",
    instruction="""
    Below is a Markdown post. Every link in it must be a URL that really
    resolves to a real page about the cited subject.

    <post>
    {post?}
    </post>

    ## What is wrong with it

    {link_validation?}

    - If the verdict is EMPTY, this is the first pass: check the post as is.
    - If it starts with "retry", fix exactly what it listed. Keep the Markdown
      structure and the prose identical — only the links are in scope.
    - If it is "ok", re-emit the post unchanged.

    ## How to fix

    For each Markdown link in the post:

    1. Call `checar_url` with the exact URL. **You must actually call the tool
       for every single URL** — never report a status you did not get from it.
       Measured on `gemini-3.1-flash-lite`: told to check two links, it wrote
       "both responded 200" without calling the tool once, inventing the
       numbers. If you have not called it, you do not know the status.
    2. If it is dead (404, timeout, unreachable) or you cannot find a real
       page for it, either replace it with a URL that a search confirms, or
       rewrite the sentence to drop the citation. **Never invent a URL to fill
       a "3 sources" quota.** A post with zero sources is better than a post
       with three fake ones.
    3. If `buscar_fontes` is available, search for the topic of the claim to
       confirm the source exists. Cite a URL only if the search shows that
       kind of page really exists.

    Rules:
    - Keep every claim and all the Markdown formatting. Only links change.
    - Do not add commentary about what you fixed. Return only the post.
    """,
    output_key="linkchecked_post",
    tools=TOOLS,
)

# Recebe de: state["linkchecked_post"] e as ferramentas. Nao consome tool de
# busca; so julga o que ja foi checado.
link_validator = Agent(
    name="LinkValidator",
    model=model,
    description="Approves a post only if every link was proven to exist.",
    instruction="""
    You are auditing a post whose links have just been checked with
    `checar_url`, and confirmed with a web search where available.

    <post>
    {linkchecked_post?}
    </post>

    Approve ONLY if all three hold:

    1. Every Markdown link was checked and resolved (200/2xx, or 403 on a
       page that a search shows really exists).
    2. No link is a plausible-looking guess. If a URL's page could not be
       confirmed to exist, it must have been removed or replaced.
    3. The post still contains the original content and formatting.

    Respond with EXACTLY one of these two forms and nothing else:
    - `ok`                                      (if all three hold)
    - `retry: <comma separated list of problems>` (if not)""",
    output_key="link_validation",
    after_agent_callback=escalate_when_checked("link_validation"),
)

robust_link_checker = LoopAgent(
    name="RobustLinkChecker",
    description="Retries link fixing until every URL is proven to exist.",
    sub_agents=[link_rewriter, link_validator],
    max_iterations=3,
)

linkcheck_tool = agent_tool.AgentTool(agent=robust_link_checker)

root_agent = Agent(
    name="LinkChecker",
    model=model,
    description=(
        "Verifies that every URL in a text really exists, fixing or removing "
        "dead and invented links."
    ),
    instruction=f"""
    If the user gives you some text containing links, pass it straight to the
    linkcheck tool as the post. If the user gives a file path, read the file
    first and pass its contents.

    The tool retries internally until every link is proven to exist. Report
    back: how many links were found, which were dead or invented, and what you
    did about each.
    {SEM_BUSCA}
    """,
    tools=[linkcheck_tool],
)
