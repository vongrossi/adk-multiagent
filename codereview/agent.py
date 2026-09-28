"""
CodeReviewer — revisa código e aponta problema concreto, com linha.

=====================================================================
O QUE ELE FAZ, E POR QUE EXISTE
=====================================================================

O Blogger tem dois validadores, e nenhum dos dois olha código. Este é o
primeiro agente do projeto que le um arquivo de verdade do disco — os outros
só enxergam texto que o próprio modelo escreveu.

Ele segue o mesmo desenho do Blogger (gerador + veredito + loop) mas a
diferença está em como a evidência entra. O validador do Blogger julga
qualidade de escrita; aqui o revisor precisa *ler o arquivo*, então ele
precisa de uma tool. É o primeiro agente aqui com `tools=` numa função
Python, e o ponto do exercício.

=====================================================================
O QUE ELE DEVE PEGAR, EM ORDEM DE IMPORTANCIA
=====================================================================

Os três primeiros são bugs, não estilo:

  - **Segredo commitado.** Chave de API, token, senha, string de conexão com
    credencial. Isso é o achado que mais dói em repo público, e é o único que
    não pode ser "corrigido depois" — o segredo já vazou.
  - **Crash garantido.** `[]` acessado sem checar, `.get()` em None, `except:
    pass` em volta de uma escrita.
  - **Concorrência.** `requests` síncrono dentro de `async def`.

Depois disso vem o resto (injeção de SQL, TOCTOU, divisão por zero, recurso
não fechado). Estilo fica de fora de propósito: um revisor que reclama de
nomenclatura faz o autor ignorar os três primeiros achados.

=====================================================================
A TOOL `ler_arquivo` E DELIMITADA DE PROPÓSITO
=====================================================================

Ela lê o arquivo que o root passou, e **só aquele**. Se a tool aceitasse um
caminho arbitrário vindo do modelo, o agente viraria um leitor de
`~/.ssh/id_rsa` a cada revisão. A raiz e fixada no import; o modelo não escolhe
o que será lido.

O mesmo vale para `.env`: ele fica na lista de ignorados, então a tool recusa
com uma mensagem clara em vez de devolver segredo para o prompt.

=====================================================================
COMO RODAR

    adk run codereview common.py            # o caminho vem do comando
    adk web .                                # sem alvo: use `adk run`
"""

import ast
import os
import re
import sys

from common import model
from google.adk.agents import Agent, LoopAgent
from google.adk.tools import agent_tool
from google.genai import types

# Subcomandos do `adk` e o nome da pasta do agente NAO sao caminho de arquivo.
# Sem esta lista, `adk run codereview x.py` resolveria "codereview" como alvo
# se o arquivo nao existisse, e `adk web .` pegaria o ".".
_NAO_E_ARQUIVO = {
    "run", "web", "start", "api", "agent", "create", "help", "version",
    "codereview", "codereviewer", "adk",
}

# o que conta como "isto parece um caminho de codigo"
_EXTENSAO_CODIGO = {
    ".py", ".js", ".ts", ".tsx", ".jsx", ".go", ".rs", ".java", ".rb", ".php",
    ".c", ".h", ".cpp", ".sh", ".sql", ".env.example", ".yaml", ".yml", ".toml",
}


def _resolver_alvo(argv: list[str] | None = None) -> str | None:
    """Descobre o arquivo a revisar no argv do shell. Retorna None se nao houver.

    Funcao pura e separada do import de proposito: `sys.argv` no momento em que
    o ADK carrega o agente depende de quem chamou (o `adk run` passa o caminho
    como query, o `adk web` nao passa nada). Deixar a decisao numa funcao que
    recebe a lista torna ela testavel sem subir processo nenhum.

    Ordem de precedencia:
      1. `--file X` / `-f X` / `--file=X` — explicito, vence tudo.
      2. O ultimo token que exista no disco como arquivo.

    O filtro `os.path.isfile` e o que segura o resto: `adk web .` passa "." que
    e diretorio, e uma query em portugues como "revise o linkcheck" nao existe
    como arquivo. Nenhum dos dois vira alvo.
    """
    argv = list(sys.argv if argv is None else argv)

    for i, token in enumerate(argv):
        if token in ("--file", "-f"):
            if i + 1 < len(argv):
                return os.path.expanduser(argv[i + 1])
        if token.startswith("--file="):
            return os.path.expanduser(token.split("=", 1)[1])

    for token in reversed(argv):
        if token.startswith("-"):
            continue
        nome = os.path.basename(token)
        if nome in _NAO_E_ARQUIVO:
            continue
        if os.path.isfile(token):
            return os.path.expanduser(token)
        # token nao existe: so aceitaria se parecer codigo, para nao engolir
        # "revise o modulo de auth" como caminho
        if os.path.splitext(nome)[1].lower() in _EXTENSAO_CODIGO:
            return os.path.expanduser(token)
    return None


