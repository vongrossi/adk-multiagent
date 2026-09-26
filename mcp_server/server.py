#!/usr/bin/env python3
"""
Servidor MCP de exemplo — analise deterministica de texto.

=====================================================================
POR QUE ESTE EXEMPLO EXISTE
=====================================================================

Este e um servidor MCP minimo, no sentido estrito: um arquivo, tres tools,
transporte stdio. Ele existe para mostrar o PADRAO completo, nao para ser
util. O padrao tem cinco pecas:

  1.Um processo Python que roda sozinho e fala JSON-RPC em stdin/stdout.
  2.Decoradores `@mcp.tool()` que registram funcoes Python como tools.
  3.  Docstrings em ingles — o texto que o modelo le para decidir quando
     chamar a tool. Uma docstring ruim aqui produz um agente que nao chama.
  4.Um `MCPToolset` do lado do cliente, com `StdioConnectionParams`.
  5.O ADK sobe e derruba o processo sob demanda, por `stdio`.

=====================================================================
O QUE AS TOOLS FAZEM, E POR QUE NAO DELEGAR AO LLM
=====================================================================

As tres tools fazem o mesmo tipo de trabalho, e e por isso que ele esta aqui:

  - `contar_texto`      conta caractere, palavra, link
  - `achar_placeholders` acha `{LIMITE_TITULO}` que ninguem preencheu
  - `achar_segredos`    acha `API_KEY = "..."` no meio de um arquivo

Nenhuma delas precisa de inteligencia. Todas precisam de *exatidao*, que e
justamente onde um LLM de texto e ruim: ele conta caractere comprecatedo, e
"aproximadamente 95" e exatamente o tipo de numero que passa num review e
quebra num limite de API.

O `achar_placeholders` nao e hipoteetico. Ele foi escrito porque este
repositorio tinha exatamente esse bug: um prompt de agente com `{LIMITE_TITULO}`
literal, que chegava ao modelo como texto em vez de numero. Nenhum teste de
"o agente responde certo" pegaria — o modelo leria o placeholder com toda
naturalidade. Um teste que procura `{` resolve.

=====================================================================
TRANSPORTE
=====================================================================

`stdio` significa: o cliente abre um subprocesso e conversa pelos descritores
1 e 2. Nao ha porta, nem HTTP, nem token. Por isso `print()` neste arquivo
quebraria o protocolo — se precisar logar, escreva em stderr.
"""

import os
import re
import sys

# O log vai para stderr de proposito. stdout e o canal do protocolo MCP, e
# qualquer byte extra ali corrompe o JSON-RPC. (Aprendi isso do jeito dificil:
# um `print` de debug no meio do servidor derruba a conexao sem mensagem.)
def _log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


from mcp.server.mcpserver import MCPServer  # noqa: E402

mcp = MCPServer(
    name="texto-adk",
    instructions=(
        "Deterministic text analysis: exact counts, unfilled template "
        "placeholders, and hardcoded secrets. Use these instead of counting or "
        "guessing, because the numbers here are exact and yours are not."
    ),
)

# Limites do Blogger, para `contar_texto` poder dizer "estourou" sem o
# modelo precisar saber o numero. Mover a regra para ca e o ponto: o codigo
# conhece o limite, o prompt nao.
LIMITES = {
    "title": 100,
    "description": 200,
    "slug": 50,
    "alt": 125,
}

# Placeholder de template: {ALGUMA_COISA}, mas nao {{escapado}} e nao
# set de programacao. Explicito porque a alternativa (qualquer `{}`) gera
# falso positivo em cada f-string e dict do arquivo.
PLACEHOLDER = re.compile(r"\{[A-Za-z_][A-Za-z0-9_]{1,40}\}")

SEGREDOS = [
    (r"AIza[0-9A-Za-z_\-]{35}", "chave de API do Google"),
    (r"sk-[A-Za-z0-9_\-]{20,}", "chave de API estilo OpenAI"),
    (r"gh[pousr]_[A-Za-z0-9]{20,}", "token do GitHub"),
    (r"AKIA[0-9A-Z]{16}", "chave de acesso AWS"),
    (r"-----BEGIN [A-Z ]*PRIVATE KEY-----", "chave privada"),
    (r"(?i)(api[_-]?key|secret|token|password|senha)\s*=\s*[\"'][^\"'\n]{12,}[\"']",
     "segredo hardcoded"),
]


