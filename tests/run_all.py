"""Roda toda a suite de testes e resume.

Cada teste roda em um subprocesso separado, e obrigatorio: eles sobrescrevem
`os.environ` e apagam modulos de `sys.modules` para recarregar `common.py` com
outra configuracao. Se rodassem no mesmo interpretador, o teste seguinte herdaria
o estado do anterior. As portas (8291-8295) tambem sao distintas por esse motivo.

Nao usa pytest de proposito: `requirements.txt` nao depende dele, e cada script
continua rodando sozinho com `python3 tests/test_x.py`.

Uso:
    python3 tests/run_all.py
    python3 tests/run_all.py test_llamacpp test_fallback   # so alguns
"""
import os
import subprocess
import sys
import time

AQUI = os.path.dirname(os.path.abspath(__file__))

# (arquivo, o que trava). A ordem nao importa porque cada um e isolado, mas
# deixar os mais rapidos primeiro faz o "--help" nao custar 30s.
SUITE = [
    ("test_emulacao.py", "tool calling emulado no LlamaCppLlm (Gemma)"),
    ("test_fallback.py", "cadeia 429/5xx, exclusao de 429 do retry, local fora do ar"),
    ("test_llamacpp.py", "adaptador local: schema OpenAI, tool call, 6 cenarios de env"),
    ("test_blogger_retry.py", "loop do Blogger: retry -> escalate, veredito intacto"),
    ("test_blogger_local.py", "Blogger inteiro contra llama-server falso"),
    ("test_novos_agentes.py", "limites do Blogger, padroes de segredo, extracao de URL"),
    ("test_rag.py", "chunkagem e ChromaDB, com embedder falso (sem API)"),
    ("test_triage.py", "rota por confianca (jev): limiar, runner-up, fallback"),
    ("test_mcp.py", "servidor MCP real: spawn, schema, execucao, o trap do dict"),
    ("e2e_check.py", "ADK puro: state, output_key, callbacks, LoopAgent, AgentTool"),
]


def main(alvos):
    if alvos:
        sel = [t for t in SUITE if any(a in t[0] for a in alvos)]
        if not sel:
            print(f"nenhum teste casa com {alvos}")
            print("disponiveis:", ", ".join(t[0] for t in SUITE))
            return 2
    else:
        sel = SUITE

    print(f"rodando {len(sel)} suite(s)\n" + "=" * 68)
    resultados = []
    for arquivo, descricao in sel:
        caminho = os.path.join(AQUI, arquivo)
        t0 = time.monotonic()
        proc = subprocess.run(
            [sys.executable, "-B", caminho],
            capture_output=True, text=True, cwd=os.path.dirname(AQUI),
        )
        dt = time.monotonic() - t0
        ok = proc.returncode == 0
        falhas = [l.strip() for l in proc.stdout.splitlines() if l.strip().startswith("FALHA")]
        resultados.append((arquivo, ok, dt, falhas))
        print(f"{'PASS' if ok else 'FAIL'}  {arquivo:24} {dt:5.1f}s  {descricao}")
        if not ok:
            for f in falhas[:6]:
                print(f"        {f}")
            if not falhas:
                err = (proc.stderr or proc.stdout).strip().splitlines()
                for l in err[-6:]:
                    print(f"        {l}")

    print("=" * 68)
    passou = sum(1 for _, ok, _, _ in resultados if ok)
    dt_total = sum(dt for _, _, dt, _ in resultados)
    print(f"{passou}/{len(resultados)} passaram em {dt_total:.1f}s")
    if passou != len(resultados):
        print("\nfalhou: " + ", ".join(a for a, ok, _, _ in resultados if not ok))
    return 0 if passou == len(resultados) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
