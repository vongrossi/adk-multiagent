"""
Policy of compatibility with the versions of the ADK.

This exists because the same question was answered in two test files, and a
copied answer is an answer that drifts. The question: **where is the tool
schema that the model actually sees?**

The answer changed at least three times:

  - `parameters_json_schema` — ADK 2.x flattens the JSON Schema here.
  - `parameters` — other versions, pydantic-wrapped.
  - `input_schema` — what the raw MCP tool uses.
  - and `MCPTool` itself exposes *no* public attribute for it: only the private
    `_get_declaration()`.

A test that pins one of those names passes on one version and fails on the
next, with a message about the schema when the real problem is the schema
moving. Probing in order, and falling back to the raw MCP tool, keeps both
test files answering the same question the same way.
"""

import warnings

warnings.filterwarnings("ignore", category=DeprecationWarning)


def schema_de(tool):
    """Return the JSON Schema of a tool as a dict. Never raises."""
    fn = getattr(tool, "_get_declaration", None)
    if callable(fn):
        try:
            decl = fn()
        except Exception:
            decl = None
        if decl is not None:
            dump = (decl.model_dump(exclude_none=True)
                    if hasattr(decl, "model_dump") else dict(decl))
            for chave in ("parameters_json_schema", "parameters", "input_schema"):
                if dump.get(chave):
                    return dump[chave]
            return dump

    raw = getattr(tool, "raw_mcp_tool", None)
    schema = getattr(raw, "input_schema", None) if raw is not None else None
    if isinstance(schema, dict):
        return schema
    if schema is not None and hasattr(schema, "model_dump"):
        return schema.model_dump(exclude_none=True)
    return {}


def descricao_de(tool):
    """Return the tool description as a string, even in the old versions that
    only exposed it in the declaration."""
    d = getattr(tool, "description", None)
    if d:
        return d
    fn = getattr(tool, "_get_declaration", None)
    if callable(fn):
        try:
            decl = fn()
        except Exception:
            return ""
        return getattr(decl, "description", "") or ""
    return ""


def nomes_das_tools(ferramentas):
    """`get_tools()` returns a list in some versions and a dict in others.
    Return `(sorted_names, {name: tool})` so a test can assert on either."""
    if isinstance(ferramentas, dict):
        return sorted(ferramentas), ferramentas
    return (sorted(t.name for t in ferramentas),
            {t.name: t for t in ferramentas})
