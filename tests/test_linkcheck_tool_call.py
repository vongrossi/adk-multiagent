"""Regressão: o modelo pode INVENTAR o resultado de uma tool.

Medido nesta conta com `gemini-3.1-flash-lite`: ao pedir para checar dois
links, ele respondeu "ambos responderam 200" **sem chamar a tool uma vez** —
e um dos URLs dava 404 de verdade. A tool estava registrada e funcionando
(chamada isolada devolve 404 corretamente).

Nada disso é visível num teste offline: a tool existe, o schema existe, o
agente monta. O defeito é o modelo pulando a chamada. Por isso este teste
bate na API de verdade e é o único da suite que depende de cota — fica fora
do `run_all.py` de proposito.

Ele trava a garantia que importa: se o agente afirmar um status HTTP, ele tem
que ter vindo de uma chamada real a `checar_url`.

Uso:  python3 tests/test_linkcheck_tool_call.py
Custo: ~2 requests de `gemini-3.1-flash-lite` (o balde, nao o total de 20/dia
       do modelo, entao este teste nao esvazia a cota).

Se falhar com "0 chamadas", o modelo esta inventando resultado de tool. A
primeira defesa é o prompt do `link_rewriter`; a segunda é o
`SEM_BUSCA` do root, que recusa aprovar sem prova.
"""
import asyncio
import importlib.util
import os
import sys
import warnings

warnings.filterwarnings("ignore")
RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RAIZ)

from google.adk.runners import Runner  # noqa: E402
from google.adk.sessions import InMemorySessionService  # noqa: E402
from google.genai import types  # noqa: E402

spec = importlib.util.spec_from_file_location(
    "lc_agent", os.path.join(RAIZ, "linkcheck", "agent.py")
)
lc = importlib.util.module_from_spec(spec)
sys.modules["lc_agent"] = lc
spec.loader.exec_module(lc)

CHAMADAS = []
_real = lc.checar_url


def _rastreado(url: str):
    r = _real(url)
    CHAMADAS.append((url, r["status"]))
    return r


ok = True


def chk(t, c, d=""):
    global ok
    print(f"  {'OK   ' if c else 'FALHA'} {t}" + (f" — {d}" if d else ""))
    ok = ok and c


async def main():
    # instrumenta DEPOIS do import, para nao ser sobrescrito pelo agente
    lc.checar_url = _rastreado
    lc.link_rewriter.tools = [_rastreado]

    svc = InMemorySessionService()
    await svc.create_session(app_name="t", user_id="u", session_id="s")
    runner = Runner(agent=lc.link_rewriter, app_name="t", session_service=svc)

    saida = []
    async for ev in runner.run_async(
        user_id="u",
        session_id="s",
        new_message=types.Content(
            role="user",
            parts=[
                types.Part(
                    text=(
                        "Cheque com a tool checar_url estas duas URLs e diga o "
                        "status de cada uma:\n"
                        "https://docs.python.org/3/library/asyncio.html\n"
                        "https://docs.python.org/3/asyncio-task.html"
                    )
                )
            ],
        ),
    ):
        if ev.is_final_response() and ev.content and ev.content.parts:
            for p in ev.content.parts:
                if p.text:
                    saida.append(p.text)

    texto = (saida[-1] if saida else "").lower()
    print("\n  chamadas reais a checar_url:")
    for url, st in CHAMADAS:
        print(f"    {st:<5} {url}")

    chk("a tool foi chamada", len(CHAMADAS) > 0, f"{len(CHAMADAS)} chamada(s)")
    if not CHAMADAS:
        print("\n  >>> o modelo AFIRMOU um status sem chamar a tool. "
              "Fabricao de resultado. O prompt nao esta segurando.")
        return 1

    urls_checadas = {u for u, _ in CHAMADAS}
    chk("as duas URLs foram checadas", len(urls_checadas) == 2, str(len(urls_checadas)))

    # o ground truth vem da tool, nao do texto do modelo
    real_404 = {u for u, st in CHAMADAS if st == 404}
    chk("a URL inexistente foi detectada como 404", len(real_404) == 1, str(real_404))
    if real_404:
        chk("o modelo NAO reports a URL 404 como 200",
            "200" not in texto.split(real_404.pop().split("/")[-1])[0][:40]
            if real_404 else False,
            texto[:80])

    print("\n" + "=" * 58)
    print("RESULTADO:", "TOOL CALL REAL" if ok else "FALHOU — modelo fabricando resultado")
    return 0 if ok else 1


sys.exit(asyncio.run(main()))
