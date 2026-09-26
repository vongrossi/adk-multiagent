"""Blogger completo contra um llama-server falso.

O ponto critico nao e a tool: e o veredito "ok"/"retry" dos validadores, que
e o que dispara o escalate. Se a traducao quebrar o texto, o loop nao para.
"""
import asyncio, json, os, sys, threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORTA = 8293
CHAMADAS = []
CHAMADAS_ROOT = []
CHAMADAS_POST = []
CHAMADAS_OUTLINE = []
RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

OUTLINE = "## Outline\n\n- Titulo\n- Conclusao"  # incompleto de proposito
POST = "## Artigo\n\nconteudo gerado localmente."


def _fase(texto):
    """Identifica qual agente esta chamando, pela frase distintiva da instrucao."""
    t = texto.lower()
    if "check the outline below" in t:   return "check_outline"
    if "check the post below" in t:      return "check_post"
    if "produce a clear markdown outline" in t: return "outline"
    if "write a complete markdown article" in t: return "post"
    if "call the planner tool" in t:     return "root"
    return "root"


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a): pass

    def do_POST(self):
        corpo = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        CHAMADAS.append(corpo)
        sys_txt = " ".join(
            str(m.get("content") or "") for m in corpo["messages"] if m.get("role") == "system"
        ) + " " + " ".join(str(m.get("content") or "") for m in corpo["messages"])
        fase = _fase(sys_txt)
        corpo["_fase"] = fase
        import os as _o
        if _o.environ.get("DEBUG_FASES"):
            print(f"   [call {len(CHAMADAS)}] fase={fase} tools={len(corpo.get('tools') or [])} roles={[m.get('role') for m in corpo['messages']]}")
            print(f"      texto={sys_txt[:150]!r}")

        ultima = (corpo["messages"] or [{}])[-1]
        # a tool so e chamada de novo quando a ultima mensagem for o RESULTADO
        # de uma tool (o loop planner terminou, agraca.writer) ou quando ainda
        # nao rodou nada. `any(tool_calls)` erra: o historico sempre traz a
        # chamada da rodada anterior, entao nunca seria True na 2a vez.
        if fase == "root" and corpo.get("tools") and len(CHAMADAS_ROOT) < 2 and (
            ultima.get("role") == "tool" or len(CHAMADAS_ROOT) == 0
        ):
            nomes = [t["function"]["name"] for t in corpo["tools"]]
            # o root chama o planner na 1a vez, o writer na 2a
            alvo = nomes[min(len(CHAMADAS_ROOT), len(nomes) - 1)]
            CHAMADAS_ROOT.append(alvo)
            CHAMADAS[-1]["_alvo"] = alvo
            resp = {"choices":[{"message":{"role":"assistant","content":"",
                "tool_calls":[{"id":"c1","type":"function","function":{
                    "name":alvo,"arguments":"{\"request\":\"topico de teste\"}"}}]}}]}
        elif fase == "outline":
            resp = {"choices":[{"message":{"role":"assistant","content":OUTLINE}}]}
        elif fase == "check_outline":
            if CHAMADAS_OUTLINE.count(1) < 1:
                CHAMADAS_OUTLINE.append(1)
                resp = {"choices":[{"message":{"role":"assistant","content":"retry: faltam as 4-6 secoes"}}]}
            else:
                resp = {"choices":[{"message":{"role":"assistant","content":"ok"}}]}
        elif fase == "post":
            resp = {"choices":[{"message":{"role":"assistant","content":POST}}]}
        elif fase == "check_post":
            if CHAMADAS_POST.count(1) < 1:
                CHAMADAS_POST.append(1)
                resp = {"choices":[{"message":{"role":"assistant","content":"retry: falta conclusao"}}]}
            else:
                resp = {"choices":[{"message":"x"}]}
                resp = {"choices":[{"message":{"role":"assistant","content":"ok"}}]}
        else:
            resp = {"choices":[{"message":{"role":"assistant","content":"Artigo pronto."}}]}

        saida = json.dumps(resp).encode()
        self.send_response(200); self.send_header("Content-Type","application/json")
        self.send_header("Content-Length",str(len(saida))); self.end_headers()
        self.wfile.write(saida)