ARQUIVO = _resolver_alvo()  # fixado no import; a tool le SO este caminho
TAMANHO_MAX = 60_000

# Raiz do repositorio, derivada da posicao deste arquivo e nao do `cwd`:
# `adk run codereview` pode ser disparado de qualquer diretorio, e um `cwd`
# errado tornaria o confinamento inutil ou laxo demais.
#
# `realpath` em ambos resolve symlink, entao `ln -s ~/.ssh/id_rsa link.py`
# nao contorna a fronteira.
RAIZ = os.path.realpath(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))
IGNORAR = {".env", "id_rsa", "id_ed25519", ".pem", ".key", "credentials.json"}

# Heuristicas Cheap que rodam em Python antes do modelo. Nao substituem a
# revisao, mas pegam o que tem padrao exato — e pagam a mesma execucao, sem
# gastar cota. Um segredo commitado nao pode depender de o modelo reparar.
PADROES = [
    ("segredo", re.compile(
        r"(?i)(api[_-]?key|secret|passwd|password|token|private[_-]?key)\s*[:=]\s*"
        r"['\"][A-Za-z0-9_\-]{16,}['\"]")),
    ("conexao com credencial", re.compile(
        r"(?i)(postgres|mysql|mongodb|redis|amqp)://[^\s:@/]+:[^\s:@/]+@")),
    ("log de segredo", re.compile(
        r"(?i)print\(\s*['\"].*(token|senha|password|secret)")),
]

REGRAS_AST = [
    # except genérico que engole erro: o sintoma clássico de bug silencioso
    ("trata toda excecao", "except:", "captura generica; engolir erro aqui "
     "esconde o bug ate virar sintoma em outro lugar"),
]


def _linha_de(texto: str, alvo: str) -> int:
    for n, linha in enumerate(texto.splitlines(), 1):
        if alvo in linha:
            return n
    return 0


def _heuristicas(texto: str) -> list[dict]:
    achados = []
    for rotulo, rx in PADROES:
        for m in rx.finditer(texto):
            linha = texto.count("\n", 0, m.start()) + 1
            achados.append({
                "tipo": rotulo,
                "linha": linha,
                "trecho": m.group(0)[:80],
                "achado_por": "heuristica",
            })
    try:
        arvore = ast.parse(texto)
    except SyntaxError as e:
        return achados + [{
            "tipo": "erro de sintaxe", "linha": e.lineno or 0,
            "trecho": str(e.msg)[:80], "achado_por": "ast",
        }]
    for no in ast.walk(arvore):
        if isinstance(no, ast.Try):
            for handler in no.handlers:
                if handler.type is None:
                    achados.append({
                        "tipo": "engole excecao",
                        "linha": handler.lineno,
                        "trecho": "except:",
                        "achado_por": "ast",
                    })
        # subscrito de None e o crash mais comum em Python e nao aparece em
        # nenhum padrao de texto
        if isinstance(no, ast.Subscript) and isinstance(no.value, ast.Name):
            if no.value.id in {"session", "sessao", "client", "ctx", "context"}:
                achados.append({
                    "tipo": "acesso sem checar None",
                    "linha": no.lineno,
                    "trecho": no.value.id + "[...]",
                    "achado_por": "ast",
                })
    return achados


def ler_arquivo() -> str:
    """Le o arquivo indicado em `--file` no comando de execucao.

    Nao recebe caminho do modelo: a tool so le o arquivo que o root ja fixou.
    """
    if not ARQUIVO:
        return (
            "Nenhum arquivo indicado, entao nao ha o que revisar. "
            "Rode `adk run codereview common.py` — o caminho vem do "
            "comando, nao de mim. Em `adk web` nao existe caminho de "
            "arquivo: use `adk run`."
        )
    nome = os.path.basename(ARQUIVO)
    if nome in IGNORAR or nome.endswith((".env", ".pem", ".key")):
        return (
            f"Arquivo '{nome}' esta na lista de ignorados: revisao nao le "
            "segredos. Revise o codigo que consome a variavel, em vez dela."
        )
    caminho = os.path.realpath(os.path.expanduser(ARQUIVO))

    # Confinamento a raiz do repo. O caminho vem do `argv`, nao do modelo, o
    # que ja reduz o risco: o operador e quem roda. Mas `adk run codereview
    # --file ~/.ssh/id_rsa` lia a chave privada e a mandava para o modelo, e
    # um revisor de segredos que le segredos e contradicao. O `.gitignore`
    # impede o commit; nao impede a leitura.
    if not caminho.startswith(RAIZ + os.sep) and caminho != RAIZ:
        return (
            f"Arquivo fora do repositorio ({nome}). A revisao so le codigo "
            f"deste projeto. Use um caminho dentro de {RAIZ}."
        )
    if not os.path.isfile(caminho):
        return f"Arquivo nao encontrado: {caminho}"
    if os.path.getsize(caminho) > TAMANHO_MAX:
        return (
            f"Arquivo tem {os.path.getsize(caminho) // 1024}KB, acima do limite "
            f"de {TAMANHO_MAX // 1024}KB. Revise um trecho menor."
        )
    with open(caminho, encoding="utf-8", errors="replace") as fh:
        texto = fh.read()
    achados = _heuristicas(texto)
    aviso = (
        f"\n\n## Achados automaticos (padroes exatos, {len(achados)})\n"
        "Confirme cada um no codigo antes de reportar. Pode haver falso positivo:\n\n"
        + "\n".join(
            f"- linha {a['linha']} — {a['tipo']}: {a['trecho']!r}" for a in achados
        )
        if achados
        else "\n\n## Achados automaticos\nNenhum padrao exato encontrado. Isso NAO "
             "significa que o arquivo esteja limpo."
    )
    return f"```{os.path.splitext(nome)[1][1:]}\n{texto}\n```{aviso}"


