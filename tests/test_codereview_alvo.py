"""
Item 7 do BACKLOG: `codereview` recebia ARQUIVO = None, sempre.

O que este teste trava, e por que ele e diferente dos outros:

Os outros testes exercitam uma funcao com entradas inventadas. Este testa
`_resolver_alvo` contra o argv que o ADK REAL produz, porque o bug nao estava
na logica — estava no **acordo com o CLI**. Duas coisas que so aparecem
rodando o comando de verdade:

1. `adk run codereview common.py` NAO passa um flag: o click trata `common.py`
   como a *query*, e o texto vai para o agente como mensagem do usuario. O
   caminho chega no modulo pelo `sys.argv`, que e a unica fonte disponivel no
   momento do import.

2. O `--file` que o docstring do modulo prometia nao existe no CLI do adk; o
   click aborta com "No such option". A documentacao estava ensinando um
   comando que nao roda.

O risco do parser e falso positivo, e ele e real: `adk web .` passa "." (que e
diretorio) e um nome de agente ("codereview") aparece no proprio argv. Um
parser ingenuo resolve `adk web` para um arquivo chamado "codereview" e le o
codigo-fonte do agente. Cada caso abaixo e um desses falsos positivos.
"""

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


def main():
    from codereview.agent import _resolver_alvo, ler_arquivo
    import codereview.agent as C

    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    print("=" * 68)
    print("1. A SINTAXE QUE O DOCSTRING PROMETIA NAO EXISTE")
    print("=" * 68)
    # `adk run codereview --file x.py` -> o click rejeita antes do import.
    # O que sobra e a query posicional, e ela precisa virar o alvo.
    chk("query posicional vira o alvo",
        _resolver_alvo(["adk", "run", "codereview", "common.py"]) == "common.py",
        _resolver_alvo(["adk", "run", "codereview", "common.py"]))
    chk("caminho com subpasta",
        _resolver_alvo(["adk", "run", "codereview", "linker/agent.py"])
        == "linker/agent.py")
    chk("--file explicito ainda funciona",
        _resolver_alvo(["adk", "run", "codereview", "--file", "common.py"])
        == "common.py")
    chk("--file=com igual",
        _resolver_alvo(["x", "--file=common.py"]) == "common.py")

    print("\n" + "=" * 68)
    print("2. FALSOS POSITIVOS: O QUE *NAO* PODE VIRAR ALVO")
    print("=" * 68)
    nao_alvo = [
        ("adk web (o '.' e diretorio)", ["adk", "web", "."]),
        ("adk web com caminho de agente", ["adk", "web", "codereview"]),
        ("adk run sem query", ["adk", "run", "codereview"]),
        ("query em portugues", ["adk", "run", "codereview", "revise o modulo de auth"]),
        ("subcomando solto", ["run", "codereview"]),
        ("flags do cli", ["adk", "run", "codereview", "--save_session", "--in_memory"]),
        ("argv vazio", []),
    ]
    for nome, argv in nao_alvo:
        r = _resolver_alvo(argv)
        chk(f"ignora: {nome}", r is None, f"veio {r!r}")

    print("\n" + "=" * 68)
    print("3. UM ARQUIVO INEXISTENTE NAO SERA LIDO (a tool recusa)")
    print("=" * 68)
    r = _resolver_alvo(["adk", "run", "codereview", "nao_existe.py"])
    chk("guarda o caminho mesmo se nao existe", r == "nao_existe.py", r)
    real, C.ARQUIVO = C.ARQUIVO, r
    try:
        saida = ler_arquivo()
        chk("a tool diz que nao encontrou", "nao encontrado" in saida.lower(), saida[:70])
    finally:
        C.ARQUIVO = real

    print("\n" + "=" * 68)
    print("4. ARQUIVO DE VERDADE: A TOOL LE E AS HEURISTICAS RODAM")
    print("=" * 68)
    def ler_arquivo_com(caminho):
        anterior, C.ARQUIVO = C.ARQUIVO, os.path.join(here, caminho)
        try:
            return ler_arquivo()
        finally:
            C.ARQUIVO = anterior

    out = ler_arquivo_com("common.py")
    # o fence sai da extensao do arquivo: "```py", nao "```python"
    chk("abre e fecha o fence de codigo", out.startswith("```py\n") and "\n```" in out)
    chk("le o arquivo inteiro", len(out) > 500)
    chk("o texto do arquivo esta dentro do fence",
        "Configuração compartilhada" in out)
    chk("numera as linhas do arquivo", "linha " in out)
    chk("cabe o cabecalho de achados", "Achados automaticos" in out)

    print("\n" + "=" * 68)
    print("5. A LISTA DE IGNORADOS AINDA VALE APOS O PARSER")
    print("=" * 68)
    # o parser nao pode ter criado um buraco: Path traversal / segredo seguem
    # barrados pela checagem de basename, que nao foi tocada.
    for nome in [".env", "id_rsa", "server.pem", "credentials.json"]:
        anterior, C.ARQUIVO = C.ARQUIVO, os.path.join(here, nome)
        try:
            saida = ler_arquivo()
            barrado = "ignorados" in saida
            chk(f"ignora {nome}", barrado, saida[:60])
        finally:
            C.ARQUIVO = anterior

    # E o caminho de fora do repo tambem.
    #
    # Este teste ja afirmava o contrario: ele exigia que `/etc/passwd` fosse
    # lido, e chamava isso de "nao e um bug". Era a vulnerabilidade escrita
    # como expectativa. O alvo vinha do shell do operador, nao do modelo — o
    # que reduz o risco, mas nao zera: `adk run codereview --file ~/.ssh/id_rsa`
    # lia a chave privada e a mandava para o modelo. Um revisor de segredos
    # que le segredos e contradicao. Agora a tool e confinada a raiz do repo.
    for fora in ("/etc/passwd", "~/.ssh/id_rsa", "/root/.env", "../fora.py"):
        anterior, C.ARQUIVO = C.ARQUIVO, os.path.expanduser(fora)
        try:
            saida = ler_arquivo()
            chk(f"{fora}: recusado",
                "fora do repositorio" in saida or "ignorados" in saida,
                saida[:60].replace("\n", " "))
            chk(f"{fora}: nada de conteudo vazou",
                "root:" not in saida, saida[:60])
        finally:
            C.ARQUIVO = anterior

    # Symlink nao contorna: o confinamento compara `realpath` dos dois lados.
    import tempfile
    with tempfile.TemporaryDirectory() as tmpd:
        link = os.path.join(tmpd, "parece.py")
        os.symlink("/etc/hostname", link)
        anterior, C.ARQUIVO = C.ARQUIVO, link
        try:
            saida = ler_arquivo()
            chk("symlink para fora do repo: recusado",
                "fora do repositorio" in saida or "ignorados" in saida,
                saida[:60])
        finally:
            C.ARQUIVO = anterior

    print("\n" + "=" * 68)
    print("6. A FERRAMENTA NAO ACEITA CAMINHO DO MODELO (a garantia real)")
    print("=" * 68)
    import inspect
    sig = inspect.signature(ler_arquivo)
    chk("ler_arquivo nao tem parametros", len(sig.parameters) == 0, sig)
    chk("o modelo nao consegue escolher o arquivo",
        "arquivo" not in sig.parameters)

    print("\n" + "=" * 68)
    print("RESUMO")
    print("=" * 68)
    if falhas:
        print(f"{len(falhas)} FALHA(S):")
        for f in falhas:
            print(f"  - {f}")
        return 1
    print("o caminho vem do shell; o modelo nunca escolhe o que e lido")
    return 0


if __name__ == "__main__":
    sys.exit(main())
