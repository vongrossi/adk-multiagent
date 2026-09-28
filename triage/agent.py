"""
Triage — le uma mensagem de caixa de entrada e decide para onde ela vai.

=====================================================================
O PROBLEMA QUE ESTE AGENTE RESOLVE
=====================================================================

Uma caixa de entrada de suporte recebe tudo junto: "minha fatura veio errada",
"a API da plataforma esta dando 500", "quanto custa o plano enterprise?". O trabalho de
triagem e decidir a fila. E a primeira decisao de um agente, a mais barata e a
que menos se quer errar: classificar "quero um reembolso" como `sales` custa o
cliente esperando ate sexta.

=====================================================================
POR QUE O JEV, E POR QUE COMO TOOL
=====================================================================

Ver `triage/jev.py` para a analise completa. O resumo:

O Jev (TypeSafe AI, "System One Model") nao gera texto. Ele devolve decisao
tipada com probabilidade e confianca. Isso resolve exatamente o problema da
triagem, porque triagem e a unica tarefa em que "quanto o modelo tem certeza"
importa mais do que "como ele escreve".

Entao o Jev **nao substitui** o modelo do pipeline. Ele entra como tool:

    TriageAgent (gemini-3.1-flash-lite)
      └─ classificar_ticket  →  Jev  →  {choice, confidence, probabilities}
                                     ↓
                          decide()  em Python puro
                                     ↓
                          auto:{fila}  ou  revisar

A divisoria importa. A *classificacao* e uma decisao de machine, e o Jev e
melhor nisso do que um LLM de texto. A *apresentacao* — explicar ao usuario
pra onde foi a mensagem e por que — e linguagem natural, e o modelo de texto e
melhor nisso. Forcar um deles a fazer o trabalho do outro piora os dois.

O que o LLM de texto nao tem, e o Jev tem: um numero. `confidence: 0.93` e
verificavel, comparavel, e pode virar regra. "O modelo pareceu confiante" nao e
uma regra que voce consegue auditar num ticket contestado.

=====================================================================
A REGRA DE ROTEAMENTO ESTA EM CODIGO, NAO NO PROMPT
=====================================================================

`jev.decidir()` compara a confianca com um limiar e devolve `auto:{fila}` ou
`revisar`. Fica em Python por tres motivos:

  1. **Auditoria.** Um ticket contestado leva a "por que foi para billing?" e a
     resposta esta no `motivo`, com o numero exato. Se a regra estivesse no
     prompt, a resposta seria um novo palpite do mesmo modelo.
  2. **Ajuste sem custo.** Mudar a politica de triagem de 0.85 para 0.95 e
     editar um numero. Reescrever prompt e mexer em comportamento com custo
     por request.
  3. **Testabilidade.** `decidir()` e uma funcao pura; o teste roda sem API.

=====================================================================
DEGRADACAO
=====================================================================

Sem `OPENROUTER_API_KEY` nem `TYPESAFE_API_KEY`, o Jev e pulado e o agente
classifica pelo modelo de
texto, avisando explicitamente que a rota foi fallback e que a confianca
reportada nao e calibrada. A diferenca importa: a resposta fallback diz "esta e
uma estimativa", a resposta Jev traz a distribuicao completa. Misturar as duas
sem avisar seria pior que nao ter o Jev.
"""

from google.adk.agents import Agent
from google.adk.tools import FunctionTool

from common import model

from triage import jev

LIMIAR = 0.85


def classificar_ticket(mensagem: str) -> dict:
    """Classify a support message and decide which team queue it belongs to.

    Args:
        mensagem: Raw message text, exactly as it arrived in the inbox. Pass it
            verbatim; do not summarize or clean it up, because the routing is
            only as good as the text that gets classified.

    Returns:
        A dict with `rota` (`auto:{team}` or `revisar`), the chosen
        `departamento`, the `confianca` and full `probabilidades`, plus the
        `motivo` behind the decision. Contains `erro` and `modo: fallback`
        when Jev is not configured or unreachable.
    """
    if not (mensagem or "").strip():
        return {"erro": "mensagem vazia — nao ha o que classificar"}

    if not jev.disponivel():
        return {
            "erro": "nenhuma chave de Jev no .env: defina OPENROUTER_API_KEY "
            "ou TYPESAFE_API_KEY",
            "modo": "fallback",
            "aviso": (
                "Sem o Jev a classificacao fica sem confianca calibrada. "
                "Adicione OPENROUTER_API_KEY (ou TYPESAFE_API_KEY) ao .env "
                "para rotear por confianca."
            ),
        }

    bruto = jev.classificar(mensagem)
    if "erro" in bruto:
        return {"erro": bruto["erro"], "modo": "fallback"}

    decisao = jev.decidir(bruto.get("answers", {}), LIMIAR)
    decisao["modo"] = "jev"
    decisao["modelo"] = bruto.get("modelo")
    decisao["usage"] = bruto.get("usage")
    return decisao


classificar_tool = FunctionTool(classificar_ticket)

root_agent = Agent(
    model=model,
    name="TicketTriage",
    description="Classifies a support message into a team queue using calibrated confidence.",
    instruction="""
    You route a single support message to the right team, and you explain the
    routing. You never answer the customer's problem — routing is your whole
    job.

    ## What to do

    1. Call the `classificar_ticket` tool with the message, verbatim. Do not
       summarize it, do not clean it up, and do not drop parts of it. The
       classification is only as good as the text you pass; paraphrasing a
       complaint into a polite summary is the fastest way to route it wrong.

    2. Report the result in this shape:

       **Route:** `auto:billing` or `revisar`
       **Department:** <department>
       **Confidence:** <0.00-1.00>
       **Urgency:** yes / no
       **Frustration:** <score, as a word>
       **Why:** <the `motivo`, plus the runner-up category and its probability>

    3. If the tool returns `modo: fallback`, say plainly that the ticket was
       NOT routed, that there is no confidence, and that neither
       `OPENROUTER_API_KEY` nor `TYPESAFE_API_KEY` is configured. Do not
       classify it yourself from the text and do not
       invent a number. A wrong route with fake confidence is worse than no
       route: the human trusts the number and stops reading the ticket.

    4. If the route is `revisar`, add one sentence saying a human should look
       at it and name the two departments that were closest. A close call is
       the interesting part of a ticket, so do not hide it behind "review".

    ## Reporting rules

    Never invent a confidence. Only the number the tool returned is real, and
    if the tool failed there is no number to report — say that it failed.

    The department, the probabilities, and the routing rule are produced by the
    tool. Restate them; do not re-decide them yourself. If you disagree with the
    routing, say so in a separate line under **My read**, after reporting what
    the tool decided.
    """,
    tools=[classificar_tool],
)
