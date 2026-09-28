"""
Item 2 do BACKLOG: o MCP de busca do linkcheck nunca funcionou.

O bug era um `except Exception` em volta de quatro defeitos independentes, e o
`except` comia os quatro. O agente imprimia "busca do Google indisponivel",
seguia rodando sem busca, e o veredito dele — que depende de buscar — nunca
podia ser exercido. Pior: o aviso parecia configuracao faltando, quando o
problema era codigo quebrado.

Os quatro defeitos, e por que um teste so do tipo de objeto nao pegaria:

  1. `MCPToolset` nao existe no ADK 2.9 (o nome e `McpToolset`, e o modulo
     mudou). Um teste que so verificasse `isinstance(x, Toolset)` passaria
     contra o `dict` — o `dict` e aceito no construtor.
  2. `connection_params` como `dict` quebra SO em `get_tools()`. E o unico
     lugar onde quebra. Por isso o teste abre a conexao de verdade.
  3. `@modelcontextprotocol/server-gemini` da npm 404: o pacote nao existe.
  4. Env e `tool_filter` do pacote errado.

Corrigi os quatro para `@adenot/mcp-google-search`, que e real. Depois
descobri que a API por tras — a Custom Search JSON API — esta fechada para
novos clientes desde jan/2026, e o `/create/new` do Programmable Search Engine
responde 404. A fiacao ficava correta apontando para uma porta trancada.

Entao: Brave Search. O que este teste trava agora e que isso nao volta a
acontecer — o pacote existe na npm, sobe, e expoe `brave_web_search`. O bloco
4 vai alem: executa uma busca de verdade, que e o unico jeito de descobrir se a
chave expirou, ja que `tools/list` responde mesmo com chave invalida.
"""

import asyncio
import os
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from _adk_compat import nomes_das_tools, schema_de  # noqa: E402

falhas = []
pulados = []


def chk(desc, cond, detalhe=""):
    if cond:
        print(f"  OK    {desc}")
    else:
        falhas.append(desc)
        print(f"  FALHA {desc}  {detalhe}")
    return cond


def pula(desc, motivo):
    """Marca um bloco como nao executado, com o motivo.

    O que o CI provou: o bloco 3 falhava com `timed out after 5.0s` em todo
    runner, e por um motivo que nao era do MCP. O `npx -y brave-search-mcp`
    baixa o pacote da npm no primeiro uso; no runner isso estoura o timeout de
    5s do handshake, antes de qualquer checagem deste bloco rodar. Localmente o pacote ja
    estava em cache e passava.

    Sem este registro, o `PULADO` virava so mais uma linha de log e o sumico
    da prova ficava invisivel: o resumo contava quantas faltaram, e nao o que
    nao chegou a ser testado. Pular em silencio e o jeito mais barato de
    perder cobertura sem ninguem perceber.
    """
    print(f"  PULADO  {desc} -- {motivo}")
    pulados.append((desc, motivo))


def npx_pronto():
    """`npx` existe E o pacote ja esta em cache local.

    O download da npm e o que estoura o timeout de 5s do handshake no CI. Os
    dois estados sao distinguidos porque o conserto e diferente: sem `npx` e
    ambiente sem Node; com `npx` e cache frio. `npx -y` nao tem timeout
    proprio, entao pre-aquecer o cache e o que resolve.
    """
    if shutil.which("npx") is None:
        return False, "npx ausente (ambiente sem Node)"
    cache = os.path.expanduser("~/.npm/_npx")
    if os.path.isdir(cache):
        return True, "npx com cache aquecido"
    return False, "npx existe, mas o pacote ainda nao foi baixado (cache frio)"


