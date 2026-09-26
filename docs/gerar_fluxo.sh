#!/usr/bin/env bash
# Regenera docs/fluxo-claro.svg e docs/fluxo-escuro.svg a partir do código
# Mermaid que está no README (seção "Como funciona").
#
# Por que existem dois SVG: o preview do VS Code não renderiza Mermaid, e o
# GitHub só renderiza Mermaid em contexto próprio. A imagem funciona nos dois.
# Manter claro e escuro evita texto branco sobre fundo branco no tema escuro.
#
# Uso:  bash docs/gerar_fluxo.sh
# Precisa de Node:  npm install -g @mermaid-js/mermaid-cli   (dá o comando mmdc)

set -euo pipefail

AQUI="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RAIZ="$(dirname "$AQUI")"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

if ! command -v mmdc >/dev/null 2>&1; then
  echo "mmdc nao encontrado. Instale com:" >&2
  echo "  npm install -g @mermaid-js/mermaid-cli" >&2
  exit 1
fi

# Extrai o primeiro bloco ```mermaid do README, para o diagrama ter uma
# fonte de verdade só — o README. Editou o README, reexecute este script.
python3 - "$RAIZ/README.md" "$TMP/fluxo.mmd" <<'PY'
import re, sys
src, dest = sys.argv[1], sys.argv[2]
texto = open(src, encoding="utf-8").read()
m = re.search(r"```mermaid\n(.*?)\n```", texto, re.S)
if not m:
    sys.exit("Nenhum bloco ```mermaid encontrado no README.")
open(dest, "w", encoding="utf-8").write(m.group(1))
print("Extraido o mermaid do README.")
PY

cat > "$TMP/claro.json" <<'JSON'
{ "theme": "default", "flowchart": { "htmlLabels": true, "curve": "basis", "padding": 12, "useMaxWidth": false } }
JSON
cat > "$TMP/escuro.json" <<'JSON'
{ "theme": "dark", "flowchart": { "htmlLabels": true, "curve": "basis", "padding": 12, "useMaxWidth": false } }
JSON

mmdc -i "$TMP/fluxo.mmd" -o "$AQUI/fluxo-claro.svg"  -c "$TMP/claro.json" -b transparent
mmdc -i "$TMP/fluxo.mmd" -o "$AQUI/fluxo-escuro.svg" -c "$TMP/escuro.json" -b transparent

echo "OK: docs/fluxo-claro.svg e docs/fluxo-escuro.svg regenerados."
