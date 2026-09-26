"""
RagAgent — responde perguntas sobre os SEUS documentos, com citação.

=====================================================================
O QUE ELE FAZ, E POR QUE EXISTE
=====================================================================

Os outros agentes geram texto a partir do conhecimento do modelo. Esse é o
primeiro do projeto que consulta uma base externa: ele indexa arquivos do
seu disco, e a partir dai responde citando de onde tirou cada afirmação.

A diferença importa quando a resposta precisa estar certa. O modelo tem a
documentação do Dart na training data, mas não tem o código do SEU projeto, e
não sabe o que mudou na sua versão. Aqui a resposta vem do seu texto, e cada
fragmao vem com o arquivo e a linha de origem — dá para conferir.

=====================================================================
INDEXAR E PERGUNTAR SAO DOIS COMANDOS
=====================================================================

Indexar é um passo separado, e de propósito. Embedding custa cota, entao
indexar 200 arquivos toda vez que alguem pergunta seria desperdicio. O fluxo é:

    adk run rag indexar ./docs        # uma vez, ou quando os docs mudam
    adk run rag "como configuro X"     # a partir dai, sem reindexar

O agente é o mesmo; a palavra "indexar" no primeiro argumento escolhe o
caminho. Nao ha comando separado porque o ADK so expoe um `root_agent` por
pasta, e um segundo `root_agent` na mesma pasta sobrescreve o primeiro.

=====================================================================
ONDE FICA O INDICE
=====================================================================

`rag/.indice/` — um diretorio PersistentClient do ChromaDB. Ele guarda os
embeddings, entao:

  - Delete sem querer e voce paga para reindexar tudo.
  - Nao versiona no git: `.gitignore` ja cobre.
  - A colecao e por origem, nao global. Indexar `./docs` e depois `./codigo`
    nao se misturam, porque embusca diferente em colecao diferente daria
    resposta sem contexto.

Um arquivo de 30MB vira um chunk e enche a cota de embedding de uma vez. O
limite por arquivo esta em `TAMANHO_MAX_BYTES`, e o que for grande demais e
pulado com aviso em vez de travar o indexing inteiro.

=====================================================================
COMO OS CHUNKS SÃO FEITOS
=====================================================================

Por cabeçalho Markdown, nao por contagem de caracteres. Um guia de 3000
palavras sobre configuracao, com 12 `##`, quebrado em 12 pedaços tem sentido;
quebrado em 3 pedaços de 1000 palavras tem 3 chunks que ninguno embeddor
indexa bem — cada um mistura assunto. Busca densa degrada muito aqui.

O mesmo vale para chunk de codigo: o limite de 40 linhas divide por
funcao/classe, que e onde o significado comeca e termina.

=====================================================================
COMO RODAR

    adk run rag indexar ./docs
    adk run rag "como eu configuro o cache do pip"

Os embeddings usam o modelo de embedding do Google, que e separado do modelo
de texto e tem cota propria. Indexar 100 arquivos de 200 palavras custa ~100
unidades de embedding, nao requests de texto.
"""

import os
import re
import unicodedata

import chromadb
from common import model
from google.adk.agents import Agent
from google.genai import types

AQUI = os.path.dirname(os.path.abspath(__file__))
INDICE = os.path.join(AQUI, ".indice")
# Medido nesta conta: `text-embedding-004` devolve 404 NOT_FOUND, tanto com
# quanto sem o prefixo `models/`. `gemini-embedding-001` responde com 3072
# dimensoes. O prefixo `models/` e opcional nos dois casos.
EMBED_MODEL = "models/gemini-embedding-001"

EXTENSOES = {".md", ".txt", ".py", ".js", ".ts", ".json", ".yaml", ".yml", ".toml"}
TAMANHO_MAX_BYTES = 200_000
CHUNK_LINHAS_CODIGO = 40
CHUNK_CARACTERES_TEXTO = 1800
CHUNK_SOBREPOSTA = 150

_CLIENT = None


def cliente():
    """Abre (ou cria) o indice persistente. Criado sob demanda, nao no import."""
    global _CLIENT
    if _CLIENT is None:
        os.makedirs(INDICE, exist_ok=True)
        _CLIENT = chromadb.PersistentClient(path=INDICE)
    return _CLIENT


def _normalizar(texto: str) -> str:
    return unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode()


def _cabecalhos(linhas: list[str]) -> list[tuple[int, str]]:
    return [(i, l.lstrip("#").strip()) for i, l in enumerate(linhas) if l.lstrip().startswith("#")]


