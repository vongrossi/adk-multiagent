"""
Tools do verificador de links.

Duas ferramentas, deliberadamente separadas:

  - `checar_url`: faz a requisição HTTP de verdade. É a evidência dura.
  - `buscar_fontes`: usa a API de busca do Google, se houver chave. É a prova
    de que a URL aponta para algo que realmente existe.

Por que duas: a busca é o que impede o Writer de inventar uma URL plausível
(ex.: `https://docs.python.org/3/asyncio-task.html`, que parece real e não é).
O HTTP sozinho não pega isso — devolve 404 e o agente só sabe que está morto,
não que existe uma página de verdade sobre o assunto. Juntas, elas fecham o
caso: a URL respondeu, E o assunto dela aparece nos resultados.

NENHUMA das duas tools consulta modelo, então não gastam cota.

`search_tool` é resolvido em tempo de import. Sem `BRAVE_API_KEY` no `.env`,
vira `None` e o agente funciona só com o HTTP. A chave nunca é lida dentro do
prompt — o SDK monta o cabeçalho internamente.

A busca é do Brave, não do Google: a Custom Search JSON API do Google está
fechada para novos clientes desde jan/2026, o PSE novo não aceita mais busca
na web inteira (`/create/new` dá 404) e a chave do AI Studio é recusada com
401. Detalhes e links em `linkcheck/README.md` e `BACKLOG.md` item 2.
"""

import ipaddress
import os
import re
import socket
import urllib.error
import urllib.parse
import urllib.request

from dotenv import load_dotenv

# `tools.py` le `BRAVE_API_KEY` no import, entao precisa do `.env` carregado
# por conta propria. Antes isso acontecia so por acidente: o `agent.py` importa
# `common` antes de `tools`, e `common` chama `load_dotenv()`. Importar este
# modulo direto (num teste, num script, num REPL) deixava a variavel vazia e a
# busca morria em "Connection closed" — que e o mesmo sintoma de pacote
# quebrado, e por isso custou duas horas de diagnostico.
#
# O `load_dotenv()` e idempotente e nao sobrescreve variavel ja existente, entao
# chamar aqui e em `common` nao tem efeito colateral.
#
# A consequencia IMPORTANTE e o inverso disso: como o `load_dotenv()` nao
# sobrescreve, ele tambem nao repreenche. Se um teste faz
# `os.environ.pop("BRAVE_API_KEY")` para exercitar o caminho "sem busca" e
# depois este modulo e importado, o `.env` traz a chave de volta do mesmo jeito
# — e o teste mede o caminho errado sem nenhum aviso. Por isso quem precisa
# desse estado tem que remover a chave do ambiente E fechar o `load_dotenv`
# deste modulo (ver `tests/test_local_e2e.py`).
load_dotenv()

TIMEOUT = 15

# Este bloco já foi reescrito duas vezes, e vale registrar por quê.
#
# Primeira versão: quatro defeitos ao mesmo tempo, todos mascarados pelo mesmo
# `except Exception` — o agente imprimia "busca indisponivel" e seguia. Item 2
# do BACKLOG.
#
#   1. `MCPToolset` nao existe no ADK 2.9. O nome certo e `McpToolset`, e o
#      modulo mudou: `mcp_tool.mcp_toolset`, nao `mcp_tool.mcp_tool`. O
#      ImportError caia no except e virava "busca indisponivel".
#   2. `connection_params` e um `dict` cru. O ADK aceita no construtor e so
#      quebra em `get_tools()`, com "Connection should be StdioServerParameters
#      or SseServerParams". Precisa de `StdioConnectionParams(...)`.
#   3. `@modelcontextprotocol/server-gemini` NAO EXISTE (npm 404).
#   4. env e `tool_filter` do pacote errado.
#
# Segunda versao: corrigi para `@adenot/mcp-google-search`, que e real e sobe.
# So que a API por tras — a Custom Search JSON API — esta FECHADA para novos
# clientes desde jan/2026, e o Programmable Search Engine novo nao ace mais
# "Search the entire web" (o /create/new da 404). Conferi a resposta real da
# API com a chave do AI Studio: `401 UNAUTHENTICATED - API keys are not
# supported by this API`. A fiaacao ficava correta e o destino trancado.
#
# Terceira versao (esta): Brave Search. Index proprio deles, self-serve,
# $5 de credito por mes, e um servidor MCP de verdade. O que o agente chama
# continua sendo `buscar_fontes` — so muda quem responde.
BRAVE_SEARCH_MCP = {
    "command": "npx",
    "args": ["-y", "brave-search-mcp"],
    "env": {"BRAVE_API_KEY": os.getenv("BRAVE_API_KEY", "")},
    "cwd": os.getcwd(),
}

