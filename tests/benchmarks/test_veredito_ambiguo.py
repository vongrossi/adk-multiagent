"""Investiga o 'retry: excesso' dos validadores.

Hipotese: o outline de teste tem 5 secoes numeradas + Introducao + Conclusao.
Se o modelo conta intro e conclusao como secao, sao 7 e viola a regra "4-6" —
logo responde retry num outline que era valido. Gastando as 3 iteracoes do loop
e a cota da conta.

Testa a mesma tarefa com a regra desambiguada.

CUSTA COTA DE VERDADE: 2 chamadas por modelo. Conclusao: desambiguar o prompt
NAO resolveu — o 3.5 e o flash-lite-latest continuaram dando retry falso, so
que com outro motivo. O problema nao era a ambiguidade da regra.

Uso:  python3 tests/benchmarks/test_veredito_ambiguo.py
"""
import json
import os
import time
import urllib.error
import urllib.request

from dotenv import load_dotenv

RAIZ = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
load_dotenv(f"{RAIZ}/.env")
KEY = os.environ["GOOGLE_API_KEY"]
URL = "https://generativelanguage.googleapis.com/v1beta/models/{m}:generateContent?key=" + KEY

OUTLINE = """# Python asyncio na pratica
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
texto"""

AMBIGUO = """Responda com EXATAMENTE uma destas duas formas e nada mais:
- `ok` (se o outline abaixo esta completo)
- `retry: <lista de problemas separados por virgula>` (se falta algo)

Regras do outline: precisa de titulo, intro, 4-6 secoes e conclusao.

<outline>
""" + OUTLINE + """
</outline>"""

DESAMBIGUADO = """Responda com EXATAMENTE uma destas duas formas e nada mais:
- `ok` (se o outline abaixo esta completo)
- `retry: <lista de problemas separados por virgula>` (se falta algo)

Regras do outline:
- tem titulo (`# `)
- tem introducao
- tem 4-6 secoes de conteudo, numeradas (`## 1. ` ate `## 6. `)
- tem conclusao
A introducao e a conclusao NAO contam como secao de conteudo.

<outline>
""" + OUTLINE + """
</outline>"""


def chamar(modelo, prompt, max_out=80):
    corpo = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0, "maxOutputTokens": max_out},
    }
    req = urllib.request.Request(
        URL.format(m=modelo), data=json.dumps(corpo).encode(),
        headers={"Content-Type": "application/json"},
    )
    for tentativa in range(4):
        try:
            with urllib.request.urlopen(req, timeout=90) as r:
                d = json.load(r)
            txt = "".join(
                p.get("text", "") for p in d["candidates"][0]["content"].get("parts", [])
            ).strip()
            return txt
        except urllib.error.HTTPError as e:
            raw = e.read().decode()
            if e.code == 503 and tentativa < 3:
                time.sleep(4 * (tentativa + 1))
                continue
            return f"[ERRO {e.code}] {raw[:44]}"
    return "[sem resposta]"


MODELOS = ["gemini-3.1-flash-lite", "gemini-3.5-flash-lite", "gemini-flash-lite-latest"]

print("Regra ambigua ('4-6 secoes') vs desambiguada (intro/conclusao nao contam)\n")
for m in MODELOS:
    a = chamar(m, AMBIGUO)
    b = chamar(m, DESAMBIGUADO)
    va = "OK" if a.strip() == "ok" else ("FALSO RETRY" if a.lower().startswith("retry") else "?")
    vb = "OK" if b.strip() == "ok" else ("FALSO RETRY" if b.lower().startswith("retry") else "?")
    print(f"{m}")
    print(f"   ambigua    -> {va:12} {a[:58]!r}")
    print(f"   desambig.  -> {vb:12} {b[:58]!r}")
    print()
