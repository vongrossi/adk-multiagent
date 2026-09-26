"""Prova que a cadeia de fallback do common.py funciona.

O caso real que o usuario-reportou: o modelo principal estoura a cota (429) e
a execucao morre. Aqui um modelo falso sempre levanta 429, e conferimos que o
agente mesmo assim completa usando o modelo seguinte.
"""

import asyncio
import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORTA = 8295
CHAMADAS = []
TENTATIVAS = []
RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

import httpx

from google.adk.models.base_llm import BaseLlm  # noqa: E402
from google.adk.models.llm_request import LlmRequest  # noqa: E402
from google.adk.models.llm_response import LlmResponse  # noqa: E402
from google.genai import types  # noqa: E402


class Exausto429(BaseLlm):
    """Modelo que sempre falha com 429, como a cota estourada."""

    model: str = "modelo-exausto"

    @classmethod
    def supported_models(cls):
        return [r"exhausto-.*"]

    async def generate_content_async(self, llm_request: LlmRequest, stream: bool = False):
        TENTATIVAS.append(1)
        raise httpx.HTTPStatusError(
            "429 RESOURCE_EXHAUSTED - quota do dia esgotada",
            request=httpx.Request("POST", "https://x"),
            response=httpx.Response(429, json={"error": {"code": 429}}),
        )
        yield  # pragma: no cover


class Handler(BaseHTTPRequestHandler):
    """Finge o llama-server local: sempre responde texto."""

    def log_message(self, *a):
        pass

    def do_POST(self):
        corpo = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        CHAMADAS.append(corpo)
        resp = {
            "choices": [
                {"message": {"role": "assistant", "content": "resposta do modelo local"}}
            ]
        }
        saida = json.dumps(resp).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(saida)))
        self.end_headers()
        self.wfile.write(saida)


async def main():
    servidor = ThreadingHTTPServer(("127.0.0.1", PORTA), Handler)
    threading.Thread(target=servidor.serve_forever, daemon=True).start()

    ok = True

    def chk(t, c, d=""):
        nonlocal ok
        print(f"  {'OK   ' if c else 'FALHA'} {t}" + (f" — {d}" if d else ""))
        ok = ok and c

    # ---------- 1. cadeia montada ----------
    os.environ.update(
        {
            "MODEL": "gemini-3.6-flash",
            "MODEL_FALLBACK": "gemini-3.5-flash-lite",
            "use_llamacpp": "true",
            "llamacpp_base_url": f"http://127.0.0.1:{PORTA}/v1",
            "llamacpp_model": "gemma-3-4b-it",
            "llamacpp_emulate_tools": "false",
        }
    )
    for m in [k for k in list(sys.modules) if k in ("common", "llamacpp")]:
        del sys.modules[m]
    sys.path.insert(0, RAIZ)
    import common

    from google.adk.models import FallbackModel

    print("[1] cadeia montada")
    chk("model e FallbackModel", isinstance(common.model, FallbackModel))
    chk("3 modelos na cadeia", len(common.model.models) == 3, str([type(m).__name__ for m in common.model.models]))
    nomes = [m.model for m in common.model.models]
    chk("ordem: principal, fallback, local", nomes == ["gemini-3.6-flash", "gemini-3.5-flash-lite", "gemma-3-4b-it"], str(nomes))
    codes_principal = common.model.models[0].retry_options.http_status_codes
    chk("429 NAO esta no retry do principal", 429 not in codes_principal, str(sorted(codes_principal)))
    chk("5xx continua no retry do principal", 503 in codes_principal, str(sorted(codes_principal)))
    chk("attempts=8 preservado", common.model.models[0].retry_options.attempts == 8, str(common.model.models[0].retry_options.attempts))

    # ---------- 2. 429 no primeiro modelo cai no seguinte ----------
    print("\n[2] principal estoura a cota: o agente completa pelo fallback")
    comum_orig = common.model
    # os DOIS modelos da nuvem estouram: so o local pode salvar a execucao
    comum_orig.models[0] = Exausto429()
    comum_orig.models[1] = Exausto429()

    from google.adk.agents import Agent
    from google.adk.runners import Runner
    from google.adk.sessions import InMemorySessionService

    ag = Agent(name="A", model=comum_orig, instruction="Responda curto.")
    se = InMemorySessionService()
    ctx = await se.create_session(app_name="c", user_id="u", session_id="s")
    run = Runner(agent=ag, app_name="c", session_service=se)
    partes = []
    async for ev in run.run_async(
        user_id="u",
        session_id=ctx.id,
        new_message=types.Content(role="user", parts=[types.Part.from_text(text="oi")]),
    ):
        if ev.is_final_response() and ev.content and ev.content.parts:
            partes = ev.content.parts

    chk("os 2 modelos de nuvem foram tentados", len(TENTATIVAS) == 2, f"{len(TENTATIVAS)}x (nenhum retentado)")
    chk("agente completou apesar do 429", bool(partes), (partes[0].text or "")[:40] if partes else "vazia")
    chk("caiu no modelo local (llama.cpp)", len(CHAMADAS) >= 1, f"{len(CHAMADAS)} requests ao local")
    if CHAMADAS:
        chk("requisição foi para o modelo local", CHAMADAS[0].get("model") == "gemma-3-4b-it", str(CHAMADAS[0].get("model")))

    # ---------- 3. modelo unico: sem camada FallbackModel ----------
    print("\n[3] sem MODEL_FALLBACK e sem local: volta ao Gemini cru")
    os.environ["MODEL_FALLBACK"] = ""
    os.environ["use_llamacpp"] = "false"
    for m in [k for k in list(sys.modules) if k in ("common", "llamacpp")]:
        del sys.modules[m]
    import common as c2

    from google.adk.models.google_llm import Gemini

    chk("model e Gemini cru", isinstance(c2.model, Gemini))
    chk("429 volta a ser retentado", 429 in c2.model.retry_options.http_status_codes)

    # ---------- 4. so fallback, sem local ----------
    print("\n[4] cadeia de 2 (principal + fallback lite), sem local")
    os.environ["MODEL_FALLBACK"] = "gemini-3.5-flash-lite"
    os.environ["use_llamacpp"] = "false"
    for m in [k for k in list(sys.modules) if k in ("common", "llamacpp")]:
        del sys.modules[m]
    import common as c3

    chk("cadeia de 2", len(c3.model.models) == 2, str([x.model for x in c3.model.models]))
    chk("429 nao retentado no principal", 429 not in c3.model.models[0].retry_options.http_status_codes)
    # o ultimo da cadeia DEVE retentar 429: nao ha para onde cair, e insistir
    # e a unica chance de o reset de cota chegar no meio.
    chk("ultimo da cadeia retenta 429 (nada abaixo dele)", 429 in c3.model.models[-1].retry_options.http_status_codes)

    servidor.shutdown()
    print("\n" + "=" * 62)
    print("RESULTADO:", "CADEIA OK" if ok else "HOUVE FALHAS")
    print("=" * 62)
    return 0 if ok else 1


sys.exit(asyncio.run(main()))
