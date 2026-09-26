"""
Researcher — exemplo de um segundo agente no mesmo ambiente.

Mostra o mínimo necessário para adicionar um agente novo: uma pasta com um
agent.py que expoe `root_agent` e importa o modelo compartilhado de
`common.py`. Nenhuma outra configuracao muda — nem a venv, nem o
requirements.txt, nem o .env da raiz.

Para rodar:
    adk run researcher "k8s vs nomad"
    adk web .        (aparece no dropdown junto com o Blogger)
"""

from common import model
from google.adk.agents import Agent

# Funcao:     pesquisa um tema e devolve um resumo enxuto com fontes.
# Recebe de:  a mensagem do usuario (o tema), direta no chat.
# Envia para: o state, via output_key="research_notes" — a chave fica
#             disponivel para outros agentes da mesma invocacao.
# Modelo:     vem de common.py, compartilhado com todos os outros agentes.
root_agent = Agent(
    name="Researcher",
    model=model,
    description="Researches a topic and returns a short summary with sources.",
    instruction="""
    Research the topic the user gives you and return, in Markdown:

    - A 3-line summary
    - 5 key points as bullets
    - 3 sources as links

    Be concise. No preamble, no "here is the summary" — just the content.
    """,
    output_key="research_notes",
)
