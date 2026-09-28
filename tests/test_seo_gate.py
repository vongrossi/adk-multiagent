"""
Item 5 do BACKLOG: o portao de Python precisa valer mais que o "ok" do LLM.

O que este teste trava, e por que ele e diferente dos outros:

Os outros testes verificam que uma funcao devolve a resposta certa. Este
verifica uma *hierarquia de autoridade*. Antes da correção, o callback
`escalate_when_valid` lia apenas `seo_validation` — o veredito do LLM — e
escalava se ele começasse com "ok". `validar_metadados` estava escrito,
testado por CalledTool e documentado no README, mas NADA no runtime o chamava.

Ou seja: um modelo que respondesse "ok" para um título de 180 caracteres
escapava do limite de 100 do Blogger. O código de validação era decorativo.

Por que um teste comum não pegaria isso:
- Assert sobre `validar_metadados` passa: a função está correta.
- Rodar o agente na nuvem passa: o modelo "se comporta".
- Só um teste que injeta um "ok" do LLM sobre metadata INVÁLIDA e depois
  confere se `escalate` continuou False prova que o portão é o Python.

O script abaixo é determinístico: nenhum modelo é chamado.
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

falhas = []


def chk(desc, cond, detalhe=""):
    if cond:
        print(f"  OK    {desc}")
    else:
        falhas.append(desc)
        print(f"  FALHA {desc}  {detalhe}")


class CtxFalso:
    """So o minimo que o callback usa: state + actions."""

    def __init__(self, state):
        self.state = state
        self.actions = type("A", (), {"escalate": False})()
        self.agent_name = "SeoValidator"


def meta_valido():
    return {
        "titulo": "Pacotes Python que todo dev deveria conhecer",
        "meta_description": "Um tour rapido por pacotes Python que economizam "
                            "tempo em projetos do dia a dia.",
        "slug": "pacotes-python-que-todo-dev-deveria-conhecer",
        "tags": ["python", "pacotes", "produtividade"],
        "alt_text": "Capa com tres caixas de pacotes Python empilhadas",
    }


def main():
    from seo.agent import escalate_when_valid, validar_metadados

    cb = escalate_when_valid("seo_validation")

    print("=" * 66)
    print("1. O LLM DIZ 'ok', O PYTHON REPROVA -> O LLM NAO VENCE")
    print("=" * 66)

    casos = {
        "titulo com 180 chars (limite 100)": lambda d: d.update(
            titulo="T" * 180),
        "meta_description com 300 chars (limite 155)": lambda d: d.update(
            meta_description="D" * 300),
        "slug com acento": lambda d: d.update(slug="configuração-instalada"),
        "slug com espaco": lambda d: d.update(slug="pacotes python"),
        "slug com underscore": lambda d: d.update(slug="pacotes_python"),
        "slug terminando em hifen": lambda d: d.update(slug="pacotes-"),
        "slug com 70 chars (limite 50)": lambda d: d.update(
            slug="a" * 70),
        "6 tags (limite 5)": lambda d: d.update(
            tags=["python", "pip", "testes", "async", "web", "dados"]),
        "tag generica proibida": lambda d: d.update(
            tags=["python", "blog", "post"]),
        "tags repetidas": lambda d: d.update(
            tags=["python", "python", "pip"]),
        "alt_text vazio": lambda d: d.update(alt_text=""),
        "campo obrigatorio faltando": lambda d: d.pop("slug"),
        "titulo vazio": lambda d: d.update(titulo=""),
    }

    for nome, mutilar in casos.items():
        d = meta_valido()
        mutilar(d)
        ctx = CtxFalso({
            "seo_metadata": json.dumps(d, ensure_ascii=False),
            "seo_validation": "ok",   # o LLM aprovou. de proposito.
        })
        cb(ctx)
        reprovado = (not ctx.actions.escalate
                     and ctx.state["seo_validation"].startswith("retry"))
        chk(f"bloqueia: {nome}", reprovado,
            f"escalate={ctx.actions.escalate} "
            f"estado={ctx.state['seo_validation'][:60]!r}")

    print("\n" + "=" * 66)
    print("2. O ERRO DO PYTHON VOLTA PARA O GERADOR (loop pode corrigir)")
    print("=" * 66)
    d = meta_valido(); d["titulo"] = "T" * 180
    ctx = CtxFalso({"seo_metadata": json.dumps(d), "seo_validation": "ok"})
    cb(ctx)
    msg = ctx.state["seo_validation"]
    chk("o estado virou 'retry: ...'", msg.startswith("retry"), msg[:60])
    chk("a mensagem diz quantos chars tem", "180" in msg, msg[:80])
    chk("a mensagem diz o limite", "100" in msg, msg[:80])
    chk("nao escalou", not ctx.actions.escalate)
    print("        o que o SeoGenerator vai ler no proximo passo:")
    print(f"        {msg}")

    print("\n" + "=" * 66)
    print("3. METADATA VALIDO + LLM 'ok' -> ESCALA (o caso feliz)")
    print("=" * 66)
    ctx = CtxFalso({"seo_metadata": json.dumps(meta_valido()),
                    "seo_validation": "ok"})
    cb(ctx)
    chk("escalou", ctx.actions.escalate)
    chk("o veredito continua 'ok'", ctx.state["seo_validation"] == "ok")

    print("\n" + "=" * 66)
    print("4. METADATA VALIDO, MAS O LLM REPROVA -> O LLM VENCE AQUI")
    print("=" * 66)
    # O julgamento semantico ("isso e sobre ESTE post?") e do LLM e nao ha como
    # fazer em Python. Ele precisa poder reprovar algo que passa nos limites.
    ctx = CtxFalso({"seo_metadata": json.dumps(meta_valido()),
                    "seo_validation": "retry: fala de Java, nao de Python"})
    cb(ctx)
    chk("nao escalou mesmo com metadata dentro dos limites",
        not ctx.actions.escalate)
    chk("o motivo do LLM foi preservado",
        ctx.state["seo_validation"] == "retry: fala de Java, nao de Python")

    print("\n" + "=" * 66)
    print("5. JSON QUEBRADO")
    print("=" * 66)
    for nome, bruto in [("JSON invalido", "{titulo: sem aspas}"),
                        ("JSON vazio", ""),
                        ("estado sem seo_metadata", None)]:
        st = {"seo_validation": "ok"}
        if bruto is not None:
            st["seo_metadata"] = bruto
        ctx = CtxFalso(st)
        cb(ctx)
        chk(f"nao escala e pede retry: {nome}",
            (not ctx.actions.escalate
             and ctx.state["seo_validation"].startswith("retry")),
            f"escalate={ctx.actions.escalate} "
            f"estado={ctx.state['seo_validation'][:50]!r}")

    print("\n" + "=" * 66)
    print("6. A FUNCAO DE VALIDACAO ESTA MESMO SENDO USADA")
    print("=" * 66)
    # Sem isto, os testes 1-5 passam mas o callback pode ter regredido para
    # "so olha o LLM". Este bloco usa a funcao real, nao uma copia.
    d = meta_valido(); d["tags"] = ["python", "blog"]
    erros = validar_metadados(d)
    chk("validar_metadados acha a tag proibida",
        any("blog" in e for e in erros), erros)
    ctx = CtxFalso({"seo_metadata": json.dumps(d), "seo_validation": "ok"})
    cb(ctx)
    chk("o callback usa a MESMA funcao (erro aparece no estado)",
        "blog" in ctx.state["seo_validation"], ctx.state["seo_validation"][:70])

    print("\n" + "=" * 66)
    print("RESUMO")
    print("=" * 66)
    if falhas:
        print(f"{len(falhas)} FALHA(S):")
        for f in falhas:
            print(f"  - {f}")
        return 1
    print("Python manda nos limites duros; LLM manda no julgamento semantico")
    return 0


if __name__ == "__main__":
    sys.exit(main())