async def main():
    servidor = ThreadingHTTPServer(("127.0.0.1", PORTA), Handler)
    threading.Thread(target=servidor.serve_forever, daemon=True).start()
    os.environ.update({
        "MODEL": "",            # local-only: sem cloud na frente da fila
        "MODEL_FALLBACK": "",
        "use_llamacpp": "true",
        "llamacpp_base_url": f"http://127.0.0.1:{PORTA}/v1",
        "llamacpp_model": "gemma-3-12b-it",
        "llamacpp_emulate_tools": "false",
    })
    sys.path.insert(0, RAIZ); sys.path.insert(0, os.path.join(RAIZ,"blogger"))
    import importlib.util
    spec = importlib.util.spec_from_file_location("blogger_agent", os.path.join(RAIZ,"blogger","agent.py"))
    mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)

    from google.adk.runners import Runner
    from google.adk.sessions import InMemorySessionService
    from google.genai import types as gt
    sessoes = InMemorySessionService()
    ctx = await sessoes.create_session(app_name="b", user_id="u", session_id="s")
    runner = Runner(agent=mod.root_agent, app_name="b", session_service=sessoes)

    partes = []
    async for ev in runner.run_async(user_id="u", session_id=ctx.id,
        new_message=gt.Content(role="user", parts=[gt.Part.from_text(text="escreva sobre X")]),
    ):
        if ev.is_final_response() and ev.content and ev.content.parts:
            partes = ev.content.parts

    ok = True
    def chk(t, c, d=""):
        nonlocal ok
        print(f"  {'OK   ' if c else 'FALHA'} {t}" + (f" — {d}" if d else ""))
        ok = ok and c

    print("[Blogger com modelo local]")
    chk("resposta final obtida", bool(partes), (partes[0].text or "")[:50])
    fases = [c.get("_fase") for c in CHAMADAS]
    chk("fluxo planner->writer executado", "outline" in fases and "post" in fases, " -> ".join(fases))
    alvos = [c.get("_alvo") for c in CHAMADAS if c.get("_alvo")]
    chk("root chamou as 2 tools", len(alvos) == 2, str(alvos))

    # o state precisa ter os 4 artefatos
    s = await sessoes.get_session(app_name="b", user_id="u", session_id=ctx.id)
    state = s.state if hasattr(s, "state") else {}
    for chave in ["blog_outline","outline_validation","blog_post","post_validation"]:
        chk(f"state tem {chave}", chave in state, str(state.get(chave,""))[:38])

    # o veredito "ok" precisa ter chegado intacto (e o que dispara o escalate)
    tem_ok = any("ok" in str(m.get("content") or "").lower() for c in CHAMADAS for m in c["messages"])
    chk("veredito 'ok' chegou ao modelo", tem_ok)
    # cenario retry: cada loop roda 2x (1 retry + 1 ok) => 11 chamadas.
    # o que importa e que o retry aconteceu E o loop escapou depois.
    fases_l = [c.get("_fase") for c in CHAMADAS]
    chk("loop do planner iterou 2x", fases_l.count("check_outline") == 2, str(fases_l.count("check_outline")))
    chk("loop do writer iterou 2x", fases_l.count("check_post") == 2, str(fases_l.count("check_post")))
    chk("escalate escapou apos o retry (11 chamadas)", len(CHAMADAS) == 11, f"{len(CHAMADAS)} chamadas")

    servidor.shutdown()
    print("\n" + "="*58)
    print("RESULTADO:", "BLOGGER LOCAL OK" if ok else "FALHOU")
    return 0 if ok else 1

sys.exit(asyncio.run(main()))
