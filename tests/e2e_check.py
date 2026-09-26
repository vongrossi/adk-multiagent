"""End-to-end offline validation of agent.py (no API key).

Every agent's model is swapped for a scripted fake, so this exercises the real
ADK machinery: state, output_key, instruction templating, after_agent_callback
escalation, LoopAgent, and the AgentTool calls from the root agent.
"""

import asyncio
import os
import sys

from google.adk.models.base_llm import BaseLlm
from google.adk.models.llm_response import LlmResponse
from google.adk.runners import InMemoryRunner
from google.genai import types

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RAIZ)
sys.path.insert(0, os.path.join(RAIZ, "blogger"))
import agent  # noqa: E402

APP = "e2e"
calls = []  # (agent_name, system_instruction, tool_names_available)


class Scripted(BaseLlm):
    agent_name: str = "?"
    script: list = []  # [{"text": ...} | {"call": "ToolName"}]

    async def generate_content_async(self, llm_request, stream=False):
        si = llm_request.config.system_instruction or ""
        calls.append(
            (self.agent_name, si, llm_request.config.tools or [])
        )
        step = self.script.pop(0) if self.script else {"text": "ok"}
        if step.get("call"):
            part = types.Part(
                function_call=types.FunctionCall(
                    name=step["call"], args=step.get("args", {})
                )
            )
        else:
            part = types.Part(text=step["text"])
        yield LlmResponse(
            content=types.Content(role="model", parts=[part])
        )


def fake(name, script):
    return Scripted(
        model="gemini-2.5-flash", agent_name=name, script=list(script)
    )


class Shim:
    """Gemini falso: delega generate_content_async para o Scripted."""
    scripted: object = None

    def __init__(self, inner):
        object.__setattr__(self, "_inner", inner)

    @property
    def model(self):
        return self._inner.model

    def generate_content_async(self, *a, **k):
        return self._inner.generate_content_async(*a, **k)


async def go(session_id, message, root=None):
    root = root or agent.root_agent
    for a in (agent.blog_planner, agent.OutlineValidationChecker,
              agent.blog_writer, agent.BlogPostValidationChecker,
              agent.root_agent):
        if not isinstance(getattr(a, "model", None), Scripted):
            a.model = Shim(a.model)
    runner = InMemoryRunner(agent=root, app_name=APP)
    await runner.session_service.create_session(
        app_name=APP, user_id="u", session_id=session_id
    )
    calls.clear()
    async for _ in runner.run_async(
        user_id="u",
        session_id=session_id,
        new_message=types.Content(
            role="user", parts=[types.Part(text=message)]
        ),
    ):
        pass
    s = await runner.session_service.get_session(
        app_name=APP, user_id="u", session_id=session_id
    )
    return s.state


def n(name):
    return sum(1 for c in calls if c[0] == name)


def instr(name, i=0):
    return [c[1] for c in calls if c[0] == name][i]


def check(label, got, want):
    ok = got == want
    print(f"   {'PASS' if ok else 'FAIL'}  {label}: got={got!r} want={want!r}")
    return ok


ok = True

print("== A. happy path: root -> planner tool -> writer tool ==")
agent.blog_planner.model = fake("BlogPlanner", [{"text": "# T\n## S1\n## S2"}])
agent.OutlineValidationChecker.model = fake(
    "OutlineValidationChecker", [{"text": "ok"}]
)
agent.blog_writer.model = fake("BlogWriter", [{"text": "artigo final"}])
agent.BlogPostValidationChecker.model = fake(
    "BlogPostValidationChecker", [{"text": "ok"}]
)
agent.root_agent.model = fake(
    "Blogger",
    [
        {"call": "RobustBlogPlanner"},
        {"call": "RobustBlogWriter"},
        {"text": "3 titulos alternativos\n\nhooks"},
    ],
)

