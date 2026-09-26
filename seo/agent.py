"""
SeoAgent — transforma um post em metadados prontos para o Blogger.

=====================================================================
O QUE ELE FAZ, E POR QUE EXISTE
=====================================================================

O pipeline do Blogger produz um `blog_post` em Markdown e para aí. Publicar
no Blogger API exige, além do corpo, um punhado de campos que o Writer não
tem por que inventar: título com limite de caracteres, `meta description`
(ela aparece no snippet do Google), slug, tags e um alt-text para a imagem de
capa.

Esses campos têm limites duros e mecânicos. O Blogger corta título acima de
100 caracteres, o Google só mostra ~155 da description, e slug não pode ter
espaço nem acento. Delegar isso ao modelo é pedir para ele errar o
especificado — mesmo um bom modelo erra o limite de caractere às vezes.

A saída deste agente é um JSON validado contra esses limites, com a validação
em Python, não no prompt. Se o modelo estourar um limite, o loop reprocessa.

=====================================================================
POR QUE JSON E NÃO TEXTO
=====================================================================

Todo agente anterior deste projeto devolve texto livre, porque texto livre é
fácil de ler num `adk web`. Metadados são o contrário: o próximo passo é
publicar, e publicar precisa de campos com nome exato. Texto livre aqui
significa parser frágil.

Por isso a resposta vai em JSON e o agente valida o schema. A alternativa
seria `response_schema` do Gemini, que força o formato no lado do modelo — mas
isso trava o backend local (llama.cpp) e o fallback, porque a emulação de
JSON estruturado no `LlamaCppLlm` não existe. Validação em Python funciona em
todos os backends e dá a mesma garantia com mais uma chance de corrigir.

=====================================================================
COMO RODAR

    adk run seo "beneficios do pacote pub"       # gera do zero
    adk run seo "beneficios do pacote pub" < post.md   # a partir de um post
"""

import json

from common import model
from google.adk.agents import Agent, LoopAgent
from google.adk.tools import agent_tool
from google.genai import types

# Limites do Blogger API e do Google. Fonte: developers.google.com/blogger.
LIMITE_TITULO = 100
LIMITE_DESCRIPTION = 155
LIMITE_SLUG = 50
LIMITE_TAGS = 5

# Tags que não ajudam: o Blogger já indexa pelo conteúdo, e repetir o próprio
# título como tag só ocupa espaço no limite de 5.
TAG_PROIBIDA = {"blog", "post", "artigo", "postagem", "texto", "tutorial"}

# o slug precisa ter no maximo 4 palavras, para nao virar uma frase
def _slug_valido(slug: str) -> bool:
    return (
        bool(slug)
        and len(slug) <= LIMITE_SLUG
        and all(c.isalnum() or c == "-" for c in slug)
        and not slug.startswith("-")
        and not slug.endswith("-")
        and "--" not in slug
    )


def validar_metadados(dados: dict) -> list[str]:
    """Confere o JSON contra os limites do Blogger. Devolve a lista de erros.

    Fica em Python, nao no prompt: é aritmética de caractere e sanitizacao de
    acento, coisa que o modelo erra de forma imprevisível.
    """
    erros = []
    if not isinstance(dados, dict):
        return ["a resposta nao e um objeto JSON"]

    titulo = str(dados.get("titulo", ""))
    if not titulo.strip():
        erros.append("titulo vazio")
    elif len(titulo) > LIMITE_TITULO:
        erros.append(f"titulo com {len(titulo)} caracteres, o limite e {LIMITE_TITULO}")

    desc = str(dados.get("meta_description", ""))
    if not desc.strip():
        erros.append("meta_description vazia")
    elif len(desc) > LIMITE_DESCRIPTION:
        erros.append(
            f"meta_description com {len(desc)} caracteres, o limite e {LIMITE_DESCRIPTION}"
        )

    slug = str(dados.get("slug", ""))
    if not _slug_valido(slug):
        erros.append(
            f"slug invalido: {slug!r} (use minusculas, sem acento, so letras/"
            f"numeros/hifen, ate {LIMITE_SLUG} caracteres)"
        )

    tags = dados.get("tags")
    if not isinstance(tags, list) or not tags:
        erros.append("tags deve ser uma lista nao vazia")
    else:
        if len(tags) > LIMITE_TAGS:
            erros.append(f"{len(tags)} tags, o limite e {LIMITE_TAGS}")
        if len({str(t).lower() for t in tags}) != len(tags):
            erros.append("tags repetidas")
        for t in tags:
            if str(t).lower() in TAG_PROIBIDA:
                erros.append(f"tag generica e inutil: {t!r}")

    if not str(dados.get("alt_text", "")).strip():
        erros.append("alt_text vazio")
    return erros


