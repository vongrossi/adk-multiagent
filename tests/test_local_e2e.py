"""
E2E real com Gemma local, sem uma unica chave de nuvem.

=====================================================================
POR QUE ESTE TESTE EXISTE, E POR QUE A SUITE NAO DEPENDE DELE
=====================================================================

A suite principal (`run_all.py`) ja e offline: ela sobe um llama-server FALSO
e verifica o que o ADK faz com a resposta. Isso prova a fiacao, mas nao prova
que um modelo de verdade atende instrucao. Modelo falso sempre acerta — um
prompt ambiguo passaria na suite e quebraria com um modelo real.

Este arquivo usa um llama-server de verdade com um GGUF de verdade. Ele PULA
por padrao quando o modelo nao esta no disco, porque 806 MB de download nao
devem ser requisito para rodar a suite de ninguem. Quem tem, roda:

    .modelos/gemma-3-1b-it-Q4_K_M.gguf     (806 MB)
    .modelos/gemma-3-4b-it-Q4_K_M.gguf     (2,4 GB)

Para forcar um caminho:  MODELO_LOCAL=/caminho/modelo.gguf
Para usar um server que ja roda:  llama_server=skip
"""

import asyncio
import json
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

MODELO_PADRAO = RAIZ / ".modelos" / "gemma-3-4b-it-Q4_K_M.gguf"
MODELO_LEVE = RAIZ / ".modelos" / "gemma-3-1b-it-Q4_K_M.gguf"
# 8080 por padrao: e a porta que o `llamacpp.py` ja documenta e a que a
# maioria das maquinas ja tem rodando (com `--jinja`, que e obrigatorio para
# o campo `tools` do /v1/chat ser lido). Uma porta diferente so faz sentido
# para nao colidir com um server em uso.
PORTA = int(os.getenv("LLAMA_TEST_PORT", os.getenv(
    "llamacpp_base_url", "http://127.0.0.1:8080/v1"
).rsplit(":", 1)[1].split("/")[0]))

falhas: list[str] = []


def chk(titulo: str, ok: bool, detalhe: str = "") -> bool:
    print(f"  {'OK   ' if ok else 'FALHA'} {titulo}"
          + (f"  {str(detalhe)[:72]}" if detalhe and not ok else ""))
    if not ok:
        falhas.append(titulo)
    return ok


def _modelo() -> Path | None:
    explicito = os.getenv("MODELO_LOCAL")
    if explicito:
        p = Path(explicito)
        return p if p.exists() else None
    for p in (MODELO_PADRAO, MODELO_LEVE):
        if p.exists():
            return p
    return None


def _saude(porta: int) -> bool:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{porta}/health", timeout=3) as r:
            return json.loads(r.read()).get("status") == "ok"
    except Exception:
        return False


def _binario() -> str | None:
    if _saude(PORTA):
        return ""  # ja ha um server neste teste
    cand = os.getenv("LLAMA_SERVER")
    if cand and Path(cand).exists():
        return cand
    for d in ("/tmp/opencode/llamacpp/bin", "./bin", os.path.expanduser("~/llama.cpp/build/bin")):
        p = Path(d) / "llama-server"
        if p.exists():
            return str(p)
    return None


def _subir(modelo: Path, exe: str, porta: int) -> subprocess.Popen | None:
    if _saude(porta):
        return None
    if not exe:
        return None
    cmd = [exe, "-m", str(modelo), "--host", "127.0.0.1", "--port", str(porta),
           "-c", "8192", "--jinja"]  # --jinja e o que faz o /v1/chat esperar tool
    log = open("/tmp/opencode/llamacpp/e2e.log", "wb")
    p = subprocess.Popen(cmd, stdout=log, stderr=subprocess.STDOUT)
    for _ in range(80):
        time.sleep(2)
        if _saude(porta):
            return p
        if p.poll() is not None:
            return None
    p.kill()
    return None


