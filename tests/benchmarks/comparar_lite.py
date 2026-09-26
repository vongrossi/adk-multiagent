"""Compara os candidatos 'lite' no que o Blogger realmente exige.

Nao mede inteligencia geral. Mede as duas coisas que quebram o agente:
  1. disciplina do veredito: responder EXATAMENTE 'ok' ou 'retry: <lista>'
  2. tool calling: chamar a tool com o schema certo

CUSTA COTA DE VERDADE: 3 chamadas por modelo, na conta do .env. Foi esse
script que escolheu `gemini-3.1-flash-lite` como padrao. Ver README.

Uso:  python3 tests/benchmarks/comparar_lite.py
"""
import json
import os
import urllib.error
import urllib.request

from dotenv import load_dotenv

RAIZ = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
load_dotenv(f"{RAIZ}/.env")
KEY = os.environ["GOOGLE_API_KEY"]
URL = "https://generativelanguage.googleapis.com/v1beta/models/{m}:generateContent?key=" + KEY

CANDIDATOS = [
    "gemini-3.6-flash",
    "gemini-3.5-flash-lite",
    "gemini-3.1-flash-lite",
    "gemini-flash-lite-latest",
    "gemma-4-26b-a4b-it",
    "gemma-4-31b-it",
]

FERRAMENTA = {
    "name": "buscar_fontes",
    "description": "Busca fontes sobre um tema.",
    "parameters": {
        "type": "object",
        "properties": {"tema": {"type": "string", "description": "assunto a buscar"}},
        "required": ["tema"],
    },
}

TAREFA_VEREDITO = """Responda com EXATAMENTE uma destas duas formas e nada mais:
- `ok` (se o outline abaixo esta completo)
- `retry: <lista de problemas separados por virgula>` (se falta algo)

Regras do outline: precisa de titulo, intro, 4-6 secoes e conclusao.

<outline>
# Python asyncio na pratica
## Introducao
texto
## 1. Corrotinas
texto
## 2. Tasks
texto
## 3. WaitFor
texto
## 4. TaskGroup
texto
## 5. Excecoes
texto
## Conclusao
texto
</outline>"""

TAREFA_VEREDITO_RUIM = """Responda com EXATAMENTE uma destas duas formas e nada mais:
- `ok` (se o outline abaixo esta completo)
- `retry: <lista de problemas separados por virgula>` (se falta algo)

Regras do outline: precisa de titulo, intro, 4-6 secoes e conclusao.

<outline>
# Python asyncio
texto solto sem estrutura
</outline>"""

TAREFA_TOOL = """Use a ferramenta buscar_fontes para achar fontes sobre asyncio. Depois me diga o que encontrou."""


def chamar(modelo, prompt, tools=None, max_out=200):
    corpo = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0, "maxOutputTokens": max_out},
    }
    if tools:
        corpo["tools"] = [{"functionDeclarations": [FERRAMENTA]}]
    req = urllib.request.Request(
        URL.format(m=modelo), data=json.dumps(corpo).encode(),
        headers={"Content-Type": "application/json"},
    )
    import time
    d = None
    for tentativa in range(4):
        try:
            with urllib.request.urlopen(req, timeout=90) as r:
                d = json.load(r)
            break
        except urllib.error.HTTPError as e:
            corpo_erro = e.read().decode()
            if e.code == 503 and tentativa < 3:
                time.sleep(4 * (tentativa + 1))
                continue
            try:
                msg = json.loads(corpo_erro)["error"]["message"].split("\n")[0][:48]
            except Exception:
                msg = corpo_erro[:48]
            return {"erro": f"{e.code} {msg}"}
    if d is None:
        return {"erro": "sem resposta"}

    c = d["candidates"][0]["content"]
    partes = c.get("parts", [])
    texto = "".join(p.get("text", "") for p in partes)
    calls = [p.get("functionCall") for p in partes if p.get("functionCall")]
    return {"texto": texto.strip(), "calls": calls,
            "tokens": d.get("usageMetadata", {}).get("totalTokenCount", 0)}


def avaliar_veredito(r, esperado):
    if "erro" in r:
        return "erro", r["erro"]
    t = r["texto"].strip()
    if t == esperado:
        return "ok", "exato"
    primeiro = t.split("\n")[0].strip()
    if primeiro == esperado:
        return "parcial", f"exato so na 1a linha (len={len(t)})"
    if esperado == "ok" and t.lower().startswith("ok"):
        return "parcial", f"'ok' + texto extra (len={len(t)})"
    if esperado != "ok" and t.lower().startswith("retry"):
        return "parcial", f"formato parecido (len={len(t)})"
    return "ruim", f"{t[:46]!r}"


print(f"{'modelo':26} {'veredito OK':22} {'veredito retry':20} {'tool call'}")
print("-" * 96)

resumo = []
for m in CANDIDATOS:
    r1 = chamar(m, TAREFA_VEREDITO, max_out=60)
    s1, d1 = avaliar_veredito(r1, "ok")
    r2 = chamar(m, TAREFA_VEREDITO_RUIM, max_out=60)
    s2, d2 = avaliar_veredito(r2, "retry")
    r3 = chamar(m, TAREFA_TOOL, tools=True, max_out=150)

    if "erro" in r3:
        s3, d3 = "erro", r3["erro"]
    elif r3["calls"]:
        nome = r3["calls"][0].get("name")
        args = r3["calls"][0].get("args", {})
        s3 = "ok" if nome == "buscar_fontes" and args.get("tema") else "ruim"
        d3 = f"{nome}({args})"
    else:
        s3, d3 = "ruim", f"sem tool call: {r3['texto'][:30]!r}"

    pontos = sum(x == "ok" for x in (s1, s2, s3))
    resumo.append((m, pontos, s1, s2, s3))
    print(f"{m:26} {s1+' ('+d1+')':22.22} {s2+' ('+d2+')':20.20} {s3} {d3[:24]}")

print("\n" + "=" * 96)
print("Ranking (3 verificacoes por modelo):")
for m, p, s1, s2, s3 in sorted(resumo, key=lambda x: -x[1]):
    print(f"  {p}/3  {m}")
