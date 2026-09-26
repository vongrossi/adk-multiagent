"""
MCP — sobe o servidor de verdade e conversa JSON-RPC com ele.

Por que este teste fala o protocolo em vez de importar as funcoes:

Importar `contar_texto` direto passaria mesmo que o servidor estivesse
quebrado — e o servidor pode estar quebrado de tres jeitos que nao aparecem
no import:

  1.registro da tool no MCPServer (nome errado, decorator faltando)
  2.transport stdio (print no stdout corrompe o JSON-RPC)
  3.spawn (caminho do script, `sys.executable`, `-u` no buffer)

Todos os tres so aparecem quando ha um cliente do outro lado do pipe. Por
isso o teste abre um `MCPSession` de verdade.

E o que garante que o `doctor()` do agente funciona, que e a mesma pergunta
que o `try/except` do linkcheck deixava sem resposta.
"""

import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

falhas = []


def chk(desc, cond, detalhe=""):
    if cond:
        print(f"  OK    {desc}")
    else:
        falhas.append(desc)
        print(f"  FALHA {desc}  {detalhe}")
    return cond


def main():
    from google.adk.tools.mcp_tool import StdioConnectionParams
    from google.adk.tools.mcp_tool.mcp_toolset import McpToolset
    from mcp import StdioServerParameters

    raiz = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    server = os.path.join(raiz, "mcp_server", "server.py")
    chk("mcp_server/server.py existe", os.path.exists(server), server)

    # `sys.executable`, nunca "python3": e o mesmo interpretador deste teste.
    spawn = StdioServerParameters(
        command=sys.executable,
        args=["-u", server],
        cwd=raiz,
    )
    conn = StdioConnectionParams(server_params=spawn, timeout=20.0)
    toolset = McpToolset(connection_params=conn)

    class _Ctx:
        """Stub de ToolContext. As tools deste servidor sao puras — nao leem
        state nem escrevem — entao um objeto vazio basta. Se alguma tool
        precisar de state de verdade, este stub e o primeiro lugar a falhar,
        e o erro aparece aqui em vez de em producao."""

        def __init__(self):
            self.state = {}
            self.actions = None

        def get_state(self, key, default=None):
            return self.state.get(key, default)

    ctx = _Ctx()

    def extrai(r):
        """O ADK devolve um content de tool como lista de blocos de texto com
        JSON dentro, nao o dict. Desembrulhar em um lugar so, e nao em cada
        assert, evita que o teste vire um festival de indice [0][0]."""
        if isinstance(r, dict) and "content" in r:
            r = r["content"]
        if isinstance(r, str):
            return json.loads(r)
        if isinstance(r, list) and r:
            primeiro = r[0]
            texto = primeiro.get("text") if isinstance(primeiro, dict) else str(primeiro)
            try:
                return json.loads(texto)
            except (TypeError, ValueError):
                return texto
        return r

    async def run():
        print("\n" + "=" * 62)
        print("CONEXAO: o processo sobe e as tools se registram?")
        print("=" * 62)
        try:
            tools = await toolset.get_tools()
        except Exception as e:
            chk("servidor subiu e respondeu", False, f"{type(e).__name__}: {e}")
            return

        chk("servidor subiu e respondeu", True)
        # get_tools() devolve lista em algumas versoes e dict em outras.
        # Normalizar aqui evita que o teste quebre por mudanca do ADK que
        # nao tem nada a ver com o que ele esta testando.
        lista = list(tools.values()) if isinstance(tools, dict) else list(tools)
        nomes = sorted(t.name for t in lista)
        print(f"        tools: {nomes}")
        for esperado in ("contar_texto", "achar_placeholders", "achar_segredos"):
            chk(f"tool '{esperado}' registrada", esperado in nomes, nomes)

        # A docstring e o que o modelo le para decidir quando chamar. Se ela
        # sumiu ou virou outra coisa, o agente fica sem criterio de uso — e o
        # unico sintoma seria "o modelo nao chama a tool".
        print("\n" + "=" * 62)
        print("CONTRATO: o que o modelo ve da tool")
        print("=" * 62)
        por_nome = {t.name: t for t in lista}
        t = por_nome["contar_texto"]

        def schema_de(tool):
            """O schema da tool mora em lugares diferentes conforme a versao
            do ADK. O que o modelo realmente ve e o declaration, entao e
            esse que a gente checa."""
            fn = getattr(tool, "_get_declaration", None)
            if callable(fn):
                d = fn()
                if d is not None:
                    dump = d.model_dump(exclude_none=True)
                    # O ADK 2.9 achata o JSON Schema em
                    # `parameters_json_schema`. Versoes outras usam
                    # `parameters`, e o raw do MCP usa `properties` direto.
                    for chave in ("parameters_json_schema", "parameters", "input_schema"):
                        if dump.get(chave):
                            return dump[chave]
                    return dump
            raw = getattr(tool, "raw_mcp_tool", None)
            schema = getattr(raw, "input_schema", None) if raw else None
            if isinstance(schema, dict):
                return schema
            if schema is not None and hasattr(schema, "model_dump"):
                return schema.model_dump(exclude_none=True)
            return {}

        schema = schema_de(t)
        chk("tem descricao", bool(t.description), "(vazia)")
        chk("descricao em ingles", "count" in (t.description or "").lower(),
            (t.description or "")[:70])
        chk("descreve quando usar", "use this" in (t.description or "").lower(),
            (t.description or "")[:70])
        # FunctionDeclaration tem {name, description, parameters}; o JSON
        # Schema de verdade esta em parameters.properties. O raw do MCP
        # entrega properties direto. Aciona os dois.
        props = (schema.get("parameters") or schema).get("properties") or {}
        required = (schema.get("parameters") or schema).get("required") or []
        chk("parametro 'texto' existe", "texto" in props, list(props))
        chk("parametro 'texto' e string", props.get("texto", {}).get("type") == "string", props.get("texto"))
        chk("parametro 'texto' e obrigatorio", "texto" in required, required)

        print("\n" + "=" * 62)
        print("EXECUCAO: as tools devolvem numero exato?")
        print("=" * 62)
        d = extrai(await t.run_async(args={"texto": "abc " * 10 + "https://exemplo.com/x"}, tool_context=ctx))
        chk("contar_texto devolve dict", isinstance(d, dict), type(d))
        if isinstance(d, dict):
            chk("61 caracteres (40 de texto + 21 da URL)", d.get("caracteres") == 61, d.get("caracteres"))
            chk("11 palavras (10 + a URL)", d.get("palavras") == 11, d.get("palavras"))
            chk("1 URL contada", d.get("urls") == 1, d.get("urls"))

        ph = por_nome["achar_placeholders"]
        d = extrai(await ph.run_async(args={"texto": "O titulo tem ate {LIMITE_TITULO} chars e {LIMITE_TITULO} de novo"}, tool_context=ctx))
        chk("achar_placeholders acha o placeholder", isinstance(d, dict) and d.get("total") == 2, d)
        chk("conta repeticao", isinstance(d, dict) and
            d.get("placeholders", {}).get("{LIMITE_TITULO}") == 2, d)

        d = extrai(await ph.run_async(args={"texto": "texto limpo sem nada"}, tool_context=ctx))
        chk("texto limpo devolve limpo=True", isinstance(d, dict) and d.get("limpo") is True, d)

        sg = por_nome["achar_segredos"]
        d = extrai(await sg.run_async(args={
            "texto": 'import os\nGOOGLE_API_KEY = "AIzaSyFAKEFAKEFAKEFAKEFAKEFAKE12345"\n',
            "nome_arquivo": "x.py",
        }, tool_context=ctx))
        chk("achar_segredos acha a chave", isinstance(d, dict) and d.get("total", 0) >= 1, d)
        if isinstance(d, dict) and d.get("achados"):
            a = d["achados"][0]
            chk("acha da linha 2", a.get("linha") == 2, a)
            chk("exemplo redigido NAO contem a chave completa",
                "FAKEFAKE" not in str(a.get("exemplo_redigido", "")),
                a.get("exemplo_redigido"))
            chk("nao devolve o valor da chave em nenhum campo",
                "AIzaSyFAKE" not in str(d), "(o segredo vazou no retorno)")

        d = extrai(await sg.run_async(args={"texto": "print('oi')", "nome_arquivo": "y.py"}, tool_context=ctx))
        chk("arquivo limpo devolve limpo=True", isinstance(d, dict) and d.get("limpo") is True, d)

        print("\n" + "=" * 62)
        print("O TRAP: connection_params como dict falha?")
        print("=" * 62)
        # Este e o bug real do linkcheck/tools.py. Reproduzir aqui e o que
        # impede que o mesmo erro entre de novo.
        # Descoberto na pratica: o dict NAO falha na construcao. O construtor
        # aceita qualquer coisa, e a validacao acontece so quando o toolset
        # tenta abrir a conexao — ou seja, durante o run, la no meio da
        # conversa, nao no import. Por isso o try/except em volta da
        # construcao em linkcheck/tools.py nunca pega: ele protege a linha
        # errada.
        aceitou_na_construcao = False
        try:
            ruim = McpToolset(connection_params={
                "command": sys.executable, "args": ["-u", server], "cwd": raiz,
            })
            aceitou_na_construcao = True
        except Exception:
            chk("dict nao passa na construcao", False, "falhou cedo (seria melhor)")

        chk("dict PASSA na construcao (por isso nao da erro no import)",
            aceitou_na_construcao)

        if aceitou_na_construcao:
            try:
                await ruim.get_tools()
                chk("dict falha na CONEXAO", False, "conectou — o bug passaria despercebido")
            except Exception as e:
                chk("dict falha na CONEXAO", True)
                print(f"        {type(e).__name__}: {str(e)[:88]}")
                print("        (linkcheck/tools.py passa um dict aqui: o agente sobe,")
                print("         roda, e simplesmente nunca tem busca. Sem aviso.)")

    asyncio.run(run())

    print("\n" + "=" * 62)
    print("RESUMO")
    print("=" * 62)
    if falhas:
        print(f"{len(falhas)} FALHA(S):")
        for f in falhas:
            print(f"  - {f}")
        return 1
    print("servidor MCP respondeu de ponta a ponta")
    return 0


if __name__ == "__main__":
    sys.exit(main())