# O servidor expoe seis tools: brave_web_search, brave_image_search,
# brave_news_search, brave_video_search, brave_local_search e
# brave_llm_context_search. So a primeira interessa.
#
# `brave_image_search` e `brave_video_search` nao servem para checar se um
# link existe. `brave_local_search` e lugares, nao assunto. E
# `brave_llm_context_search` devolve texto extraido pronto, que e uma segunda
# fonte de verdade brigando com o veredito do validador por quem leu a URL.
BRAVE_SEARCH_TOOLS = ["brave_web_search"]


def _conectar_busca(chave: str | None = None):
    """Monta o toolset de busca do Brave. Devolve None se nao der.

    `chave` existe para o teste: o servidor MCP morre no boot se
    `BRAVE_API_KEY` vier vazia, entao nao da para provar a fiação (spawn,
    handshake, `tool_filter`) sem passar algum valor. Passar uma chave falsa
    prova a fiação; passar a de verdade prova a busca.

    Isolado do import para que o teste chame direto, sem reiniciar processo.
    """
    from google.adk.tools.mcp_tool import StdioConnectionParams
    from google.adk.tools.mcp_tool.mcp_toolset import McpToolset
    from mcp import StdioServerParameters

    return McpToolset(
        connection_params=StdioConnectionParams(
            server_params=StdioServerParameters(
                command=BRAVE_SEARCH_MCP["command"],
                args=BRAVE_SEARCH_MCP["args"],
                env={"BRAVE_API_KEY": chave if chave is not None
                     else os.getenv("BRAVE_API_KEY", "")},
                cwd=BRAVE_SEARCH_MCP["cwd"],
            )
        ),
        tool_filter=BRAVE_SEARCH_TOOLS,
    )


_cache_busca = {}


async def _alvo_do_brave():
    """Resolve (e memoriza) a tool `brave_web_search` do servidor.

    O toolset fica aberto de proposito: fechar e reabrir a cada chamada paga um
    `npx` novo (~1,5 s) por URL verificada. O processo morre junto com o
    interpretador.
    """
    if "tool" not in _cache_busca:
        _set = _conectar_busca()
        for _f in await _set.get_tools():
            if _f.name == "brave_web_search":
                _cache_busca["set"] = _set
                _cache_busca["tool"] = _f
                break
    return _cache_busca.get("tool")


async def buscar_fontes(query: str, count: int = 5, tool_context=None) -> str:
    """Pesquisa na web e devolve os resultados.

    Use para confirmar que um assunto realmente tem pagina na web, e nao apenas
    que a URL responde.

    Args:
        query: o que pesquisar (assunto, titulo de pagina, nome de projeto).
        count: quantos resultados trazer, de 1 a 20.

    Returns:
        Os resultados da busca, com titulo, URL e um trecho de cada.
    """
    # `tool_context` na assinatura nao e um parametro que o modelo preenche: o
    # ADK injeta o contexto real da invocacao. Passar direto e o unico jeito
    # de a tool do MCP funcionar — ela le `ctx.session`, e um ToolContext
    # fabricado quebra com "NoneType has no attribute session". Tambem por
    # isso a funcao e `async`: a chamada e assincrona e nao ha como dar
    # `asyncio.run` de dentro do loop do agente.
    alvo = await _alvo_do_brave()
    if alvo is None:
        return ("A busca nao esta disponivel agora. Trate como falha de "
                "verificacao, nunca como confirmacao.")
    return await alvo.run_async(
        args={"query": query, "count": max(1, min(int(count), 20))},
        tool_context=tool_context)