@mcp.tool()
def contar_texto(texto: str) -> dict:
    """Count characters, words, lines and URLs in a text, exactly.

    Use this whenever you need a number about a text. Do not count by hand or
    estimate: your counts drift, and these are exact. Also reports whether the
    text exceeds the Blogger metadata limits.

    Args:
        texto: The text to measure.

    Returns:
        Exact counts plus a `estoura` list naming any Blogger limit the text
        breaks (empty when it fits).
    """
    urls = re.findall(r"https?://[^\s)\]}>\"']+", texto)
    palavras = re.findall(r"\S+", texto)
    nao_ascii = [c for c in texto if ord(c) > 127]

    limites = {}
    for campo, maximo in LIMITES.items():
        if campo in ("title", "description", "slug", "alt"):
            limites[campo] = maximo

    return {
        "caracteres": len(texto),
        "palavras": len(palavras),
        "linhas": texto.count("\n") + (1 if texto else 0),
        "urls": len(urls),
        "urls_lista": urls,
        "caracteres_nao_ascii": len(nao_ascii),
        "tem_emoji": any(ord(c) > 0x2190 for c in texto),
        "limites_blogs": limites,
        # O modelo nao precisa saber que title e 100: a tool compara.
        "estoura_title": len(texto) > LIMITES["title"],
    }


@mcp.tool()
def achar_placeholders(texto: str) -> dict:
    """Find template placeholders that were never filled in, like {LIMITE_TITULO}.

    Use this on any prompt, template or instruction before sending it. An
    unfilled placeholder reaches the model as literal text and it will read it
    without complaint, so nothing else in the pipeline will catch it.

    Args:
        texto: The prompt, template or instruction to inspect.

    Returns:
        The distinct placeholders found and how many times each occurs.
    """
    achados = PLACEHOLDER.findall(texto)
    contagem = {}
    for p in achados:
        contagem[p] = contagem.get(p, 0) + 1
    return {
        "total": len(achados),
        "placeholders": contagem,
        # Vazio e o caso bom. O agente deve ler isso e nao reportar.
        "limpo": not achados,
    }


@mcp.tool()
def achar_segredos(texto: str, nome_arquivo: str = "") -> dict:
    """Scan text for hardcoded API keys, tokens and passwords.

    Use this on any file before sharing, committing or publishing it. Matches
    the secret patterns that leak most often: cloud keys, tokens and
    credentials assigned inline.

    Args:
        texto: The file contents to scan.
        nome_arquivo: Just a label, used to make the report readable.

    Returns:
        One entry per finding with the line number, the kind of secret, and a
        redacted excerpt. The secret value itself is never returned.
    """
    achados = []
    for numero, linha in enumerate(texto.splitlines(), 1):
        for padrao, tipo in SEGREDOS:
            for m in re.finditer(padrao, linha):
                inicio = max(0, m.start() - 4)
                trecho = linha[inicio:m.end()]
                # Redige: o modelo precisa saber QUE achou, nunca o valor.
                # Mostrar a chave inteira aqui seria reentregar o segredo por
                # um caminho novo — e a tool roda em cima de um payload que o
                # modelo esta lendo.
                excerpt = f"{trecho[:6]}…{trecho[-4:]}" if len(trecho) > 14 else "…"
                achados.append({
                    "arquivo": nome_arquivo,
                    "linha": numero,
                    "tipo": tipo,
                    "exemplo_redigido": excerpt,
                })
    return {
        "achados": achados,
        "limpo": not achados,
        "total": len(achados),
    }


if __name__ == "__main__":
    # `stdio` e o default. `-u` desliga buffer: sem isso um print() fica no
    # buffer e o cliente ve conexao morta em vez da resposta.
    _log(f"[texto-adk] servidor MCP subindo, pid={os.getpid()}")
    mcp.run(transport="stdio")
