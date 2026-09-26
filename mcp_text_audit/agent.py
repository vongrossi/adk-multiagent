"""
Agente que consome o servidor MCP local por stdio.

=====================================================================
O QUE ESTE EXEMPLO ENSINA (E O QUE NAO ENSINA)
=====================================================================

O que ensina: o caminho completo de um toolset MCP ate um agente ADK, e as
tres coisas que quebram quando uma delas esta errada.

    MCPServer (server.py)          <- processo separado, JSON-RPC em stdio
           ▲
           │  StdioConnectionParams(server_params=StdioServerParameters(...))
           │  MCPToolset(connection_params=..., tool_filter=[...])
    Agent(tools=[toolset])         <- o ADK resolve e executa

O que NAO ensina: a diferenca entre `tools=[toolset]` e
`tools=[toolset, outra_tool]`. O ADK aceita os dois, e nao ha como forcar
pelo tipo. Um toolset que falha em conectar deixa o agente rodando *sem*aquela
capacidade, silenciosamente — o unico sinal e o que o modelo escreve na
resposta. Por isso `doctor()` abaixo, e por isso a regra de escrever no prompt
que o modelo deve dizer quando a tool nao respondeu.

=====================================================================
O TRAP: O DICIONARIO QUE NAO E DICIONARIO
=====================================================================

A forma mais comum de errar isto e passar os parametros de spawn como dict:

    McpToolset(connection_params={"command": "python3", "args": [...]})   # ERRADO

`connection_params` espera um `StdioConnectionParams`, e dentro dele um
`StdioServerParameters` — dois modelos pydantic, nao um dict.

E aqui esta a parte que machuca: **o dict passa na construcao.** Nao ha
validacao no construtor. O erro so aparece quando o toolset tenta abrir a
conexao:

    ConnectionError: Connection should be StdioServerParameters or
    SseServerParams, but got {'command': 'python3', 'args': [...]}

Ou seja: o agente sobe, importa, roda, e a tool so falta no meio da
conversa. Um `try/except` em volta da *construcao* — que e o que
`linkcheck/tools.py` faz — protege a linha errada e nao pega nada. O
`doctor()` deste arquivo existe porque e a unica forma de perguntar "o toolset
conectou?" antes de desconfiar do modelo.

=====================================================================
A REGRA DE OURO
=====================================================================

Log em servidor MCP vai para **stderr**. `stdout` e o canal do protocolo: um
`print()` e um byte a mais no meio do JSON-RPC e a conexao morre sem mensagem
util. O `server.py` deste exemplo usa `-u` no spawn justamente por causa disso
— sem buffer desligado, o log fica preso e o cliente ve conexao morta em vez
da resposta.
"""

import sys

from google.adk.agents import Agent
from google.adk.tools.mcp_tool import StdioConnectionParams
from google.adk.tools.mcp_tool.mcp_toolset import McpToolset
from mcp import StdioServerParameters

from common import model

# `sys.executable` e o mesmo interpretador deste processo. Hardcodar "python3"
# funciona na sua maquina e quebra em venv, em container e em Windows — e o
# servidor MCP e justamente o componente que sobe sozinho, sem ninguem ali
# para consertar o PATH.
SPAWN = StdioServerParameters(
    command=sys.executable,
    args=["-u", "mcp_server/server.py"],
)

CONNECTION = StdioConnectionParams(server_params=SPAWN, timeout=15.0)

TOOL_NAMES = ["contar_texto", "achar_placeholders", "achar_segredos"]

mcp_tools = McpToolset(
    connection_params=CONNECTION,
    tool_filter=TOOL_NAMES,
)

TOOLS = [mcp_tools]


async def doctor() -> dict:
    """Abre o servidor e lista as tools, sem gastar request de modelo.

    Use para diagnosticar antes de culpar o modelo. Responde "o toolset
    conectou?" — que e a pergunta que o `try/except` do linkcheck deixava sem
    resposta.
    """
    try:
        tools = await mcp_tools.get_tools()
    except Exception as e:
        return {"conectou": False, "erro": f"{type(e).__name__}: {e}"}
    return {
        "conectou": True,
        "tools": sorted(t.name for t in tools.values()),
        # O filtro e um pedido, nao uma garantia. Se uma tool nao aparecer,
        # o nome no filtro esta errado — e o agente fica sem ela em silencio.
        "faltando": sorted(set(TOOL_NAMES) - {t.name for t in tools.values()}),
    }


root_agent = Agent(
    model=model,
    name="TextAuditor",
    description="Audits text with exact counts, placeholder leaks and secret scanning over MCP.",
    instruction="""
    You audit text using the MCP tools. You never count, estimate, or claim a
    limit was respected — you call a tool and report what it says.

    ## Tools

    - `contar_texto` — exact character, word, line and URL counts
    - `achar_placeholders` — unfilled `{PLACEHOLDER}` in a prompt or template
    - `achar_segredos` — hardcoded API keys, tokens and passwords

    ## What each is for

    **Counting.** If the question involves how long something is, how many
    items, or whether a limit was exceeded, call `contar_texto`. Do not count
    in your head. Your counts drift and "roughly 95" is exactly the kind of
    number that passes review and breaks an API.

    **Placeholders.** Before sending any prompt or template onward, call
    `achar_placeholders`. An unfilled `{LIMIte}` reaches the model as literal
    text and it reads it without complaint, so no other check in the pipeline
    will catch it. Report the exact placeholders found.

    **Secrets.** Before a file is shared, committed, or published, call
    `achar_segredos`. Report file, line and kind. The tool redacts the value on
    purpose — do not ask for it, and do not try to reconstruct it.

    ## Reporting

    State the number the tool returned, not a rounded version of it. When a
    tool returns `limpo: true`, say the text is clean; do not pad the report
    with "no issues found" for things you did not check.

    If a tool call fails or the toolset is unavailable, say so plainly. A
    missing tool is a fact about the run, and hiding it turns a broken audit
    into a clean bill of health.
    """,
    tools=TOOLS,
)