def dividir(texto: str, caminho: str) -> list[str]:
    """Fatia um arquivo em trechos. Markdown por secao, codigo por bloco de 40 linhas."""
    ext = os.path.splitext(caminho)[1].lower()
    if ext in (".py", ".js", ".ts"):
        return _dividir_codigo(texto) or [texto]
    if ext in (".md", ".txt"):
        return _dividir_markdown(texto) or [texto]
    return [texto[:CHUNK_CARACTERES_TEXTO]]


def _dividir_codigo(texto: str) -> list[str]:
    linhas = texto.splitlines(keepends=True)
    partes, atual = [], []
    for linha in linhas:
        atual.append(linha)
        if len(atual) >= CHUNK_LINHAS_CODIGO:
            partes.append("".join(atual))
            atual = []
    if atual:
        partes.append("".join(atual))
    return partes


def _dividir_markdown(texto: str) -> list[str]:
    """Uma parte por cabecalho. O titulo entra no proprio trecho, para ele
    fazer sentido sozinho quando aparecer isolado na resposta."""
    linhas = texto.splitlines(keepends=True)
    cortes = _cabecalhos(linhas)
    if not cortes:
        return [texto]
    if cortes[0][0] > 0:
        cortes = [(0, "")] + cortes

    partes = []
    for n, (i, titulo) in enumerate(cortes):
        fim = cortes[n + 1][0] if n + 1 < len(cortes) else len(linhas)
        corpo = "".join(linhas[i:fim]).strip()
        # Corta so o que NAO tem conteudo. Uma secao curta e valida — "## Deploy"
        # com uma frase — precisa entrar no indice, senao o agente nunca
        # encontra aquele assunto. Descartar aqui e perda silenciosa: o
        # arquivo fica "indexado" mas sem o pedaco que o usuario procura.
        conteudo = "\n".join(
            l for l in corpo.splitlines() if not l.lstrip().startswith("#")
        ).strip()
        if not conteudo:
            continue
        # O titulo e o da secao ATUAL, nunca o da anterior: usar o anterior
        # rotula o trecho com o assunto errado quando ele volta na busca.
        prefixo = f"## {titulo}\n\n" if titulo else ""
        partes.append((prefixo + corpo)[:CHUNK_CARACTERES_TEXTO])
    return partes


def _embed(trechos: list[str]) -> list[list[float]]:
    """Chama o modelo de embedding do Google. Cota separada da de texto."""
    from google import genai

    api = genai.Client(api_key=os.environ["GOOGLE_API_KEY"])
    saida = []
    for i in range(0, len(trechos), 100):  # limite do endpoint por request
        res = api.models.embed_content(model=EMBED_MODEL, contents=trechos[i : i + 100])
        saida.extend(e.values for e in res.embeddings)
    return saida


def _nome_colecao(rotulo: str) -> str:
    """Nome de colecao derivado do alvo indexado.

    ASCII e minusculo por exigencia do ChromaDB: um `ç` ou um `/` no caminho
    faria `get_or_create_collection` recusar o nome.
    """
    limpo = _normalizar(os.path.basename(rotulo.rstrip(os.sep)) or "raiz").lower()
    limpo = re.sub(r"[^a-z0-9_]+", "_", limpo).strip("_")[:40]
    return f"rag_{limpo or 'raiz'}"