search_tool = None
if os.getenv("BRAVE_API_KEY"):
    try:
        from google.adk.tools.function_tool import FunctionTool

        # De proposito NAO conecto aqui. `asyncio.run` no importinguishiria
        # `adk run` (funciona) de `adk web` e do runner, que ja estao dentro
        # de um loop — e levantaria "asyncio.run() cannot be called from a
        # running event loop" no simples import do agente. A ligacao acontece
        # na primeira chamada, em `_alvo_do_brave()`.
        search_tool = FunctionTool(buscar_fontes)
    except Exception as _erro:  # degrada em vez de derrubar o agente inteiro
        print(f"[linkcheck] busca do Brave indisponivel: "
              f"{type(_erro).__name__}: {_erro}")
else:
    print(
        "[linkcheck] busca do Brave desligada: falta BRAVE_API_KEY. O agente "
        "so vai conseguir checar se a URL responde, nao se a fonte existe "
        "de verdade. Chave em https://api.search.brave.com/app/keys"
    )


# URL em texto Markdown: [](https://x) e [t](https://x), com parenteses aninhados.
_URL_MD = re.compile(r"https?://[^\s<>\)\]\"'`]+")


def extrair_urls(texto: str) -> list[str]:
    """Puxa as URLs de um texto Markdown, na ordem, sem repetir."""
    vistas, achadas = set(), []
    for m in _URL_MD.finditer(texto or ""):
        u = m.group(0).rstrip(".,;:!?")
        if u not in vistas:
            vistas.add(u)
            achadas.append(u)
    return achadas


class _Bloqueado(Exception):
    """A URL aponta para rede interna, onde nao ha pagina para checar."""


def _redec_permitida() -> bool:
    """Escapatoria: `LINKCHECK_PERMITIR_REDE_LOCAL=1` reabre IPs privados.

    Existe para quem roda o agente de proposito contra um servico local (o
    `mcp_server` desta propria repo, por exemplo). Desligado por padrao.
    """
    return os.getenv("LINKCHECK_PERMITIR_REDE_LOCAL", "").strip() in ("1", "true", "True")


def _ip_proibido(ip: "ipaddress.IPv4Address | ipaddress.IPv6Address") -> str | None:
    """Motivo para bloquear o IP, ou None se ele pode ser alcancado.

    O caso que mais importa e o `169.254.169.254`: em GCP, AWS e Azure isso e
    o endpoint de metadata, e o token que ele devolve da credencial da
    instancia. Um post com essa URL fazia o agente buscar a credencial e
    devolver o texto para o modelo.
    """
    if ip.is_unspecified:
        return "endereco nao especificado"
    if ip.is_loopback:
        return "loopback local"
    if ip.is_link_local:
        return "link-local (inclui o endpoint de metadata 169.254.169.254)"
    if ip.is_private:
        # `is_private` cobre 10/8, 172.16/12, 192.168/16 e fc00::/7.
        return "faixa privada"
    # 100.64.0.0/10 e o CGNAT (RFC 6598). Nenhuma flag do `ipaddress` o
    # marca: `is_private`, `is_reserved` e `is_link_local` sao todos False.
    # Verifiquei. E faixa de operadora e de cloud, entao entra no mesmo
    # grupo de "nao e a web publica".
    if ip.version == 4 and ip in ipaddress.ip_network("100.64.0.0/10"):
        return "faixa CGNAT (100.64.0.0/10)"
    if ip.is_reserved or ip.is_multicast:
        return "faixa reservada ou multicast"
    return None


def _alvo_permitido(url: str) -> None:
    """Levanta `_Bloqueado` se `url` resolve para rede interna.

    Chamado antes do request E a cada redirect, porque um alvo publico pode
    mandar o cliente para `127.0.0.1` em tres lances — bloquear so a URL
    inicial deixa a porta aberta.
    """
    if _redec_permitida():
        return
    try:
        parsed = urllib.parse.urlparse(url)
        host = parsed.hostname
    except ValueError:
        return  # deixa o `urlopen` producezir o erro de sintaxe
    if not host:
        return

    # Host ja e um IP literal: nao ha DNS para consultar.
    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        literal = None
    if literal is not None:
        motivo = _ip_proibido(literal)
        if motivo:
            raise _Bloqueado(f"{host} e {motivo}")
        return

    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror:
        return  # host nao resolve: o request vai falhar sozinho, com mensagem util
    for info in infos:
        endereco = info[4][0]
        try:
            ip = ipaddress.ip_address(endereco)
        except ValueError:
            continue
        motivo = _ip_proibido(ip)
        if motivo:
            raise _Bloqueado(f"{host} resolve para {ip} ({motivo})")


