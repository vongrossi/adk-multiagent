"""Testes dos agentes novos (linkcheck, seo, codereview) — sem API.

Nenhum destes chama modelo. O que se testa e a parte mecanica, que e onde o
defeito aparece sem o modelo fazer nada de errado: limites de metadados do
Blogger, padroes de segredo no codigo, e extracao de URL do texto. Rodam
offline, junto com o resto da suite.

Cada agente e importado por caminho, com nome proprio. As tres pastas irmas
(`linkcheck/`, `seo/`, `codereview/`) tem todas um `agent.py`, e o
`codereview/` ainda tem um `tools.py` que colidiria com o do `linkcheck/` se
fosse importado por `sys.path` — o `from agent import ...` pegaria sempre o
primeiro da lista.
"""
import importlib.util
import os
import sys

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RAIZ)  # `common.py` vive na raiz


def carregar(subpasta: str, arquivo: str, nome: str):
    """Importa `subpasta/arquivo` como modulo `nome`, isolado dos homonimos."""
    spec = importlib.util.spec_from_file_location(
        nome, os.path.join(RAIZ, subpasta, arquivo)
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules[nome] = mod
    spec.loader.exec_module(mod)
    return mod


ok = True


def chk(t, c, d=""):
    global ok
    print(f"  {'OK   ' if c else 'FALHA'} {t}" + (f" — {d}" if d else ""))
    ok = ok and c


# ---------------------------------------------------------------- linkcheck
print("[linkcheck] extracao de URL")
lc = carregar("linkcheck", "tools.py", "lc_tools")

md = """
Veja [a doc](https://docs.python.org/3/library/asyncio.html) e
<https://realpython.com/async-python>, mais https://blog.exemplo.com/post.
Repetida: https://blog.exemplo.com/post
Com ponto: https://exemplo.org/fim.
"""
urls = lc.extrair_urls(md)
chk("acha as 4 URLs distintas", len(urls) == 4, str(len(urls)))
chk("remove ponto final", urls[-1] == "https://exemplo.org/fim", urls[-1])
chk("nao duplica", urls.count("https://blog.exemplo.com/post") == 1,
    str(urls.count("https://blog.exemplo.com/post")))
chk("pega URL nua e de markdown", any("realpython" in u for u in urls))

print("\n[linkcheck] checagem sem rede (casos que nem saem do processo)")
for entrada, rotulo in [("nao-e-url", "recusa nao-URL"),
                        ("ftp://exemplo.com", "recusa esquema nao http"),
                        ("https://", "recusa sem host")]:
    r = lc.checar_url(entrada)
    chk(rotulo, r["status"] == "invalida" and not r["ok"], r["status"])

# ---------------------------------------------------------------- seo
print("\n[seo] limites do Blogger")
seo = carregar("seo", "agent.py", "seo_agent")
validar = seo.validar_metadados

bom = {
    "titulo": "Pub: o gerenciador de pacotes do Dart",
    "meta_description": "Como o pub resolve dependencias no Dart e Flutter.",
    "slug": "pub-gerenciador-pacotes-dart",
    "tags": ["dart", "dependencias", "flutter"],
    "alt_text": "Terminal mostrando pub get",
}
chk("metadados validos passam", validar(bom) == [], str(validar(bom)))

casos = [
    ("titulo acima de 100", {**bom, "titulo": "x" * 130}, "titulo com"),
    ("slug com acento", {**bom, "slug": "configuração do pub"}, "slug invalido"),
    ("slug com espaco", {**bom, "slug": "configuracao do pub"}, "slug invalido"),
    ("6 tags", {**bom, "tags": ["a", "b", "c", "d", "e", "f"]}, "o limite e 5"),
    ("tag generica", {**bom, "tags": ["blog"]}, "generica"),
    ("tag repetida", {**bom, "tags": ["dart", "dart"]}, "repetidas"),
    ("slug com hifen duplo", {**bom, "slug": "pub--dart"}, "slug invalido"),
    ("slug terminado em hifen", {**bom, "slug": "pub-"}, "slug invalido"),
    ("description vazia", {**bom, "meta_description": "  "}, "vazia"),
    ("alt vazio", {**bom, "alt_text": ""}, "alt_text vazio"),
    ("tags nao-lista", {**bom, "tags": "dart"}, "lista nao vazia"),
]
for nome, dados, esperado in casos:
    erros = validar(dados)
    chk(nome, any(esperado in e for e in erros), str(erros[:1]))

# ---------------------------------------------------------------- codereview
print("\n[codereview] heuristicas de segredo e crash")
cr = carregar("codereview", "agent.py", "cr_agent")

amostra = '''import requests
API_KEY = "sk-abc123def456ghi789jkl"
DB = "postgres://admin:sup3rsecret@host/db"
def f(s):
    try:
        return s[0]
    except:
        pass
'''
achados = cr._heuristicas(amostra)
tipos = {a["tipo"] for a in achados}
chk("acha API key", "segredo" in tipos, str(tipos))
chk("acha conexao com credencial", "conexao com credencial" in tipos, str(tipos))
chk("acha except generico", "engole excecao" in tipos, str(tipos))
chk("linhas conferem", all(a["linha"] > 0 for a in achados), str([a["linha"] for a in achados]))

print("\n[codereview] nao gera falso positivo em codigo limpo")
limpo = '''import json
def carregar(caminho):
    with open(caminho) as fh:
        return json.load(fh)
'''
chk("codigo limpo nao gera achado", cr._heuristicas(limpo) == [], str(cr._heuristicas(limpo)))

print("\n[codereview] tool recusa arquivo de segredo")
cr.ARQUIVO = os.path.join(RAIZ, ".env")
chk("recusa .env", "ignorados" in cr.ler_arquivo(), cr.ler_arquivo()[:50])
cr.ARQUIVO = os.path.join(RAIZ, "common.py")
# o fence usa a extensao sem o ponto: "```py", nao "```python"
servido = cr.ler_arquivo()
chk("aceita .py normal", servido.startswith("```py\n") and "MODEL_FALLBACK" in servido,
    servido[:38].replace("\n", " "))
# Caminho inexistente DENTRO do repo: o erro continua sendo "nao encontrado".
# Fora do repo, nem chega na checagem de existencia — a fronteira e anterior,
# e a mensagem e outra, porque a causa e diferente.
cr.ARQUIVO = os.path.join(RAIZ, "nao_existe_xyz.py")
chk("erro claro para caminho inexistente", "nao encontrado" in cr.ler_arquivo())
cr.ARQUIVO = "/nao/existe/xyz.py"
chk("caminho fora do repo da recusa de fronteira, nao de ausencia",
    "fora do repositorio" in cr.ler_arquivo(), cr.ler_arquivo()[:60])

print("\n" + "=" * 58)
print("RESULTADO:", "AGENTES NOVOS OK" if ok else "FALHOU")
sys.exit(0 if ok else 1)