def indexar(alvo: str) -> str:
    """Indexa uma pasta (ou um arquivo). Devolve um relatorio legivel."""
    raiz = os.path.abspath(alvo)
    if os.path.isfile(raiz):
        arquivos = [raiz]
        rotulo = os.path.dirname(raiz) or "."
    else:
        arquivos = [
            os.path.join(d, f)
            for d, _, fs in os.walk(raiz)
            for f in fs
            if os.path.splitext(f)[1].lower() in EXTENSOES
        ]
        rotulo = raiz
    arquivos.sort()
    if not arquivos:
        return f"Nenhum arquivo indexavel em {alvo} (aceito: {', '.join(sorted(EXTENSOES))})"

    docs, metadados, ids, pulados = [], [], [], []
    for caminho in arquivos:
        if any(p in caminho for p in (".git", "node_modules", ".indice", "__pycache__")):
            continue
        tamanho = os.path.getsize(caminho)
        if tamanho > TAMANHO_MAX_BYTES:
            pulados.append(f"{os.path.relpath(caminho, rotulo)} ({tamanho // 1024}KB)")
            continue
        try:
            with open(caminho, encoding="utf-8") as fh:
                texto = fh.read()
        except (UnicodeDecodeError, OSError):
            continue
        rel = os.path.relpath(caminho, rotulo)
        for n, parte in enumerate(dividir(texto, caminho)):
            docs.append(parte)
            metadados.append({"fonte": rel, "trecho": n})
            ids.append(f"{_normalizar(rel).replace('/', '_')}#{n}")

    if not docs:
        return f"Encontrei {len(arquivos)} arquivo(s), mas nenhum tinha texto util."

    colecao = cliente().get_or_create_collection(name=_nome_colecao(rotulo))
    # Recarrega do zero: indexar de novo um arquivo editado precisa apagar o
    # trecho antigo, senao os dois versos ficam no indice e a busca devolve o
    # conteudo antigo junto do novo.
    try:
        colecao.delete(where={"fonte": {"$in": sorted({m["fonte"] for m in metadados})}})
    except Exception:
        pass
    colecao.add(ids=ids, documents=docs, metadatas=metadados, embeddings=_embed(docs))

    linhas = [
        f"Indexados {len(docs)} trecho(s) de {len({m['fonte'] for m in metadados})} arquivo(s).",
        f"Colecao: {colecao.name}",
    ]
    if pulados:
        linhas.append(f"Pulados por serem grandes demais: {', '.join(pulados)}")
    return "\n".join(linhas)


def buscar(pergunta: str, n: int = 6) -> list[dict]:
    """Busca os n trechos mais proximos em todas as colecoes.

    A pergunta e embutida com o MESMO modelo usado na indexacao. Passar
    `query_texts` aqui faria o Chroma cair na sua funcao de embedding padrao
    (ONNX all-MiniLM-L6-v2, 384 dimensoes) e comparar 384 contra os 3072 que
    foram gravados — alem de baixar 79MB de modelo na primeira vez.
    """
    vetor = _embed([pergunta])[0]
    achados = []
    for colecao in cliente().list_collections():
        if colecao.count() == 0:
            continue
        res = colecao.query(query_embeddings=[vetor], n_results=min(n, colecao.count()))
        for doc, meta, dist in zip(
            res["documents"][0], res["metadatas"][0], res["distances"][0]
        ):
            achados.append(
                {
                    "fonte": meta.get("fonte", "?"),
                    "texto": doc,
                    "distancia": round(dist, 4),
                }
            )
    return sorted(achados, key=lambda a: a["distancia"])[:n]


def indexar_tool(alvo: str) -> str:
    """Indexa arquivos de uma pasta para busca semantica.

    Args:
        alvo: caminho de uma pasta (ou de um unico arquivo) com .md, .txt,
              .py, .js, .ts, .json, .yaml, .yml ou .toml.
    """
    return indexar(alvo)


def buscar_tool(pergunta: str) -> str:
    """Busca trechos relevantes nos documentos ja indexados.

    Args:
        pergunta: o que procurar, em linguagem natural.
    """
    achados = buscar(pergunta)
    if not achados:
        return (
            "Nada encontrado. O indice pode estar vazio — rode "
            "`adk run rag indexar <pasta>` antes de perguntar."
        )
    return "\n\n".join(
        f"### {a['fonte']} (distancia {a['distancia']})\n{a['texto']}" for a in achados
    )


# O primeiro argumento decide o caminho: "indexar" indexa, qualquer outra coisa
# pergunta. O ADK so aceita um root_agent por pasta, entao os dois modos dividem
# o mesmo agente em vez de existir dois arquivos.
root_agent = Agent(
    name="RagAgent",
    model=model,
    description="Answers questions from indexed local documents, with citations.",
    instruction=f"""
    You answer questions about the documents indexed on disk, and you always
    cite the source. You have no other knowledge available: what is not in the
    search result, you do not know.

    ## If the message starts with "index"

    Call the `indexar` tool with the given path and show the report. Do not
    answer questions in this mode.

    ## Otherwise, treat it as a question

    1. Call the `buscar` tool with the question. Rephrase it into better
       search terms if the first result comes back empty.
    2. Answer using ONLY what the retrieved chunks contained.
    3. **Cite the source**: put `[source.md]` after every claim.
    4. If the chunks do not answer it, say the information is not in the
       indexed material. Do not fill the gap from your own knowledge — that
       self-consistency is the only thing this agent is for.
    5. If the search returns nothing, suggest running
       `adk run rag indexar <folder>`.

    Index lives in: {INDICE}
    Embeddings use {EMBED_MODEL}, which has its own quota separate from the
    text model.
    """,
    tools=[indexar_tool, buscar_tool],
)
