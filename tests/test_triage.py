"""
Triage — testes do roteador e do fallback, sem chamar API.

Por que o Jev e a parte testavel:

O `jev.decidir()` e uma funcao pura: probabilities + limiar -> rota. Nao
precisa de rede, nem de chave, nem do modelo. A politica de roteamento inteira
cabe num `for` com um dicionario dentro, entao um teste de verdade aqui vale
mais do que um teste de integracao que so reexecuta o que o codigo ja faz.

O que NAO e testado aqui, e o que precisa ficar registrado: se a confianca que
o Jev devolve e bem calibrada no SEU dataset. Isso nao se testa com mock —
precisa de mensagens reais e labeled. E o unico jeito de descobrir se o limiar
0.85 faz o que voce espera.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from triage import jev  # noqa: E402
from triage.agent import LIMIAR, classificar_ticket  # noqa: E402

falhas = []

# As chaves do `.env` da maquina nao podem decidir o resultado deste arquivo.
# O `triage.agent` importa `common`, que chama `load_dotenv()`: com
# `OPENROUTER_API_KEY` no `.env` de quem roda, `jev.disponivel()` vira True e
# o bloco de fallback abaixo mede a maquina, nao o codigo.
for _var in ("TYPESAFE_API_KEY", "OPENROUTER_API_KEY", "JEV_PROVEDOR"):
    os.environ.pop(_var, None)


def chk(desc, cond, detalhe=""):
    (print(f"  OK    {desc}") if cond else falhas.append(desc) or
     print(f"  FALHA {desc}  {detalhe}"))
    return cond


print("=" * 62)
print("ROTEAMENTO: a regra e uma funcao pura (sem API, sem chave)")
print("=" * 62)

confianca_alta = {
    "department": {
        "choice": "billing",
        "confidence": 0.93,
        "probabilities": {"billing": 0.93, "technical": 0.05, "sales": 0.02},
    },
    "urgent": {"noul": 0.0},
    "frustration": {"score": 0.0},
}
confianca_baixa = {
    "department": {
        "choice": "technical",
        "confidence": 0.52,
        "probabilities": {"technical": 0.52, "billing": 0.44, "sales": 0.04},
    },
    "urgent": {"noul": 1.0},
    "frustration": {"score": 2.0},
}

print("\n- confianca alta roteia sozinho")
d = jev.decidir(confianca_alta, LIMIAR)
chk("0.93 >= 0.85 vira auto:billing", d["rota"] == "auto:billing", d)
chk("auto_roteado marcado", d["auto_roteado"] is True)
chk("probabilidades preservadas", d["probabilidades"]["billing"] == 0.93)

print("\n- confianca baixa manda para revisao humana")
d = jev.decidir(confianca_baixa, LIMIAR)
chk("0.52 < 0.85 vira revisar", d["rota"] == "revisar", d)
chk("auto_roteado marcado False", d["auto_roteado"] is False)
chk("motivo cita o limiar", "0.52" in d["motivo"] and "0.85" in d["motivo"], d["motivo"])
chk("motivo nomeia o segundo colocado", "billing" in d["motivo"], d["motivo"])
chk("urgencia e frustrated preservadas", d["urgente"] is True and d["frustracao"] == 2.0)

print("\n- o limiar e um argumento, nao uma constante escondida")
chk("0.93 com limiar 0.95 vai para revisao",
    jev.decidir(confianca_alta, 0.95)["rota"] == "revisar")
chk("0.52 com limiar 0.50 vira auto:technical",
    jev.decidir(confianca_baixa, 0.50)["rota"] == "auto:technical")

print("\n- limiar exato e inclusivo (>=, nao >)")
no_limiar = {"department": {"choice": "sales", "confidence": 0.85, "probabilities": {"sales": 0.85}}}
chk("confidence == limiar roteia", jev.decidir(no_limiar, 0.85)["auto_roteado"] is True)

print("\n- respostas degeneradas nao estouram")
d = jev.decidir({}, LIMIAR)
chk("answers vazio vai para revisao", d["rota"] == "revisar", d)
chk("answers vazio tem confianca 0", d["confianca"] == 0.0)
d = jev.decidir({"department": {}}, LIMIAR)
chk("sem choice vai para revisao", d["rota"] == "revisar", d)

print("\n" + "=" * 62)
print("FALLBACK: sem chave o agente avisa em vez de fingir confianca")
print("=" * 62)

print("\n- a tool nao quebra sem TYPESAFE_API_KEY")
os.environ.pop("TYPESAFE_API_KEY", None)
chk("jev.indisponivel() sem chave", jev.disponivel() is False)
r = classificar_ticket("minha fatura veio errada")
chk("tool retorna modo fallback", r.get("modo") == "fallback", r)
chk("tool diz qual chave falta",
    "OPENROUTER_API_KEY" in r.get("erro", "")
    and "TYPESAFE_API_KEY" in r.get("erro", ""), r)
chk("tool avisa que nao ha confianca calibrada",
    "calibrada" in r.get("aviso", ""), r)
chk("fallback NAO inventa numero de confianca",
    "confianca" not in r and "confidence" not in r, r)

print("\n- mensagem vazia e rejeitada antes de qualquer chamada")
r = classificar_ticket("   ")
chk("vazia da erro claro", "vazia" in r.get("erro", ""), r)

print("\n- jev.classificar nao levanta excecao sem chave")
os.environ["TYPESAFE_API_KEY"] = ""
r = jev.classificar("teste")
chk("devolve dict com erro, nao exception", isinstance(r, dict) and "erro" in r, r)
del os.environ["TYPESAFE_API_KEY"]

print("\n" + "=" * 62)
print("OS DOIS CAMINHOS: TypeSafe direto e OpenRouter")
print("=" * 62)
print("\n- sem chave nenhuma, nenhum caminho e escolhido")
os.environ.pop("TYPESAFE_API_KEY", None)
os.environ.pop("OPENROUTER_API_KEY", None)
chk("_provedor() devolve None", jev._provedor() is None)
chk("disponivel() segue False", jev.disponivel() is False)

print("\n- so a do OpenRouter: usa o gateway, nao a API direta")
os.environ["OPENROUTER_API_KEY"] = "or-test-fake"
destino = jev._provedor()
chk("so existe o caminho OpenRouter", destino is not None and destino[2] == "openrouter",
    destino)
chk("endpoint e o do OpenRouter",
    destino and destino[0] == "https://openrouter.ai/api/v1/systemone",
    destino[0] if destino else "")
chk("a chave do env e a que vai no cabecalho",
    destino and destino[1] == "or-test-fake")
chk("disponivel() vira True", jev.disponivel() is True)

print("\n- as duas chaves: TypeSafe tem preferencia (vai direto ao fornecedor)")
os.environ["TYPESAFE_API_KEY"] = "ts-test-fake"
destino = jev._provedor()
chk("TypeSafe ganha", destino and destino[2] == "typesafe", destino)
chk("endpoint e o da TypeSafe",
    destino and destino[0] == "https://api.typesafe.ai/v1/systemone",
    destino[0] if destino else "")

print("\n- JEV_PROVEDOR inverte a preferencia")
os.environ["JEV_PROVEDOR"] = "openrouter"
destino = jev._provedor()
chk("forca OpenRouter mesmo com as duas chaves",
    destino and destino[2] == "openrouter", destino)
os.environ["JEV_PROVEDOR"] = "typesafe"
chk("forca TypeSafe tambem", jev._provedor()[2] == "typesafe")

print("\n- JEV_PROVEDOR invalido nao escolhe caminho nenhum")
os.environ["JEV_PROVEDOR"] = "openai"
chk("provedor desconhecido devolve None", jev._provedor() is None)
chk("e disponivel() fica False", jev.disponivel() is False)

print("\n- JEV_PROVEDOR apontando para uma chave ausente tambem nao inventa")
os.environ["JEV_PROVEDOR"] = "openrouter"
os.environ.pop("OPENROUTER_API_KEY", None)
chk("sem a chave, forcar o provedor nao magica nada", jev._provedor() is None)

for _var in ("TYPESAFE_API_KEY", "OPENROUTER_API_KEY", "JEV_PROVEDOR"):
    os.environ.pop(_var, None)

print("\n" + "=" * 62)
print("CONTRATO DA TOOL (o schema que o modelo ve)")
print("=" * 62)
from triage.agent import classificar_tool  # noqa: E402

fn = classificar_tool.func
sig = fn.__annotations__
chk("parametro se chama mensagem", "mensagem" in sig, sig)
chk("mensagem e str", sig.get("mensagem") is str, sig)
chk("devolve dict", sig.get("return") is dict, sig)
desc = (classificar_tool.description or "")
# O schema que o modelo le precisa estar em ingles. Nao validamos palavra por
# palavra: validamos que a descricao NAO esta em portugues, que e o defeito real.
nao_ingles = [p for p in ("suporte", "classificacao", "decide a fila", "mensagem de suporte",
                          "destino", "bruto", "chegou") if p in desc.lower()]
chk("descricao NAO esta em portugues", not nao_ingles, f"palavras PT: {nao_ingles}")
chk("descricao explica o que a tool faz", "classif" in desc.lower() and "support" in desc.lower(),
    desc[:60])
chk("documenta os Args que o modelo ve para inferir", "mensagem" in desc.lower(), desc[:80])

print("\n" + "=" * 62)
print("RESUMO")
print("=" * 62)
if falhas:
    print(f"{len(falhas)} FALHA(S):")
    for f in falhas:
        print(f"  - {f}")
    sys.exit(1)
print("todas as verificacoes passaram")
