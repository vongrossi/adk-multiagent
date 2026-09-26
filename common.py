"""
Configuração compartilhada por todos os agentes do projeto.

O ADK insere a raiz do projeto no `sys.path` antes de carregar cada agente
(`google/adk/cli/utils/agent_loader.py:328`), então qualquer agente numa
subpasta consegue `from common import model`.

Por que centralizar: o `.env` já é único na raiz, mas o modelo não tinha
que ser. Sem este arquivo, cada agente criaria a sua própria instância
`Gemini` — o que funciona, mas repete a config de retry em todos os arquivos
e dificulta trocar o modelo num lugar só.

Este arquivo é o **único** lugar que decide qual backend roda. Nenhum agente
sabe se existe nuvem ou máquina local envolvida: todos fazem
`from common import model`. Trocar de backend, portanto, é mexer só aqui.
"""

import os

from dotenv import load_dotenv
from google.adk.models import FallbackModel
from google.adk.models.google_llm import Gemini
from google.genai import types

# Lê o .env da raiz do projeto. O próprio ADK também faz isso antes de
# carregar o agente, então esta linha é redundante em `adk run` / `adk web` —
# mas deixa o arquivo importável fora do CLI (testes, notebooks, python direto).
load_dotenv()

# Recebe: env MODEL. Envia: nome do modelo de todos os agentes.
# Padrão: o Flash Lite mais confiável que medi, não o mais novo. Ver o bloco
# MODEL_FALLBACK para o resultado do teste que motivou a escolha.
# MODEL="" deixa a cadeia só com o modelo local — nenhum request sai daqui.
MODEL = os.getenv("MODEL", "gemini-3.1-flash-lite")

# Segundo modelo da cadeia. A cota gratuita é de 20 requests por dia POR
# MODELO, então um nome diferente é um balde diferente — o principal estourar
# não afeta o fallback em nada.
#
# Medi os candidatos no que o Blogger realmente exige (disciplina do veredito
# `ok`/`retry` + tool calling), não só disponibilidade. Em 25/09/2026:
#   - gemini-3.1-flash-lite  : veredito e tool call corretos em todas as
#                              tentativas (5/5) — escolhido como principal
#   - gemini-3.5-flash-lite  : tool call ok, mas deu `retry` FALSO em outline
#                              válido 2/2 vezes, com motivo diferente a cada
#                              vez (nitpicking). Cai para 1o plano.
#   - gemini-flash-lite-latest: mesmo problema de retry falso (2/2) + é alias,
#                              pode mudar de modelo e de balde de cota
#   - gemma-4-26b-a4b-it     : devolve `*   Input: An` no lugar do veredito
#   - gemma-4-31b-it         : erro 500
#   - gemini-3.6-flash       : melhor no veredito, mas a cota estourou (429)
#   - gemini-2.5-flash-lite  : 404, retirado
#
# Um veredito que diz `retry` num draft bom é caro: queima uma das iterações do
# loop E uma geração inteira de cota. Por isso o principal é o que erra menos.
# Deixe vazio para desativar o segundo modelo na nuvem.
MODEL_FALLBACK = os.getenv("MODEL_FALLBACK", "gemini-3.5-flash-lite")

# `use_llamacpp=true` no .env acrescenta um modelo local llama.cpp como último
# recurso (ver llamacpp.py). O default é false, e o import fica dentro do `if`
# de propósito: assim o `httpx` do adaptador não vira requirement do caminho
# padrão, e o projeto continua funcionando com o `requirements.txt` atual.
USE_LLAMACPP = os.getenv("use_llamacpp", "false").lower() == "true"


def _gemini(nome: str, com_fallback: bool) -> Gemini:
    """Monta uma instância Gemini com a política de retry certa para a cadeia.

    O detalhe que importa: quando existe fallback, **429 não é retentado**.
    A API responde 429 com `retryDelay: 53s` quando a cota do dia acabou —
    insistir 8 vezes atrasaria mais de um minuto para, no fim, cair no mesmo
    erro. Sem o 429 na lista, a falha é imediata e o `FallbackModel` passa para
    o próximo modelo na hora. O 5xx continua sendo retentado, porque esse é erro
    passageiro e o próximo modelo talvez nem precise ser acionado.

    Sem fallback na cadeia, o 429 volta a ser retentado: aí não há para onde
    cair, e insistir é a única chance de o reset de cota chegar no meio.
    """
    return Gemini(
        model=nome,
        retry_options=types.HttpRetryOptions(
            attempts=8,
            initial_delay=3.0,
            max_delay=60.0,
            exp_base=2.0,
            jitter=1.0,
            http_status_codes=(
                [500, 502, 503, 504] if com_fallback else [429, 500, 502, 503, 504]
            ),
        ),
    )


# A cadeia, na ordem em que é tentada. Cada modelo é tentado UMA vez — retry é
# responsabilidade de cada modelo, não da cadeia (por isso o `attempts=8` acima
# convive com o FallbackModel sem repetir trabalho em dobro).
modelos: list = []

if MODEL:
    modelos.append(
        _gemini(MODEL, com_fallback=bool(MODEL_FALLBACK) or USE_LLAMACPP)
    )

if MODEL_FALLBACK and MODEL:
    modelos.append(_gemini(MODEL_FALLBACK, com_fallback=USE_LLAMACPP))

if USE_LLAMACPP:
    from llamacpp import LlamaCppLlm

    modelos.append(LlamaCppLlm())

if not modelos:
    raise RuntimeError(
        "Nenhum modelo configurado. Defina MODEL no .env, ou ligue "
        "use_llamacpp=true para usar o llama.cpp local."
    )

if len(modelos) == 1:
    # Com um modelo só não há para que cair: devolve a instância crua, sem a
    # camada do FallbackModel por cima. `MODEL=""` + `use_llamacpp=true` cai
    # aqui e roda 100% local.
    model = modelos[0]
else:
    model = FallbackModel(models=modelos)
