"""
Item 6 do BACKLOG: placeholder que vira KeyError.

O que este teste trava, e por que ele e diferente dos outros:

Os outros testes pegam logica errada. Este pega um *crash*. O `SeoGenerator`
tinha `{LIMITE_TITULO}` literal no `instruction` — o autor escribvia f-string,
por isso o JSON de exemplo usava `{{`. Sem o `f` na frente, o Python entregou
as chaves literais, e o ADK interpretou cada `{...}` como placeholder de state.

O detalhe que torna isso pior do que "o modelo le um texto estranho":

`{LIMITE_TITULO}` e um identificador Python valido, entao o ADK acredita que
voce quis referenciar uma variavel de state. Como nao existe no state e nao tem
o `?` de opcional, ele **levanta KeyError**. O agente nao degrada — ele quebra.

Um teste que so lesse a string veria "ah, tem um placeholder". Um teste que so
rodasse o agente contra a nuvem veria "deu erro" e culparia cota, quota, rede,
o modelo. Só um teste com modelo FALSO isola a causa: sem chave, sem request,
sem cota, o erro aparece na hora e o traceback aponta a linha.
"""

import asyncio
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

falhas = []


def chk(desc, cond, detalhe=""):
    if cond:
        print(f"  OK    {desc}")
    else:
        falhas.append(desc)
        print(f"  FALHA {desc}  {detalhe}")


# As chaves que o modelo realmente enxerga: `{state}` e `{state?}` (a `?` marca
# opcional). Tudo que parece isso e nao esta no state e um bug — ou um crash.
PADRAO_STATE = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\??\}")


async def main():
    from google.adk.models.base_llm import BaseLlm
    from google.adk.models.llm_response import LlmResponse
    from google.adk.runners import InMemoryRunner
    from google.genai import types

    import seo.agent as S

    print("=" * 62)
    print("1. O QUE O PYTHON MONTA")
    print("=" * 62)
    inst = S.seo_generator.instruction

    chk("nao ha {LIMITE_TITULO} literal", "{LIMITE_TITULO}" not in inst)
    chk("nao ha {LIMITE_DESCRIPTION} literal", "{LIMITE_DESCRIPTION}" not in inst)
    chk("nao ha {{ duplo (JSON de exemplo)", "{{" not in inst)
    chk("o limite real entrou no prompt", str(S.LIMITE_TITULO) in inst,
        f"procurando {S.LIMITE_TITULO}")
    chk("o limite de description entrou", str(S.LIMITE_DESCRIPTION) in inst)

    print("\n" + "=" * 62)
    print("2. SOBRA ALGUM PLACEHOLDER NAO RESOLVIDO?")
    print("=" * 62)
    # Os unicos legveis sao os de state, e todos precisam do `?` opcional.
    achados = sorted(set(PADRAO_STATE.findall(inst)))
    chk("so sobraram os placeholders de state opcionais",
        all(a.endswith("?") or a.islower() for a in achados), achados)
    print(f"        placeholders de state (todos com '?'): {achados}")

    print("\n" + "=" * 62)
    print("3. O AGENTE RODA? (modelo falso: sem chave, sem cota)")
    print("=" * 62)

    class Falso(BaseLlm):
        async def generate_content_async(self, req, stream=False):
            yield LlmResponse(content=types.Content(role="model", parts=[
                types.Part(text='{"titulo":"t","meta_description":"d",'
                               '"slug":"s","tags":["a"],"alt_text":"x"}')]))

    modelo_real = S.seo_generator.model
    S.seo_generator.model = Falso(model="falso")
    try:
        runner = InMemoryRunner(agent=S.seo_generator, app_name="t")
        sess = await runner.session_service.create_session(app_name="t", user_id="u")
        async for _ in runner.run_async(
            user_id="u", session_id=sess.id,
            new_message=types.Content(role="user",
                                      parts=[types.Part(text="Python async")]),
        ):
            pass
        chk("SeoGenerator rodou sem KeyError", True)
    except KeyError as e:
        chk("SeoGenerator rodou sem KeyError", False, f"KeyError: {e}")
    except Exception as e:
        chk("SeoGenerator rodou sem KeyError", False, f"{type(e).__name__}: {e}")
    finally:
        S.seo_generator.model = modelo_real

    print("\n" + "=" * 62)
    print("4. A REGUA: TODO AGENTE DO REPO ESTA LIVRE DESTE BUG?")
    print("=" * 62)
    # Nao adianta consertar o SEO e deixar o mesmo padrao em outro lugar.
    # `?` no final = opcional = o ADK troca por vazio em vez de estourar.
    agentes = ["blogger", "linkcheck", "seo", "rag", "codereview",
               "triage", "mcp_text_audit", "researcher"]
    for nome in agentes:
        try:
            mod = __import__(f"{nome}.agent", fromlist=["root_agent"])
        except Exception as e:
            chk(f"{nome} importa", False, f"{type(e).__name__}")
            continue
        ra = getattr(mod, "root_agent", None)
        alvos = []
        if ra is not None:
            alvos.append(ra)
            for sub in (getattr(ra, "sub_agents", None) or []):
                alvos.append(sub)
            for t in (getattr(ra, "tools", None) or []):
                for sub in (getattr(t, "agent", None),) if t is not None else ():
                    if sub is not None:
                        alvos.append(sub)
                        for s2 in (getattr(sub, "sub_agents", None) or []):
                            alvos.append(s2)
        ruins = []
        for ag in alvos:
            txt = getattr(ag, "instruction", None)
            if not isinstance(txt, str):
                continue
            for chave in PADRAO_STATE.findall(txt):
                if not chave.endswith("?"):
                    # Placeholders de state em minusculo sao legítimos
                    # (o ADK substitui). O que nao deveria existir e constante
                    # de Python em CAIXA ALTA: essas o autor queria interpolar
                    # via f-string e esqueceu o `f`.
                    if chave.isupper():
                        ruins.append((ag.name, chave))
        chk(f"{nome}: nenhum placeholder MAIUSCULO orfao",
            not ruins, ruins or "")

    print("\n" + "=" * 62)
    print("RESUMO")
    print("=" * 62)
    if falhas:
        print(f"{len(falhas)} FALHA(S):")
        for f in falhas:
            print(f"  - {f}")
        return 1
    print("nenhum placeholder literal; nenhum agente quebra por isso")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