async def main():
    # `linkcheck.tools` le o env no import, entao o .env tem que estar
    # carregado antes. Fazer isso aqui e explicito vale mais do que importar
    # `common` de graca e torcer pelo side effect.
    from dotenv import load_dotenv

    load_dotenv()
    import linkcheck.tools as T

    print("=" * 70)
    print("1. O QUE O CODIGO PASSA PARA O ADK (sem conexao)")
    print("=" * 70)
    cfg = T.BRAVE_SEARCH_MCP
    chk("usa o pacote real de busca do Brave",
        "brave-search-mcp" in cfg["args"], cfg["args"])
    chk("nao usa o pacote do Google, que aponta para API fechada",
        "google" not in " ".join(cfg["args"]).lower(), cfg["args"])
    chk("env tem BRAVE_API_KEY", "BRAVE_API_KEY" in cfg["env"])
    chk("nenhuma chave do Google na env da busca",
        not any("GOOGLE" in k for k in cfg["env"]), list(cfg["env"]))
    chk("nao ha mais GOOGLE_SEARCH_ENGINE_ID (o 'cx' nao existe mais)",
        "GOOGLE_SEARCH_ENGINE_ID" not in cfg["env"])
    chk("tool_filter casa com o nome real da tool",
        T.BRAVE_SEARCH_TOOLS == ["brave_web_search"], T.BRAVE_SEARCH_TOOLS)
    chk("as 5 tools que nao servem ficam de fora",
        len(T.BRAVE_SEARCH_TOOLS) == 1, T.BRAVE_SEARCH_TOOLS)

    print("\n" + "=" * 70)
    print("2. O CONSTRUTOR RECEBE StdioConnectionParams, NAO DICT")
    print("=" * 70)
    from google.adk.tools.mcp_tool import StdioConnectionParams
    from google.adk.tools.mcp_tool.mcp_toolset import McpToolset
    from mcp import StdioServerParameters

    chk("existe o nome novo McpToolset (o MCPToolset foi removido)",
        McpToolset.__name__ == "McpToolset")
    import importlib
    try:
        importlib.import_module("google.adk.tools.mcp_tool.mcp_tool")
        getattr(sys.modules["google.adk.tools.mcp_tool.mcp_tool"],
                "MCPToolset")
        chk("o nome antigo MCPToolset NAO importa mais", False,
            "voltou a existir: o except Exception voltaria a mascarar o bug")
    except (ImportError, AttributeError):
        chk("o nome antigo MCPToolset NAO importa mais", True)

    try:
        set_ = T._conectar_busca()
        chk("_conectar_busca() construiu o toolset", isinstance(set_, McpToolset))
        cp = set_.connection_params
        chk("connection_params e StdioConnectionParams",
            isinstance(cp, StdioConnectionParams), type(cp).__name__)
        chk("e nao um dict (o dict so quebra em get_tools())",
            not isinstance(cp, dict))
        chk("o server_params e StdioServerParameters",
            isinstance(cp.server_params, StdioServerParameters))
        chk("o comando e npx", cp.server_params.command == "npx")
        chk("tem o -y (npx perguntaria antes de instalar)",
            "-y" in cp.server_params.args)
    except Exception as e:
        chk("_conectar_busca() constroi o toolset", False,
            f"{type(e).__name__}: {e}")

    print("\n" + "=" * 70)
    print("3. O SERVIDOR REAL SOBE E REGISTRA A TOOL ESPERADA")
    print("=" * 70)
    # O servidor Brave morre no boot com a chave vazia ("BRAVE_API_KEY
    # environment variable is required"), entao a fiacao e provada com uma
    # chave de mentira: isso nao mente sobre nada, porque o handshake, o spawn
    # e o `tool_filter` nao dependem de a chave ser valida. So a busca de
    # verdade (bloco 4) exige a chave real.
    #
    # Isso e uma melhoria sobre o teste do Google, onde sem as duas chaves o
    # bloco pulava inteiro — o teste passava sem nunca ter conectado a nada.
    pode, motivo = npx_pronto()
    if not pode:
        pula("bloco 3: handshake real com o servidor Brave", motivo)
    else:
        set_ = None
        try:
            set_ = T._conectar_busca("chave-de-mentira-so-para-o-handshake")
            ferramentas = await asyncio.wait_for(set_.get_tools(), timeout=180)
            itens, por_nome = nomes_das_tools(ferramentas)
            chk("conectou no servidor real (handshake com chave de mentira)",
                bool(itens), itens)
            chk("a tool 'brave_web_search' existe (o tool_filter casou)",
                "brave_web_search" in itens, itens)
            for fora in ("brave_image_search", "brave_video_search",
                         "brave_news_search", "brave_local_search",
                         "brave_llm_context_search"):
                chk(f"'{fora}' foi filtrada fora", fora not in itens, itens)
            if "brave_web_search" in por_nome:
                schema = schema_de(por_nome["brave_web_search"])
                props = schema.get("properties", {})
                chk("o schema pede 'query'", "query" in props, list(props))
                chk("o schema aceita 'count' (quantos resultados)",
                    "count" in props, list(props))
        except Exception as e:
            chk("conectou no servidor real", False,
                f"{type(e).__name__}: {str(e)[:90]}")
        finally:
            if set_ is not None:
                try:
                    await set_.close()
                except Exception:
                    pass

    print("\n" + "=" * 70)
    print("4. UMA BUSCA DE VERDADE (so com chave real)")
    print("=" * 70)
    # Ate aqui provamos a fiaacao. Isto prova que a busca acontece — e e o
    # unico jeito de saber que a chave e valida, porque uma chave expirada
    # so falha aqui, nunca no `tools/list`.
    pode, motivo = npx_pronto()
    if not pode:
        pula("bloco 4: handshake com chave real", motivo)
    elif not os.getenv("BRAVE_API_KEY"):
        pula("bloco 4: busca de verdade",
             "sem BRAVE_API_KEY (a fiacao acima ja foi provada)")
    else:
        set_ = None
        try:
            set_ = T._conectar_busca()  # chave real do .env
            ferramentas = await asyncio.wait_for(set_.get_tools(), timeout=180)
            _, por_nome = nomes_das_tools(ferramentas)
            t = por_nome["brave_web_search"]
            # A tool do MCP le `ctx.session`, entao nao serve um ToolContext
            # de mentira: precisa de um InvocationContext de verdade, com
            # agente e sessao. `ToolContext(invocation_context=None)` quebra
            # com "NoneType has no attribute session" — e, pior, quebrava
            # com a mensagem errada, parecendo defeito do Brave.
            from google.adk.agents import LlmAgent
            from google.adk.agents.invocation_context import InvocationContext
            from google.adk.sessions import InMemorySessionService
            from google.adk.tools.tool_context import ToolContext
            svc = InMemorySessionService()
            sess = await svc.create_session(app_name="t", user_id="u",
                                            session_id="s")
            inv = InvocationContext(
                invocation_id="inv1",
                agent=LlmAgent(name="probe", model="gemini-2.0-flash"),
                session=sess, session_service=svc)
            ctx = ToolContext(invocation_context=inv, function_call_id="fc1")
            r = await asyncio.wait_for(
                t.run_async(args={"query": "python asyncio documentation",
                                  "count": 3},
                            tool_context=ctx), timeout=60)
            ok = r is not None and not getattr(r, "is_error", False)
            chk("a busca executou sem erro", ok,
                str(r)[:80] if r is not None else "None")
            texto = ""
            try:
                texto = r["content"][0]["text"]
            except Exception:
                pass
            chk("voltou resultado de verdade (nao mensagem de erro)",
                ("URL:" in texto or "Title:" in texto) and "error" not in texto[:60].lower(),
                texto[:70].replace("\n", " "))
            chk("voltou um link real", "https://" in texto, texto[:70])
        except Exception as e:
            chk("a busca executou", False, f"{type(e).__name__}: {str(e)[:90]}")
        finally:
            if set_ is not None:
                try:
                    await set_.close()
                except Exception:
                    pass

    print("\n" + "=" * 70)
    print("5. O AVISO DE 'SEM BUSCA' NOS DOIS ESTADOS")
    print("=" * 70)
    # Este bloco testava so o estado em que a maquina estava, entao quem tinha
    # chave nunca via o aviso ser conferido, e quem nao tinha nunca via o
    # sumico dele. Agora os dois ramos rodam sempre: o segundo recarrega o
    # modulo com a variavel fora do ambiente.
    import importlib
    import linkcheck.agent as A

    chk("a tool de busca so entra em TOOLS se existir",
        (A.search_tool in A.TOOLS) == (A.search_tool is not None),
        f"TOOLS={A.TOOLS}")

    # Regressao: eu expunha o `McpToolset` direto e tentava renomear com
    # `_set.__name__ = "buscar_fontes"`, que e decorativo. O ADK le o nome que
    # o servidor MCP reportou, entao o modelo via `brave_web_search` enquanto o
    # prompt mandava usar `buscar_fontes`. Ele se safava por sorte, via o
    # schema na lista de tools. O prompt e o nome da tool precisam concordar.
    if A.search_tool is not None:
        chk("o prompt pede `buscar_fontes`",
            "buscar_fontes" in A.link_rewriter.instruction,
            A.link_rewriter.instruction[:80])
        chk("e a tool que o modelo ve se chama `buscar_fontes`",
            A.search_tool.name == "buscar_fontes", A.search_tool.name)
        chk("o nome do pacote nao vaza para o contrato do agente",
            "brave_web_search" not in A.search_tool.name, A.search_tool.name)

    if A.search_tool is not None:
        # com a busca no ar, o aviso tem sumir: insultaria o modelo com um
        # "vc nao consegue buscar" quando ele consegue.
        chk("com a busca no ar, SEM_BUSCA fica vazio",
            A.SEM_BUSCA == "", repr(A.SEM_BUSCA[:60]))
    else:
        chk("sem a busca, SEM_BUSCA avisa que nao ha busca", A.SEM_BUSCA != "")

    # Agora o ramo oposto, independente do que a maquina tem.
    #
    # Dois detalhes que custaram uma tentativa cada:
    #  - `os.environ.pop` nao serve: `load_dotenv` (que `tools.py` chama no
    #    proprio import) repõe a variavel ausente a partir do `.env`.
    #  - recarregar so `agent.py` nao serve: `tools.py` continua no
    #    `sys.modules`, e `search_tool` e o objeto velho. E preciso recarregar
    #    os dois, na ordem da dependencia.
    #
    # Esvaziar a variavel funciona porque `load_dotenv` tem `override=False`:
    # ele nao mexe em variavel que ja existe. E o estado real de quem deixou
    # `BRAVE_API_KEY=` em branco no `.env`, que e o caso comum.
    import os as _os
    import linkcheck.tools as _T
    _antes = _os.environ.get("BRAVE_API_KEY", "")
    _os.environ["BRAVE_API_KEY"] = ""
    try:
        importlib.reload(_T)
        A2 = importlib.reload(A)
        chk("sem BRAVE_API_KEY, a tool de busca nao entra em TOOLS",
            A2.search_tool is None and A2.search_tool not in A2.TOOLS,
            f"TOOLS={A2.TOOLS}")
        chk("sem BRAVE_API_KEY, o aviso diz qual chave falta",
            "BRAVE_API_KEY" in A2.SEM_BUSCA, A2.SEM_BUSCA[:120])
        chk("sem BRAVE_API_KEY, o aviso proibe o 'ok' por omissao",
            "nao foi possivel confirmar" in A2.SEM_BUSCA, A2.SEM_BUSCA[:160])
    finally:
        _os.environ["BRAVE_API_KEY"] = _antes
        importlib.reload(_T)
        importlib.reload(A)

    print("\n" + "=" * 70)
    print("RESUMO")
    print("=" * 70)
    if falhas:
        print(f"{len(falhas)} FALHA(S):")
        for f in falhas:
            print(f"  - {f}")
        return 1
    # O resumo e onde o `PULADO` vira fato. Sem esta lista, o resultado "sem
    # falhas" se confunde com "tudo testado" — e o CI ja escondeu uma falha
    # real do Brave exatamente por causa dessa confusao.
    if pulados:
        print(f"{len(pulados)} bloco(s) nao executado(s):")
        for desc, motivo in pulados:
            print(f"  - {desc}: {motivo}")
    print("o MCP de busca aponta para o pacote certo e abre conexao de verdade")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