state = asyncio.run(go("a1", "escreva sobre asyncio"))
ok &= check("BlogPlanner chamadas (1 iteracao)", n("BlogPlanner"), 1)
ok &= check("OutlineValidationChecker chamadas", n("OutlineValidationChecker"), 1)
ok &= check("BlogWriter chamadas", n("BlogWriter"), 1)
ok &= check("BlogPostValidationChecker chamadas", n("BlogPostValidationChecker"), 1)
ok &= check("blog_outline no state", state.get("blog_outline"), "# T\n## S1\n## S2")
ok &= check("outline_validation no state", state.get("outline_validation"), "ok")
ok &= check("blog_post no state", state.get("blog_post"), "artigo final")
ok &= check("post_validation no state", state.get("post_validation"), "ok")
ok &= check(
    "writer recebeu o outline no prompt",
    "# T\n## S1\n## S2" in instr("BlogWriter"),
    True,
)
def declared_tools(name):
    for n, _si, tools in calls:
        if n != name:
            continue
        for t in tools or []:
            if getattr(t, "function_declarations", None):
                for fd in t.function_declarations:
                    yield fd.name
            elif isinstance(t, list):
                for fd in t:
                    yield fd.name
            else:
                yield t


ok &= check(
    "root AgentTools disponiveis no prompt",
    sorted(set(declared_tools("Blogger"))),
    ["RobustBlogPlanner", "RobustBlogWriter"],
)
ok &= check(
    "placeholder {outline_validation?} resolvido na 1a passada",
    "{outline_validation?}" in instr("BlogPlanner"),
    False,
)

print("== B. retry no outline: 2 passes, loop para no ok ==")
agent.blog_planner.model = fake(
    "BlogPlanner", [{"text": "outline v1"}, {"text": "outline v2 corrigido"}]
)
agent.OutlineValidationChecker.model = fake(
    "OutlineValidationChecker",
    [{"text": "retry: falta conclusao"}, {"text": "ok"}],
)
state = asyncio.run(
    go("b1", "x", root=agent.robust_blog_planner)
)
ok &= check("BlogPlanner chamadas (2 passes)", n("BlogPlanner"), 2)
ok &= check("checker chamadas (2 passes)", n("OutlineValidationChecker"), 2)
ok &= check("2a chamada viu o veredito 'retry'",
            "retry: falta conclusao" in instr("BlogPlanner", 1), True)
ok &= check("outline final e a versao corrigida",
            state.get("blog_outline"), "outline v2 corrigido")
ok &= check("veredito final", state.get("outline_validation"), "ok")

print("== C. retry no post: 2 passes ==")
agent.blog_writer.model = fake(
    "BlogWriter", [{"text": "post v1"}, {"text": "post v2 corrigido"}]
)
agent.BlogPostValidationChecker.model = fake(
    "BlogPostValidationChecker",
    [{"text": "retry: sem conclusao"}, {"text": "ok"}],
)
state = asyncio.run(go("c1", "y", root=agent.robust_blog_writer))
ok &= check("BlogWriter chamadas (2 passes)", n("BlogWriter"), 2)
ok &= check("2a chamada viu o veredito 'retry'",
            "retry: sem conclusao" in instr("BlogWriter", 1), True)
ok &= check("post final", state.get("blog_post"), "post v2 corrigido")
ok &= check("veredito final", state.get("post_validation"), "ok")

print("== D. veredito nunca aprova: max_iterations=3 respeitado ==")
agent.blog_planner.model = fake(
    "BlogPlanner", [{"text": "v1"}, {"text": "v2"}, {"text": "v3"}]
)
agent.OutlineValidationChecker.model = fake(
    "OutlineValidationChecker",
    [{"text": "retry: a"}, {"text": "retry: b"}, {"text": "retry: c"}],
)
state = asyncio.run(go("d1", "z", root=agent.robust_blog_planner))
ok &= check("BlogPlanner chamadas (3)", n("BlogPlanner"), 3)
ok &= check("checker chamadas (3)", n("OutlineValidationChecker"), 3)
ok &= check("nao escalou (nenhum ok)", state.get("outline_validation"), "retry: c")

print("== E. veredito malformado nao trava o loop ==")
agent.blog_planner.model = fake("BlogPlanner", [{"text": "v1"}])
agent.OutlineValidationChecker.model = fake(
    "OutlineValidationChecker",
    [{"text": "Looks good to me!"}] * 3,
)
state = asyncio.run(go("e1", "w", root=agent.robust_blog_planner))
ok &= check("veredito com texto livre nao escalou (rodou 3x)",
            n("OutlineValidationChecker"), 3)

print()
print("RESULTADO:", "TODOS OS CHECKS PASSARAM" if ok else "HOUVE FALHAS")
sys.exit(0 if ok else 1)
