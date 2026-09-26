"""RAG — chunkagem e integracao com o ChromaDB, sem tocar a API.

O embedder real (`gemini-embedding-001`) tem cota PROPRIA e ja veio esgotado
nesta conta, entao o indexing nao pode ser testado de verdade aqui. O que
importa validar sem rede e a parte quebrada com mais frequencia: onde cai o
corte dos chunks, se a colecao se recria, e se a busca ordena por similaridade.

O embedder e substituido por um vetor deterministico derivado do texto, o que
da um ground truth: um trecho que CONTA a palavra deve ficar mais perto de uma
busca por essa palavra do que um trecho que so menciona o assunto de passagem.
"""
import hashlib
import os
import shutil
import sys
import tempfile

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RAIZ)
sys.path.insert(0, os.path.join(RAIZ, "rag"))

import agent as rag  # noqa: E402

ok = True
def chk(t, c, d=""):
    global ok
    print(f"  {'OK   ' if c else 'FALHA'} {t}" + (f" — {d}" if d else ""))
    ok = ok and c


DIM = 256  # fixo: o Chroma exige que o vetor da pergunta tenha a mesma dimensao
            # dos gravados, e um vocabulario montado por lote nao garante isso


def _vetor_falso(textos):
    """Embedder deterministico: cada palavra vira 1 no bucket do seu hash.

    Sem API, e com similaridade que da para afirmar na mao. O bucket vem de
    hashlib e nao do `hash()` do Python, que e salgado por processo — sem isso
    o indice gravado num run nao casaria com a consulta de outro.
    """
    vetores = []
    for t in textos:
        v = [0.0] * DIM
        for p in set(t.lower().replace(".", " ").split()):
            v[int(hashlib.md5(p.encode()).hexdigest(), 16) % DIM] = 1.0
        vetores.append(v)
    return vetores


def main():
    print("[1] chunkagem de Markdown")
    md = """# Guia de Cache

Introducao sobre cache.

## Configurando o cache
O cache fica em .cache. Use a variavel CACHE_DIR apontando para o diretorio.

## Invalidando
Rode o comando de limpeza do cache antes do deploy.

## Deploy
Depois do cache vem o deploy.
"""
    partes = rag.dividir(md, "guia.md")
    rotulos = [p.splitlines()[0] for p in partes]
    # 4 = preambulo + 3 secoes (Invalidando, Deploy inclusas)
    chk("preambulo + uma parte por secao", len(partes) == 4, str(len(partes)))
    chk("titulo da secao ATUAL no rotulo, nunca o anterior",
        rotulos[2] == "## Invalidando", str(rotulos))
    chk("preambulo do doc entra", "Guia de Cache" in rotulos[0], rotulos[0])
    chk("secao curta nao e descartada", any("Deploy" in r for r in rotulos), str(rotulos))
    chk("cada chunk carrega seu proprio titulo",
        all("CACHE_DIR" in partes[1] and "Deploy" in partes[3] for _ in (0,)),
        "conteudo casa com o rotulo")

    print("\n[2] secao sem conteudo e ignorada (nao e perda de dado)")
    sem_corpo = "# T\n\n## So um titulo\n\n## Real\nConteudo de verdade aqui.\n"
    partes2 = rag.dividir(sem_corpo, "v.md")
    chk("titulo vazio nao vira chunk", len(partes2) == 1, str(len(partes2)))
    chk("so o trecho com conteudo", "Real" in partes2[0], partes2[0].splitlines()[0])

    print("\n[3] chunkagem de codigo")
    cod = "\n".join(f"linha {i}" for i in range(1, 101))
    blocos = rag.dividir(cod, "x.py")
    chk("100 linhas -> 3 blocos de 40", len(blocos) == 3, str(len(blocos)))
    chk("ultima linha preservada", "linha 100" in blocos[-1], blocos[-1].split()[-1])

    print("\n[4] ChromaDB: indexar, buscar, reindexar")
    tmp = tempfile.mkdtemp()
    rag.INDICE = os.path.join(tmp, ".indice")
    rag._CLIENT = None
    rag._embed = _vetor_falso  # sem rede

    pasta = os.path.join(tmp, "docs")
    os.makedirs(pasta)
    with open(os.path.join(pasta, "cache.md"), "w", encoding="utf-8") as fh:
        fh.write("# Cache\n\nO cache fica em .cache e usa a variavel CACHE_DIR.\n")
    with open(os.path.join(pasta, "deploy.md"), "w", encoding="utf-8") as fh:
        fh.write("# Deploy\n\nO deploy usa kubernetes e um manifesto yaml.\n")
    with open(os.path.join(pasta, "bin.sh"), "w", encoding="utf-8") as fh:
        fh.write("nao deve ser indexado\n")

    rel = rag.indexar(pasta)
    chk("indexou os 2 arquivos", "Indexados 2 trecho(s)" in rel, rel.splitlines()[0])
    chk("ignora extensao fora da lista", "bin.sh" not in rel, rel.splitlines()[0])

    achados = rag.buscar("CACHE_DIR variavel", n=1)
    chk("busca acha o arquivo certo", achados and achados[0]["fonte"] == "cache.md",
        str([a["fonte"] for a in achados]))
    chk("ordenado por distancia", achados == sorted(achados, key=lambda a: a["distancia"]))

    # reindexar o MESMO arquivo nao pode duplicar o trecho
    antes = len(rag.buscar("cache", n=10))
    rag.indexar(pasta)
    depois = len(rag.buscar("cache", n=10))
    chk("reindexar nao duplica", antes == depois, f"{antes} -> {depois}")

    print("\n[5] arquivo grande e pulado com aviso")
    grande = os.path.join(pasta, "gigante.md")
    with open(grande, "w", encoding="utf-8") as fh:
        fh.write("x" * (rag.TAMANHO_MAX_BYTES + 10))
    rel2 = rag.indexar(pasta)
    chk("nao trava o indexing inteiro", "Indexados" in rel2, rel2.splitlines()[0])
    chk("avisa o que pulou", "Pulados" in rel2 and "gigante.md" in rel2,
        rel2.splitlines()[-1][:60])
    os.remove(grande)

    print("\n[6] pasta sem nada indexavel")
    vazia = os.path.join(tmp, "vazia")
    os.makedirs(vazia)
    chk("relatorio claro, nao traceback",
        "Nenhum arquivo" in rag.indexar(vazia), rag.indexar(vazia)[:50])

    shutil.rmtree(tmp, ignore_errors=True)
    print("\n" + "=" * 58)
    print("RESULTADO:", "RAG OK" if ok else "FALHOU")
    return 0 if ok else 1


sys.exit(main())
