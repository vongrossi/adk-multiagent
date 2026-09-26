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

`search_tool` é resolvido em tempo de import. Sem `GOOGLE_SEARCH_API_KEY` no
`.env`, vira `None` e o agente funciona só com o HTTP. A chave nunca é lida
dentro do prompt — o SDK monta o cabeçalho internamente.
"""

import os
import re
import socket
import urllib.error
import urllib.parse
import urllib.request

TIMEOUT = 15

# Versões com --jinja são necessárias para tool calling nativo; o MCP do ADK já
# faz o transporte e a traducao de schema, entao nao ha o que implementar aqui.
GOOGLE_SEARCH_MCP = {
    "command": "npx",
    "args": [
        "-y",
        "@modelcontextprotocol/server-gemini",
        "--allow-google-search",
    ],
    "env": {"GEMINI_API_KEY": os.getenv("GOOGLE_API_KEY", "")},
    "cwd": os.getcwd(),
}

# O MCP se chama "gemini"; expomos com outro nome para o agente nao confundir
# com o modelo compartilhado de common.py.
search_tool = None
if os.getenv("GOOGLE_SEARCH_API_KEY"):
    try:
        from google.adk.tools.mcp_tool.mcp_tool import MCPToolset

        _set = MCPToolset(
            connection_params=GOOGLE_SEARCH_MCP,
            tool_filter=["google_search"],
        )
        _set.__name__ = "buscar_fontes"
        _set.description = (
            "Busca na web. Use para confirmar se um dominio/pagina realmente "
            "existe sobre um assunto, antes de aceitar uma URL."
        )
        search_tool = _set
    except Exception as _erro:  # degrada em vez de derrubar o agente inteiro
        print(f"[linkcheck] busca do Google indisponivel: {_erro}")
else:
    print(
        "[linkcheck] GOOGLE_SEARCH_API_KEY ausente: o agente so vai conseguir "
        "checar se a URL responde, nao se a fonte existe de verdade."
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


def _pedir(url: str, metodo: str):
    req = urllib.request.Request(
        url, method=metodo, headers={"User-Agent": "linkcheck/1.0 (+ADK)"}
    )
    return urllib.request.urlopen(req, timeout=TIMEOUT)


def checar_url(url: str) -> dict:
    """Checa se uma URL responde e se ela de fato existe na web.

    Faz HEAD primeiro (barato). Se o servidor recusar HEAD com 405, refaz com
    GET — muitos servidores e CDNs so aceitam GET. Um 403 e ambíguo por si so
    (paywall, bot protection): nesse caso o agente ainda pode confiar na busca
    para confirmar a existencia da pagina, entao devolvemos 403 como
    "existe, inacessivel" e nao como "morta".
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
