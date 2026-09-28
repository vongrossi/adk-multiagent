"""
Jev — cliente HTTP para o System One Model da TypeSafe AI.

=====================================================================
POR QUE ISTO EXISTE, E POR QUE NAO E UM MODELO DO ADK
=====================================================================

O ADK tem `BaseLlm` como contrato de um gerador de TEXTO. O Jev é
deliberadamente o oposto: nao gera texto. Ele recebe um `state` e perguntas
tipadas, e devolve decisoes tipadas com probabilidade e confianca calibrada.
Coloca-lo em `Agent(model=...)` seria um erro de contrato — todo o resto do
pipeline do projeto (os loops `ok`/`retry`, os validadores, o Writer) le texto.

A integracao correta e o Jev como TOOL, e e o que este arquivo habilita. O
motivo e mais forte que conveniencia:

  - Um LLM de texto classifica "minha fatura veio errada" e pode responder
    `billing` com a mesma frase com que responderia `sales`. Nao existe como
    saber o quao confiante ele esta, porque ele nao tem esse numero.
  - O Jev devolve `{"choice": "billing", "confidence": 0.93,
    "probabilities": {...}}`. O codigo decide: acima de 0.85 roteia sozinho,
    abaixo disso manda para revisao humana. Isso e uma regra de negocio, e ela
    fica no codigo — nao dentro de um prompt que ninguem consegue auditar.

Chamar "zero hallucination" e o argumento da empresa, e nao vou repetir como
se fosse medido. O que da para afirmar aqui: nao existe geracao de texto, entao
nao existe texto inventado. O resto (a calibracao) e claim do fornecedor, e
so se descobre medindo o seu proprio dataset.

=====================================================================
SEM CHAVE, O AGENTE AINDA FUNCIONA
=====================================================================

Sem chave de Jev no `.env` — `TYPESAFE_API_KEY` ou `OPENROUTER_API_KEY`, serve
uma so — `disponivel()` devolve False e o agente cai no caminho de
classificacao por texto, deixando claro na resposta que a rota usada foi a
fallback. Degrada em vez de derrubar — o mesmo principio do `llamacpp.py`,
que tambem e opcional.

=====================================================================
UMA CHAMADA, VARIAS PERGUNTAS
=====================================================================

Departamento, urgencia e nivel de frustracao sao enviados juntos e avaliados
em paralelo. Uma chamada em vez de tres, e sem context-rot: cada pergunta e
isolada das outras. O ADK nao sabe disso — e o que `classificar` faz, e um
`LlmRequest` com varias `contents` e um `LlmResponse` por vez.
"""

import json
import os
import urllib.error
import urllib.request

ENDPOINT_TYPESAFE = "https://api.typesafe.ai/v1/systemone"
ENDPOINT_OPENROUTER = "https://openrouter.ai/api/v1/systemone"
MODELO = os.getenv("JEV_MODELO", "jev-latest")
TIMEOUT = 20

# =====================================================================
# DOIS CAMINHOS PARA O MESMO MODELO
# =====================================================================
#
# O Jev e um System One da TypeSafe, e a TypeSafe vende o acesso direto. O
# OpenRouter tambem o roteia, sob a propria chave e a propria cobranca. A
# diferenca pratica que importa: a API direta da TypeSafe passou por waitlist,
# e o OpenRouter nao. Sem conta TypeSafe, sem waitlist, sem segunda assinatura.
#
# A API do OpenRouter replica o payload e o formato de resposta do System One
# (`model`, `answers`, `usage`) e mapeia o ID cru do modelo no namespace
# deles: `jev-latest` vira `~typesafe/jev-latest`. Por isso o `MODELO` acima
# serve para os dois caminhos, e o `classificar` nao muda nada.
#
# O que o OpenRouter acrescenta de util: `usage.cost` em dolar, direto na
# resposta. O `classificar` ja repassa o `usage` inteiro, entao o custo
# aparece sem codigo extra.
#
# Precedencia quando as duas chaves existem: TypeSafe primeiro, porque vai
# direto ao fornecedor. `JEV_PROVEDOR=openrouter` inverte, o que e o que os
# testes usam para na depender de qual chave esta na maquina.
PROVEDOR_TYPESAFE = "typesafe"
PROVEDOR_OPENROUTER = "openrouter"


def _provedor() -> tuple[str, str, str] | None:
    """Resolve `(endpoint, chave, rotulo)`, ou `None` se nao houver chave.

    `None` e o que dispara o caminho de fallback do agente. A tupla e
    calculada por chamada e nao no import: o `.env` pode ser recarregado no
    meio do processo (e nos testes e recarregado o tempo todo), e fixar no
    import faz o agente travar na decisao errada.
    """
    forca = os.getenv("JEV_PROVEDOR", "").strip().lower()
    or_bases = {
        PROVEDOR_TYPESAFE: (ENDPOINT_TYPESAFE, "TYPESAFE_API_KEY"),
        PROVEDOR_OPENROUTER: (ENDPOINT_OPENROUTER, "OPENROUTER_API_KEY"),
    }
    if forca:
        if forca not in or_bases:
            return None
        endpoint, var = or_bases[forca]
        chave = os.getenv(var, "")
        return (endpoint, chave, forca) if chave else None

    if os.getenv("TYPESAFE_API_KEY"):
        return ENDPOINT_TYPESAFE, os.environ["TYPESAFE_API_KEY"], PROVEDOR_TYPESAFE
    if os.getenv("OPENROUTER_API_KEY"):
        return (
            ENDPOINT_OPENROUTER,
            os.environ["OPENROUTER_API_KEY"],
            PROVEDOR_OPENROUTER,
        )
    return None


