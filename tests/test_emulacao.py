"""Verifica llamacpp_emulate_tools=true (GemmaFunctionCallingMixin).

Gemma 3 nao tem function calling nativo. Com emulacao, o ADK deve injetar
as tools no system instruction em vez de mandar o campo `tools`.
"""
import asyncio, json, os, sys, threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
PORTA, CHAMADAS = 8294, []
RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def do_POST(self):
        c = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        CHAMADAS.append(c)
        sysmsg = " ".join(str(m.get("content") or "") for m in c["messages"] if m.get("role")=="system")
        # emulacao: as tools chegam como TEXTO no system instruction
        tem_texto_tool = "consultar" in sysmsg or "cidade" in sysmsg
        c["_texto_tool"] = tem_texto_tool
        # o modelo "chama" a tool escrevendo no texto, como o mixin faz o parse
        # 1a chamada: o modelo pede a tool. Depois disso: resposta final.
        if len(CHAMADAS) == 1:
            # formato que o mixin do ADK faz parse: {"name":..,"parameters":{..}}
            txt = 'Vou consultar. ```json\n{"name": "consultar", "parameters": {"cidade": "Recife"}}\n```'
        else:
            txt = "Em Recife está 27 graus."
        r = {"choices":[{"message":{"role":"assistant","content":txt}}]}
        s = json.dumps(r).encode()
        self.send_response(200); self.send_header("Content-Type","application/json")
        self.send_header("Content-Length",str(len(s))); self.end_headers(); self.wfile.write(s)

async def main():
    srv = ThreadingHTTPServer(("127.0.0.1", PORTA), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    os.environ.update({"MODEL_FALLBACK":"", "use_llamacpp":"true","llamacpp_base_url":f"http://127.0.0.1:{PORTA}/v1",
                       "llamacpp_model":"gemma-3-12b-it","llamacpp_emulate_tools":"true"})
    sys.path.insert(0, RAIZ)
    import llamacpp as ll
    print("EMULATE_TOOLS =", ll.EMULATE_TOOLS)
    print("MRO           =", " -> ".join(c.__name__ for c in type(ll.LlamaCppLlm()).__mro__[:4]))

    from google.adk.agents import Agent
    from google.adk.runners import Runner
    from google.adk.sessions import InMemorySessionService
    from google.genai import types as gt

    def consultar(cidade: str) -> dict:
        """Consulta o tempo.

        Args:
            cidade: nome da cidade
        """
        return {"cidade": cidade, "temp_c": 27}

    ag = Agent(name="Prev", model=ll.LlamaCppLlm(), instruction="Responda o tempo.", tools=[consultar])
    se = InMemorySessionService()
    ctx = await se.create_session(app_name="e", user_id="u", session_id="s")
    run = Runner(agent=ag, app_name="e", session_service=se)
    partes=[]
    async for ev in run.run_async(user_id="u", session_id=ctx.id,
        new_message=gt.Content(role="user", parts=[gt.Part.from_text(text="tempo em Recife")]),
    ):
        if ev.is_final_response() and ev.content and ev.content.parts: partes=ev.content.parts

    ok=True
    def chk(t,c,d=""):
        nonlocal ok; print(f"  {'OK   ' if c else 'FALHA'} {t}"+(f" — {d}" if d else "")); ok=ok and c
    chk("emulacao ligada", ll.EMULATE_TOOLS is True)
    chk("mixin do Gemma no MRO", any("Gemma" in c.__name__ for c in type(ll.LlamaCppLlm()).__mro__))
    chk("nenhum campo tools enviado", not any(c.get("tools") for c in CHAMADAS))
    chk("tools injetadas como texto", any(c.get("_texto_tool") for c in CHAMADAS))
    chk("2 chamadas (parse da tool + resposta)", len(CHAMADAS)==2, f"{len(CHAMADAS)}")
    for i,c in enumerate(CHAMADAS,1):
        print(f"     call {i}: roles={[m.get('role') for m in c['messages']]}")
        print(f"        ultima={str(c['messages'][-1].get('content'))[:90]!r}")
    if partes: print("     resposta final:", (partes[0].text or "")[:80])
    sys.argv=[]
    srv.shutdown()
    print("\n"+"="*54); print("RESULTADO:", "EMULACAO OK" if ok else "FALHOU"); print("="*54)
    return 0 if ok else 1
sys.exit(asyncio.run(main()))
