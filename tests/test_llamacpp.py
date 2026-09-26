"""Testa o switch use_llamacpp sem precisar de llama.cpp de verdade.

Sobe um servidor OpenAI-compatible falso (como o llama-server) e roda o
agente Researcher de verdade pelo Runner do ADK. Confere tambem que o
caminho padrao (Gemini) continua intacto.
"""

import asyncio
import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORTA = 8291
CHAMADAS = []

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_POST(self):
        corpo = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        CHAMADAS.append(corpo)
        houve_tool = any(m.get("tool_calls") for m in corpo["messages"])
        if corpo.get("tools") and not houve_tool:
            resp = {
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": "",
                            "tool_calls": [
                                {
                                    "id": "c1",
                                    "type": "function",
                                    "function": {
                                        "name": corpo["tools"][0]["function"]["name"],
                                        "arguments": '{"assunto":"gemma local"}',
                                    },
                                }
                            ],
                        }
                    }
                ]
            }
        else:
            resp = {
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": "Resumo recebido do modelo local.",
                        }
                    }
                ]
            }
        saida = json.dumps(resp).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(saida)))
        self.end_headers()
        self.wfile.write(saida)


def checar(titulo, condicao, detalhe=""):
    print(f"  {'OK   ' if condicao else 'FALHA'} {titulo}" + (f" — {detalhe}" if detalhe else ""))
    return condicao