# Categorias de exemplo. Troque livremente: o `criteria` do Choice e o que
# define o que cada categoria significa para o Jev, entao a lista abaixo e a
# unica coisa que precisa mudar para adaptar ao seu negocio.
DEPARTAMENTOS = {
    "billing": "Payment, invoice, subscription, refund or charge problems",
    "technical": "Bugs, errors, API integration failures or outages",
    "sales": "Pricing questions, plan upgrades, quotes or procurement",
}

# Perguntas enviadas na MESMA chamada, avaliadas em paralelo.
PERGUNTAS = {
    "department": {
        "type": "choice",
        "instructions": "Which team should handle this message?",
        "criteria": DEPARTAMENTOS,
    },
    "urgent": {
        "type": "noul",
        "instructions": "Does this message express urgency or a hard deadline?",
    },
    "frustration": {
        "type": "score",
        "instructions": "How frustrated does the customer sound?",
        "criteria": [
            "Calm, just stating facts",
            "Frustrated but still civil",
            "Angry, strong language or escalation threats",
        ],
    },
}


def disponivel() -> bool:
    return _provedor() is not None


def classificar(mensagem: str) -> dict:
    """Envia a mensagem ao Jev e devolve as decisoes tipadas.

    Retorna o corpo de `answers` mais o `usage`. Em caso de erro, devolve
    `{"erro": ...}` em vez de levantar excecao: quem chama e uma tool, e uma
    tool que lanca derruba o turno do agente.
    """
    destino = _provedor()
    if destino is None:
        return {
            "erro": "nenhuma chave de Jev: defina OPENROUTER_API_KEY "
            "ou TYPESAFE_API_KEY no .env"
        }
    endpoint, chave, rotulo = destino
    if not (mensagem or "").strip():
        return {"erro": "mensagem vazia"}

    corpo = json.dumps({
        "state": mensagem,
        "model": MODELO,
        "questions": PERGUNTAS,
    }).encode()
    req = urllib.request.Request(
        endpoint,
        data=corpo,
        headers={
            "Authorization": f"Bearer {chave}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            dados = json.load(r)
    except urllib.error.HTTPError as e:
        # O OpenRouter aninha o motivo em `error.message`; a TypeSafe direta
        # pode devolver texto puro. Tentar os dois evita reportar "HTTP 400: "
        # sem causa, que e o que bug de schema parece. `bruto` comeca vazio
        # porque `e.read()` pode levantar antes de atribuir.
        bruto = ""
        try:
            bruto = e.read().decode()
            detalhe = json.loads(bruto)["error"]["message"][:120]
        except Exception:
            detalhe = bruto[:120]
        return {"erro": f"{rotulo} HTTP {e.code}: {detalhe}"}
    except urllib.error.URLError as e:
        return {"erro": f"rede: {getattr(e, 'reason', e)}"}
    except Exception as e:
        return {"erro": str(e)[:120]}

    return {
        "answers": dados.get("answers", {}),
        "modelo": dados.get("model"),
        "provedor": rotulo,
        "usage": dados.get("usage", {}),
    }


def decidir(answers: dict, limiar: float = 0.85) -> dict:
    """Aplica a regra de roteamento sobre as respostas do Jev.

    Fica em codigo, e nao no prompt, porque e uma regra de negocio: mudar o
    limiar para um time mais conservador e editar um numero, sem reescrever
    instrucao nem retreinar nada.
    """
    dep = answers.get("department", {})
    escolha = dep.get("choice")
    confianca = dep.get("confidence", 0.0)
    probs = dep.get("probabilities", {})

    if escolha is None:
        return {"rota": "revisar", "motivo": "Jev nao devolveu escolha", "confianca": 0.0}

    urgente = answers.get("urgent", {}).get("noul", 0.0) >= 0.5
    frustracao = answers.get("frustration", {}).get("score")

    # O segundo colocado e o maior valor ENTRE OS OUTROS. Pegar o max() sem
    # excluir a escolhida devolveria a propria escolha de novo, que e
    # justamente a informacao que o revisor precisa.
    outras = {k: v for k, v in probs.items() if k != escolha}
    segundo = max(outras, key=outras.get) if outras else None

    if confianca >= limiar:
        rota = f"auto:{escolha}"
        motivo = f"confianca {confianca:.2f} >= {limiar}"
    else:
        rota = "revisar"
        motivo = (
            f"confianca {confianca:.2f} < {limiar} — segunda opcao "
            f"{segundo} a {outras.get(segundo, 0):.2f}"
            if segundo
            else f"confianca {confianca:.2f} < {limiar} — sem segunda opcao"
        )

    return {
        "rota": rota,
        "motivo": motivo,
        "departamento": escolha,
        "confianca": confianca,
        "probabilidades": probs,
        "segunda_opcao": segundo,
        "confianca_segunda": outras.get(segundo, 0.0) if segundo else 0.0,
        "urgente": urgente,
        "frustracao": frustracao,
        "auto_roteado": confianca >= limiar,
    }