class _RedirectSeguro(urllib.request.HTTPRedirectHandler):
    """Reaplica o bloqueio a cada salto, em vez de so a URL inicial."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        _alvo_permitido(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


# `urlopen` usa o opener global; um opener proprio impede que outro import
# troque apolitica de redirect no meio do caminho.
_OPENER = urllib.request.build_opener(_RedirectSeguro)


def _pedir(url: str, metodo: str):
    _alvo_permitido(url)
    req = urllib.request.Request(
        url, method=metodo, headers={"User-Agent": "linkcheck/1.0 (+ADK)"}
    )
    return _OPENER.open(req, timeout=TIMEOUT)


def checar_url(url: str) -> dict:
    """Checa se uma URL responde e se ela de fato existe na web.

    Faz HEAD primeiro (barato). Se o servidor recusar HEAD com 405, refaz com
    GET — muitos servidores e CDNs so aceitam GET. Um 403 e ambíguo por si so
    (paywall, bot protection): nesse caso o agente ainda pode confiar na busca
    para confirmar a existencia da pagina, entao devolvemos 403 como
    "existe, inacessivel" e nao como "morta".

    URLs que apontam para rede local sao recusadas com status `bloqueada`: o
    modelo escolhe o que checar a partir de texto que pode ter vindo da web,
    entao sem isso o agente vira um sondador de servicos internos.
    """
    if not url.lower().startswith(("http://", "https://")):
        return {"url": url, "status": "invalida", "ok": False, "detalhe": "nao e uma URL"}
    try:
        parsed = urllib.parse.urlparse(url)
        if not parsed.hostname:
            return {"url": url, "status": "invalida", "ok": False, "detalhe": "sem host"}
    except ValueError:
        return {"url": url, "status": "invalida", "ok": False, "detalhe": "URL malformada"}

    destino, status = url, None
    try:
        with _pedir(url, "HEAD") as r:
            status, destino = r.status, r.geturl()
    except _Bloqueado as e:
        # Chega antes de `URLError` porque `HTTPError` e `URLError` sao
        # irmaos, e o bloqueio nao e erro de rede: e decisao nossa.
        return {"url": url, "status": "bloqueada", "ok": False,
                "detalhe": f"rede interna, nao checada ({e})"}
    except _Bloqueado as e:
        # Antes de `URLError` de proposito: o bloqueio nao e falha de rede, e
        # uma decisao nossa. Caindo no `URLError` viraria "inalcancavel", a
        # mesma resposta de um site fora do ar, e o agente passaria a tratar
        # um servico interno como link morto em vez de recusar a checagem.
        return {"url": url, "status": "bloqueada", "ok": False,
                "detalhe": f"rede interna, nao checada ({e})"}
    except urllib.error.HTTPError as e:
        if e.code == 405:
            try:
                with _pedir(url, "GET") as r:
                    status, destino = r.status, r.geturl()
            except Exception:
                status = e.code
        else:
            status = e.code
    except urllib.error.URLError as e:
        return {
            "url": url, "status": "inalcancavel", "ok": False,
            "detalhe": str(getattr(e, "reason", e))[:80],
        }
    except (socket.timeout, TimeoutError):
        return {"url": url, "status": "timeout", "ok": False, "detalhe": f">{TIMEOUT}s"}
    except Exception as e:
        return {"url": url, "status": "erro", "ok": False, "detalhe": str(e)[:80]}

    info = {"url": url, "status": status, "final": destino, "ok": status < 400}
    if status == 403:
        info["detalhe"] = "existe mas bloqueia acesso automatizado (paywall/bot)"
        info["ok"] = True
    elif destino != url:
        info["detalhe"] = f"redireciona para {destino}"
    elif 300 <= status < 400:
        info["detalhe"] = "redireciona"
    elif status >= 400:
        info["detalhe"] = f"HTTP {status}"
    return info