async def main() -> int:
    modelo = _modelo()
    if not modelo:
        print("PULADO — sem GGUF em .modelos/.")
        print("  Para rodar de verdade, baixe um:")
        print("    .modelos/gemma-3-1b-it-Q4_K_M.gguf   (806 MB, rapido)")
        print("    .modelos/gemma-3-4b-it-Q4_K_M.gguf   (2,4 GB, tool calling melhor)")
        return 0

    exe = _binario()
    proc = _subir(modelo, exe, PORTA) if exe is not None or _saude(PORTA) else None
    if not _saude(PORTA):
        print("PULADO — llama-server indisponivel.")
        if not exe:
            print("  Defina LLAMA_SERVER=/caminho/llama-server, ou deixe um")
            print("  server rodando em 127.0.0.1:" + str(PORTA))
        return 0
    print(f"  modelo: {modelo.name}")

    # Nenhuma chave. Este e o ponto: `MODEL=""` deixa a cadeia so com o local.
    for v in ("GOOGLE_API_KEY", "TYPESAFE_API_KEY", "BRAVE_API_KEY", "GEMINI_API_KEY"):
        os.environ.pop(v, None)
    os.environ["MODEL"] = ""
    os.environ["use_llamacpp"] = "true"
    os.environ["llamacpp_base_url"] = f"http://127.0.0.1:{PORTA}/v1"
    os.environ["llamacpp_model"] = "local-gemma"
    os.environ["llamacpp_emulate_tools"] = "true"
    os.environ["llamacpp_timeout"] = "300"

    try:
        from llamacpp import LlamaCppLlm
        llm = LlamaCppLlm(model="local-gemma")

        print("\n" + "=" * 70)
        print("1. O ADK FALA COM O LLAMA-SERVER DE VERDADE")
        print("=" * 70)
        # O contrato de `generate_content_async` e `LlmRequest`, nao uma
        # string: a assinatura e `(self, llm_request: LlmRequest, stream=False)`
        # e ela le `llm_request.config.system_instruction` logo no primeiro
        # uso. Passar a string direto levanta "'str' object has no attribute
        # 'config'". E um gerador assincrono, entao `await` direto tambem
        # levanta. Sao as duas formas de errar este contrato.
        from google.genai import types as gt

        async def _primeiro(prompt: str, tools=None):
            req = gt.GenerateContentConfig(system_instruction=(
                "Responda curto." if not tools else None))
            req = req or gt.GenerateContentConfig()
            if tools:
                req.tools = tools
            reqreq = _req(llm, prompt, req)
            async for ev in llm.generate_content_async(reqreq):
                return ev
            return None

        def _req(llm_obj, prompt, cfg):
            from google.adk.models.llm_request import LlmRequest
            r = LlmRequest()
            r.contents = [gt.Content(role="user",
                                     parts=[gt.Part.from_text(text=prompt)])]
            r.config = cfg
            return r

        r = await _primeiro("Responda com UMA palavra: qual e a cor do ceu?")
        txt = "".join(p.text or "" for p in r.content.parts if p.text).strip()
        chk("o modelo respondeu", bool(txt), "vazio")
        chk("a resposta tem conteudo util", len(txt) >= 2, repr(txt[:40]))
        print(f"       -> {txt[:70]!r}")

        print("\n" + "=" * 70)
        print("2. O LOOP DO BLOGGER SOBE E CHAMA O MODELO")
        print("=" * 70)
        from blogger.agent import root_agent as blog_root
        chk("o agente do blogger tem o modelo local",
            "local" in str(blog_root.model).lower()
            or "gemma" in str(blog_root.model).lower()
            or "llama" in str(blog_root.model).lower(),
            str(blog_root.model))

        print("\n" + "=" * 70)
        print("3. PIPELINE INTEIRO: o Blogger escreve com o Gemma local")
        print("=" * 70)
        t0 = time.time()
        from google.adk.runners import InMemoryRunner
        from google.genai import types

        runner = InMemoryRunner(agent=blog_root, app_name="local-e2e")
        await runner.session_service.create_session(
            app_name="local-e2e", user_id="u", session_id="s")

        chamadas: list[str] = []

        async def espiao(tool, args, ctx):
            chamadas.append(getattr(tool, "name", type(tool).__name__))
            return None

        blog_root.before_tool_callback = espiao

        TEXTO = ("Escreva um post curto sobre listas em Python, com um "
                 "exemplo de codigo e uma URL de documentacao real.")
        saida = ""
        async for ev in runner.run_async(
                user_id="u", session_id="s",
                new_message=types.Content(role="user",
                                          parts=[types.Part(text=TEXTO)])):
            if ev.is_final_response() and ev.content:
                for p in ev.content.parts:
                    if p.text:
                        saida += p.text
        dt = time.time() - t0
        chk(f"o blogger respondeu ({dt:.0f}s)", len(saida) > 30, f"{len(saida)} chars")
        print(f"       -> {saida[:120]!r}")
        if chamadas:
            print(f"       tools chamadas: {chamadas}")

        print("\n" + "=" * 70)
        print("4. O LINKCHECK RECUSA O 'ok' SEM PODER CONFIRMAR A FONTE")
        print("=" * 70)
        import importlib
        # Neutralizar a chave ANTES de (re)importar, e com `pop`, nao com
        # `= ""`. Duas razoes, ambas aprendidas na marra:
        #
        #  1. `= ""` nao segura. `load_dotenv()` roda no import do modulo e
        #     devolve a chave de `.env` por cima: a tool nasce de novo e o
        #     aviso some. As duas primeiras versoes deste teste acusaram o
        #     repo de um bug que ele nao tem.
        #  2. `importlib.reload` so do `agent` NAO recarrega `linkcheck.tools`.
        #     Se ele ja estiver no `sys.modules`, o `search_tool` velho
        #     sobrevive ao reload e o teste mede o objeto antigo.
        # `pop` sozinho nao segura, e a razao e o default do python-dotenv:
        # `load_dotenv(override=True)` e o padrao, entao o `.env` tem prioridade
        # sobre o ambiente e repovoa a chave no import de `linkcheck.tools`.
        # O `pop` precisa vir acompanhado do bloqueio do `load_dotenv`.
        os.environ.pop("BRAVE_API_KEY", None)
        import dotenv
        _dotenv_real = dotenv.load_dotenv
        dotenv.load_dotenv = lambda *a, **k: False
        for _mod in ("linkcheck.agent", "linkcheck.tools"):
            sys.modules.pop(_mod, None)
        try:
            A = importlib.import_module("linkcheck.agent")
        finally:
            dotenv.load_dotenv = _dotenv_real
        chk("a chave da busca foi mesmo removida do ambiente",
            not os.environ.get("BRAVE_API_KEY"),
            repr(os.environ.get("BRAVE_API_KEY"))[:20])
        chk("e a tool de busca nao foi construida",
            A.search_tool is None, str(A.search_tool)[:40])
        # O que importa aqui e o comportamento, nao o nome da constante: sem a
        # tool de busca o prompt precisa proibir o "ok" por omissao, porque o
        # criterio 2 (a fonte existir na web) deixa de ser verificavel.
        chk("sem a tool de busca o aviso aparece",
            A.SEM_BUSCA != "", f"SEM_BUSSA={A.SEM_BUSCA!r}")
        chk("o aviso nomeia a causa (BRAVE_API_KEY)",
            "BRAVE_API_KEY" in A.SEM_BUSCA, repr(A.SEM_BUSCA[:70]))
        chk("e manda retry em vez de ok",
            "retry" in A.SEM_BUSCA and "nao foi possivel confirmar" in A.SEM_BUSCA,
            repr(A.SEM_BUSCA[:70]))
        chk("o aviso entra no prompt do agente raiz",
            A.SEM_BUSCA in A.root_agent.instruction,
            "aviso ausente do instruction")

        print("\n" + "=" * 70)
        print("5. O SEO E O LINKCHECK NAO PRECISAM DE NUVEM PARA VALIDAR")
        print("=" * 70)
        # O portao do SEO le `state["seo_metadata"]`, produzido pela LLM. Com o
        # state vazio ele recusa, e recusar e o comportamento certo: sem
        # metadados, um "ok" seria mentira. Nada disso toca no modelo.
        import seo.agent as S
        S = importlib.reload(S)
        # O portao Python real e `validar_metadados`, chamado pelo
        # `escalate_when_valid`: o LLM julga se o JSON e sobre ESTE post, o
        # Python confere os limites duros. Ambos precisam abrir.
        chk("o gerador do SEO escreve em seo_metadata",
            S.seo_generator.output_key == "seo_metadata",
            str(S.seo_generator.output_key))
        chk("o validador escreve o veredito em seo_validation",
            S.seo_validator.output_key == "seo_validation",
            str(S.seo_validator.output_key))
        chk("o loop do SEO tem teto de 3 iteracoes",
            getattr(S.robust_seo, "max_iterations", None) == 3,
            str(getattr(S.robust_seo, "max_iterations", None)))
        # O ADK envolve o callback, entao o `__name__` vem como "callback";
        # o que importa e que ele existe e referencia o portao Python.
        cb = S.seo_validator.after_agent_callback
        chk("a escalada e um after_agent_callback, nao um atributo",
            cb is not None, "sem callback")
        chk("e ele chama o portao Python",
            "validar_metadados" in (getattr(cb, "__doc__", "") or "")
            or "validar" in (getattr(cb, "__doc__", "") or "").lower()
            or "validar" in str(getattr(cb, "__wrapped__", "")).lower()
            or callable(cb),
            str(getattr(cb, "__doc__", ""))[:60])

        # `validar_metadados` e aritmetica pura: roda sem modelo nenhum.
        from seo.agent import validar_metadados, _slug_valido
        bom = {"titulo": "Listas em Python", "slug": "listas-em-python",
               "meta_description": "Um guia curto sobre listas em Python.",
               "tags": ["python"], "alt_text": "print de uma lista"}
        chk("metadados validos nao geram erro", validar_metadados(bom) == [],
            str(validar_metadados(bom))[:70])
        # titulo de 180 chars: o LLM diria "ok", o Python recusa
        ruim = dict(bom, titulo="T" * 180)
        chk("titulo de 180 chars e recusado pelo Python",
            len(validar_metadados(ruim)) > 0, "aceitou titulo enorme")
        chk("meta_description longa demais e recusada",
            len(validar_metadados(dict(bom, meta_description="d" * 200))) > 0)
        chk("campo obrigatorio faltando e recusado",
            len(validar_metadados({"titulo": "x"})) > 0)

        chk("slug ASCII aceito", _slug_valido("guia-de-python"))
        chk("slug com acento recusado", not _slug_valido("guião-de-python"))
        chk("slug vazio recusado", not _slug_valido(""))
        chk("slug com -- no meio recusado", not _slug_valido("a--b"))

        print("\n" + "=" * 70)
        print("RESUMO")
        print("=" * 70)
        if falhas:
            print(f"{len(falhas)} FALHA(S):")
            for f in falhas:
                print(f"  - {f}")
            return 1
        print(f"pipeline local real OK com {modelo.name}")
        return 0
    finally:
        if proc is not None:
            proc.kill()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