def escalate_when_approved(key: str):
    def callback(callback_context) -> "types.Content | None":
        if str(callback_context.state.get(key, "")).strip().lower().startswith("ok"):
            callback_context.actions.escalate = True
            return types.Content(
                role="model",
                parts=[types.Part(
                    text=f"{callback_context.agent_name}: revisao aprovada, "
                    "saindo do loop."
                )],
            )
        return None

    return callback


# --Sub-Agent 1: revisor ---------------------------------------------------
# Recebe de: state["code_review"] (achados da iteracao anterior).
# Envia para: state["review_findings"] (JSON).
code_reviewer = Agent(
    name="CodeReviewer",
    model=model,
    description="Finds concrete bugs and security problems in a source file.",
    instruction="""
    Read the file with the `ler_arquivo` tool, then review it.

    Report ONLY problems you can point at a specific line for, in this
    priority order:

    1. **Segredo commitado** — API key, token, senha, string de conexao com
       credencial. Say this loudly: no repo publico o segredo ja vazou e
       precisa ser rotacionado, nao so apagado.
    2. **Crash garantido** — `[]` sem checar, atributo de None, `except: pass`
       em volta de escrita, arquivo fechado sem `with`.
    3. **Concorrencia** — `requests`/`time.sleep`/IO bloqueante dentro de
       `async def`; uso de `threading` onde `asyncio` resolve.
    4. **Correcao** — off-by-one, mutacao de lista durante iteracao, comparacao
       com `is` em literal, excecao larga demais.

    Out of scope, on purpose: naming, formatting, comment style, type hints.
    A review full of style nits trains the reader to ignore it.

    Return ONLY a JSON array. Each element:

    {{"severidade": "critico|alto|medio|baixo",
      "linha": 42,
      "categoria": "segredo|crash|concorrencia|correcao",
      "problema": "one sentence",
      "correcao": "one concrete suggestion"}}

    Empty array [] if there is nothing concrete. Do not invent problems to fill
    the array. Quote the real line number you saw.

    ## Previous findings

    {code_review?}
    """,
    output_key="review_findings",
    tools=[ler_arquivo],
)

# Nao consome tool: so confere se os achados ancoram em linha real.
review_validator = Agent(
    name="ReviewValidator",
    model=model,
    description="Checks that every finding points at a real line and matters.",
    instruction="""
    Audit the review findings below. The code was already read; you do not need
    to read it again.

    <findings>
    {review_findings?}
    </findings>

    Reject a finding when any of these is true:

    - it points at a line that does not exist or is unrelated to the problem
    - it is about style, naming, formatting or missing type hints
    - it invents a problem that the code does not actually have
    - it is a duplicate of another finding

    Keep everything that is a real defect, and say nothing about them.

    Respond with EXACTLY one of these two forms and nothing else:
    - `ok`                                      (if every finding survives)
    - `retry: <comma separated list of what to drop and why>`""",
    output_key="code_review",
    after_agent_callback=escalate_when_approved("code_review"),
)

robust_review = LoopAgent(
    name="RobustCodeReview",
    description="Retries the review until findings are verified.",
    sub_agents=[code_reviewer, review_validator],
    max_iterations=3,
)

review_tool = agent_tool.AgentTool(agent=robust_review)

root_agent = Agent(
    name="CodeReviewer",
    model=model,
    description="Reviews one source file and returns concrete findings as JSON.",
    instruction=f"""
    The file to review is: {ARQUIVO or "(nao indicado)"}

    Call the review tool. Then present the findings sorted by severity, in
    Markdown, one section per finding: severity, `arquivo:linha`, what breaks,
    and the fix. If the array came back empty, say the file has no concrete
    problem that survived validation — do not invent one to be helpful.

    Always finish with a one-line verdict: `publicado: sim` if there is a
    `critico` or `alto` finding, `publicado: sim, com ajustes` otherwise.
    """,
    tools=[review_tool],
)