async def main():
    servidor = ThreadingHTTPServer(("127.0.0.1", PORTA), Handler)
    threading.Thread(target=servidor.serve_forever, daemon=True).start()

    # ---- 1. caminho padrao: Gemini, sem servidor local ----
    os.environ["use_llamacpp"] = "false"
    for mod in [m for m in list(sys.modules) if m in ("common", "llamacpp")]:
        del sys.modules[mod]
    sys.path.insert(0, RAIZ)

    import common as common_gemini

    ok = True
    print("[1] caminho padrao (nuvem, com fallback de cota)")
    from google.adk.models.google_llm import Gemini

    from google.adk.models import FallbackModel
    ok &= checar("model e FallbackModel (padrao tem fallback de cota)",
                 isinstance(common_gemini.model, FallbackModel))
    ok &= checar("cadeia padrao = 2 (principal + lite)",
                 len(common_gemini.model.models) == 2,
                 str([m.model for m in common_gemini.model.models]))
    ok &= checar("USE_LLAMACPP False", common_gemini.USE_LLAMACPP is False)
    ok &= checar("MODEL_FALLBACK tem default", common_gemini.MODEL_FALLBACK == "gemini-3.5-flash-lite",
                 common_gemini.MODEL_FALLBACK)
    ok &= checar("llamacpp NAO importado", "llamacpp" not in sys.modules)

    # ---- 2. switch ligado ----
    os.environ["MODEL_FALLBACK"] = ""   # isola o caminho local
    os.environ["use_llamacpp"] = "true"
    os.environ["llamacpp_base_url"] = f"http://127.0.0.1:{PORTA}/v1"
    os.environ["llamacpp_model"] = "gemma-3-12b-it"
    os.environ["llamacpp_emulate_tools"] = "false"
    for mod in [m for m in list(sys.modules) if m in ("common", "llamacpp")]:
        del sys.modules[mod]

    import common as common_local

    print("\n[2] switch ligado (llama.cpp)")
    ok &= checar("USE_LLAMACPP True", common_local.USE_LLAMACPP is True)
    ok &= checar("modelo NAO e mais Gemini", not isinstance(common_local.model, Gemini))
    ok &= checar("llamacpp importado", "llamacpp" in sys.modules)
    ok &= checar("cadeia = nuvem + local", len(common_local.model.models) == 2,
                 str([type(m).__name__ for m in common_local.model.models]))
    ok &= checar("local e o ultimo da cadeia",
                 type(common_local.model.models[-1]).__name__ == "LlamaCppLlm")
    ok &= checar("modelo local lido do env",
                 common_local.model.models[-1].model == "gemma-3-12b-it")

    # ---- 3. agente real ponta a ponta contra o servidor falso ----
    # local-only: com MODEL em aberto a nuvem responde primeiro e o servidor
    # falso nunca e alcancado, entao o teste so passaria por acidente.
    print("\n[3] execucao real do agente contra o llama-server falso")
    os.environ["MODEL"] = ""
    for mod_nome in [m for m in list(sys.modules) if m in ("common", "llamacpp")]:
        del sys.modules[mod_nome]
    import common as common_so_local
    from google.adk.runners import Runner
    from google.adk.sessions import InMemorySessionService
    from google.genai import types as gt

    sys.path.insert(0, os.path.join(RAIZ, "researcher"))
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "researcher_agent", os.path.join(RAIZ, "researcher", "agent.py")
    )
    mod_agent = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod_agent)

    ok &= checar(
        "agente usa a instancia compartilhada",
        mod_agent.root_agent.model is common_so_local.model,
    )

    sessoes = InMemorySessionService()
    ctx = await sessoes.create_session(app_name="t", user_id="u", session_id="s")
    runner = Runner(agent=mod_agent.root_agent, app_name="t", session_service=sessoes)

    partes = []
    async for evento in runner.run_async(
        user_id="u",
        session_id=ctx.id,
        new_message=gt.Content(
            role="user", parts=[gt.Part.from_text(text="resuma o uso de gemma local")]
        ),
    ):
        if evento.is_final_response() and evento.content and evento.content.parts:
            partes = evento.content.parts

    ok &= checar("resposta final obtida", bool(partes), partes[0].text if partes else "vazia")
    ok &= checar("servidor local recebeu requests", len(CHAMADAS) >= 1, f"{len(CHAMADAS)} requests")
    if CHAMADAS:
        ok &= checar(
            "researcher nao envia tools (correto, ele nao tem nenhuma)",
            not CHAMADAS[0].get("tools"),
        )

    # ---- 3b. traducao de tools de um agente que TEM tool ----
    print("\n[3b] agente com tool: o loop completo planner->tool->resposta")
    from google.adk.agents import Agent
    from google.adk.tools.tool_context import ToolContext

    def consultar(cidade: str) -> dict:
        """Consulta o tempo de uma cidade.

        Args:
            cidade: nome da cidade
        """
        return {"cidade": cidade, "temp_c": 27}

    agente_tool = Agent(
        name="Previsao",
        model=common_so_local.model,
        instruction="Use a tool para responder sobre o tempo.",
        tools=[consultar],
    )
    CHAMADAS.clear()
    sessoes2 = InMemorySessionService()
    ctx2 = await sessoes2.create_session(app_name="t2", user_id="u", session_id="s2")
    runner2 = Runner(agent=agente_tool, app_name="t2", session_service=sessoes2)
    partes2 = []
    async for evento in runner2.run_async(
        user_id="u",
        session_id=ctx2.id,
        new_message=gt.Content(
            role="user", parts=[gt.Part.from_text(text="tempo em Recife?")]
        ),
    ):
        if evento.is_final_response() and evento.content and evento.content.parts:
            partes2 = evento.content.parts

    nomes = [t["function"]["name"] for t in (CHAMADAS[0].get("tools") or [])] if CHAMADAS else []
    ok &= checar("tool enviada no schema OpenAI", nomes == ["consultar"], str(nomes))
    if nomes:
        params = CHAMADAS[0]["tools"][0]["function"]["parameters"]
        ok &= checar("parameters tem o schema da tool", "cidade" in str(params), str(params)[:60])
    ok &= checar("2 requests (tool call + resposta)", len(CHAMADAS) == 2, f"{len(CHAMADAS)}")
    if len(CHAMADAS) > 1:
        papeis = [m["role"] for m in CHAMADAS[1]["messages"]]
        ok &= checar("resultado da tool voltou como role=tool", "tool" in papeis, str(papeis))
    ok &= checar("resposta final do agente com tool", bool(partes2), partes2[0].text if partes2 else "vazia")

    # ---- 4. servidor fora do ar: erro legivel ----
    print("\n[4] servidor fora do ar")
    os.environ["llamacpp_base_url"] = "http://127.0.0.1:9/v1"
    for mod in [m for m in list(sys.modules) if m in ("common", "llamacpp")]:
        del sys.modules[mod]
    import llamacpp as ll

    ll.BASE_URL = "http://127.0.0.1:9/v1"
    req = ll.LlmRequest()
    req.contents = [gt.Content(role="user", parts=[gt.Part.from_text(text="oi")])]
    try:
        async for _ in ll.LlamaCppLlm().generate_content_async(req):
            pass
        ok &= checar("levanta erro", False, "nao levantou")
    except RuntimeError as e:
        dica = "--jinja" in str(e)
        ok &= checar("levanta RuntimeError com dica", dica, str(e)[:70])

    # ---- 5. local-only: MODEL="" ----
    print("\n[5] local-only (MODEL= vazio, sem nuvem)")
    os.environ["MODEL"] = ""
    os.environ["MODEL_FALLBACK"] = ""
    for mod in [m for m in list(sys.modules) if m in ("common", "llamacpp")]:
        del sys.modules[mod]
    import common as c5
    ok &= checar("devolve o LlamaCppLlm cru (sem FallbackModel)",
                 type(c5.model).__name__ == "LlamaCppLlm", type(c5.model).__name__)
    ok &= checar("so 1 modelo na lista", len(c5.modelos) == 1, str(c5.modelos))

    # ---- 6. nenhum modelo: erro claro ----
    print("\n[6] nenhum modelo configurado")
    os.environ["use_llamacpp"] = "false"
    for mod in [m for m in list(sys.modules) if m in ("common", "llamacpp")]:
        del sys.modules[mod]
    try:
        import common as c6
        ok &= checar("levanta RuntimeError explicativo", False, "nao levantou")
    except RuntimeError as e:
        ok &= checar("levanta RuntimeError explicativo", "Nenhum modelo" in str(e), str(e)[:60])

    servidor.shutdown()
    print("\n" + "=" * 60)
    print("RESULTADO:", "TODOS OS CHECKS PASSARAM" if ok else "HOUVE FALHAS")
    print("=" * 60)
    return 0 if ok else 1


sys.exit(asyncio.run(main()))