def escalate_when_valid(verdict_key: str):
    """Sai do loop quando o JSON passou na validacao de Python."""

    def callback(callback_context) -> "types.Content | None":
        if str(callback_context.state.get(verdict_key, "")).strip().lower().startswith("ok"):
            callback_context.actions.escalate = True
            return types.Content(
                role="model",
                parts=[
                    types.Part(
                        text=f"{callback_context.agent_name}: metadados válidos, "
                        "saindo do loop."
                    )
                ],
            )
        return None

    return callback


# --Sub-Agent 1: Gerador ---------------------------------------------------
# Recebe de: {seo_validation?} (veredito anterior) e {blog_post?} / {post?}
# Envia para: state["seo_metadata"] (JSON cru).
seo_generator = Agent(
    name="SeoGenerator",
    model=model,
    description="Generates Blogger-ready title, description, slug and tags.",
    instruction="""
    Produce publication metadata for the post below. Return ONLY a JSON object,
    no Markdown fence, no text before or after.

    {{
      "titulo": "...",          // max {LIMITE_TITULO} chars
      "meta_description": "...",// max {LIMITE_DESCRIPTION} chars
      "slug": "...",            // lowercase, no accents, only a-z 0-9 and
                                // hyphens, max {LIMITE_SLUG} chars
      "tags": ["..."],          // at most {LIMITE_TAGS} specific tags
      "alt_text": "..."         // describes the cover image, for accessibility
    }}

    <post>
    {blog_post?}
    </post>

    <topic>
    {topic?}
    </topic>

    ## Rules that are checked mechanically afterwards

    - `titulo`: clickable and specific. The limit is HARD, count the characters.
    - `meta_description`: one or two sentences, written to be read in a Google
      snippet. It must make sense standalone, without the post.
    - `slug`: ASCII only. Strip accents ("configuração" -> "configuracao"),
      replace spaces with hyphens, never start or end with a hyphen.
    - `tags`: at most {LIMITE_TAGS}, each 1-3 words, all specific. Never use
      generic ones like "blog", "post", "artigo" or the post title itself.
    - `alt_text`: describe the picture, do not repeat the title.

    ## Review loop

    Validation errors from the previous attempt:

    {seo_validation?}

    - EMPTY: this is the first pass.
    - "retry": fix exactly the listed fields. Keep the ones that passed.
    - "ok": re-emit the same JSON unchanged.
    """,
    output_key="seo_metadata",
)

# Nao consome model alem do veredito: so valida. Recebe de: state["seo_metadata"].
seo_validator = Agent(
    name="SeoValidator",
    model=model,
    description="Checks the JSON against the hard Blogger limits.",
    instruction="""
    Validate the metadata JSON below as a publication-readiness gate.

    <metadata>
    {seo_metadata?}
    </metadata>

    Check that the response is a single JSON object (no fence, no prose around
    it) with exactly the keys: titulo, meta_description, slug, tags, alt_text.

    Check it is actually about THIS post and not generic filler.

    Respond with EXACTLY one of these two forms and nothing else:
    - `ok`                                      (if it is publishable as is)
    - `retry: <comma separated list of problems>` (if it is not)""",
    output_key="seo_validation",
    after_agent_callback=escalate_when_valid("seo_validation"),
)

robust_seo = LoopAgent(
    name="RobustSeo",
    description="Retries metadata generation until it passes the limits.",
    sub_agents=[seo_generator, seo_validator],
    max_iterations=3,
)

seo_tool = agent_tool.AgentTool(agent=robust_seo)

root_agent = Agent(
    name="SeoAgent",
    model=model,
    description="Gates a blog post with title, description, slug, tags and alt text.",
    instruction="""
    If the user gives a topic, call the seo tool to generate metadata for it.
    If the user gives a full post, pass the post content so the metadata
    matches it instead of guessing from a one-line topic.

    After the tool returns, parse the JSON it wrote to `seo_metadata` and show
    the user the five fields in a readable list. If any field is empty or the
    JSON failed to parse, say so explicitly instead of filling it in yourself.
    """,
    tools=[seo_tool],
)


def metadados_prontos(state: dict) -> tuple[dict | None, list[str]]:
    """Le `seo_metadata` do state e valida em Python. Retorna (dados, erros).

    Fica disponivel para o agente do Blogger usar antes de publicar: e a mesma
    validacao que roda no loop, mas sem chamar o modelo.
    """
    bruto = state.get("seo_metadata")
    if not bruto:
        return None, ["state nao tem seo_metadata"]
    try:
        dados = json.loads(str(bruto))
    except json.JSONDecodeError as e:
        return None, [f"JSON invalido: {e}"]
    return dados, validar_metadados(dados)
