"""
Adaptador para rodar um modelo local servido pelo llama.cpp.

Por que este arquivo existe: o `Agent.model` do ADK aceita `Union[str, BaseLlm]`,
entao qualquer backend pode entrar no lugar do `Gemini`. O llama.cpp expoe uma
API compativel com a da OpenAI em `/v1/chat/completions`; este arquivo traduz
o formato do ADK (`LlmRequest`/`LlmResponse`, tipos do google-genai) para o
formato OpenAI e vice-versa.

O que o ADK continua garantindo, com modelo local: `output_key`, `state`,
`AgentTool`, `LoopAgent`, `after_agent_callback` e os placeholders `{chave?}`.
O modelo local so substitui quem gera o texto e emite a tool call.

Detalhe importante: o `Gemma` que ja vem no ADK (`google.adk.models.Gemma`) NAO
serve para isto. Ele herda de `Gemini` e so fala com a API do Google, apesar do
nome suggestir Ollama. A classe abaixo e a que conversa com o llama.cpp.

Nao e o caminho mais curto (o LiteLlm faz o mesmo com menos codigo, mas exige
`pip install 'google-adk[extensions]'`). E o caminho sem dependencia extra e com
controle sobre o payload.

Configuracao (no .env da raiz):
    use_llamacpp=true
    llamacpp_base_url=http://127.0.0.1:8080/v1
    llamacpp_model=gemma-3-12b-it
    llamacpp_context=8192
    llamacpp_gpu_layers=99

O servidor precisa subir assim, senao ele ignora o campo `tools`:
    llama-server -m gemma-3-12b-it-Q4_K_M.gguf --jinja -c 8192 -ngl 99
"""

from __future__ import annotations

import json
import os
from typing import Any, AsyncGenerator

import httpx
from dotenv import load_dotenv
from google.adk.models.base_llm import BaseLlm
from google.adk.models.gemma_llm import GemmaFunctionCallingMixin
from google.adk.models.llm_request import LlmRequest
from google.adk.models.llm_response import LlmResponse
from google.genai import types

load_dotenv()

# ---------------------------------------------------------------------------
# Configuracao
# ---------------------------------------------------------------------------

BASE_URL = os.getenv("llamacpp_base_url", "http://127.0.0.1:8080/v1")
LOCAL_MODEL = os.getenv("llamacpp_model", "gemma-3-12b-it")
CONTEXT_SIZE = int(os.getenv("llamacpp_context", "8192"))
GPU_LAYERS = int(os.getenv("llamacpp_gpu_layers", "99"))

# Timeout generoso: um 12B em CPU pode levar dezenas de segundos por token, e o
# Blogger faz varias chamadas em sequencia. Um timeout curto derruba a execucao
# no meio, bem no lugar que o `HttpRetryOptions` do Gemini nao alcança.
TIMEOUT = float(os.getenv("llamacpp_timeout", "600"))

# Gemma 3 nao tem function calling nativo. Quando `llamacpp_emulate_tools=true`,
# reaproveitamos o mixin do proprio ADK: ele injeta as declaracoes de tool no
# system instruction e faz parse da resposta em texto. Custa mais token e erra
# mais que tool calling nativo, mas e melhor do que o modelo ignorar a tool.
EMULATE_TOOLS = os.getenv("llamacpp_emulate_tools", "true").lower() == "true"


# ---------------------------------------------------------------------------
# Traducao: ADK -> formato OpenAI
# ---------------------------------------------------------------------------


def _partes_para_mensagens(content: types.Content) -> list[dict[str, Any]]:
    """Traduz um `Content` do ADK na lista de mensagens OpenAI equivalente.

    O ADK usa um unico tipo `Part` com sub-campos (text, function_call,
    function_response); o formato OpenAI separa em papeis distintos
    (assistant para a chamada, tool para o resultado). E aqui que a traducao
    realmente acontece.
    """
    mensagens: list[dict[str, Any]] = []

    for parte in content.parts or []:
        if parte.text:
            mensagens.append({"role": "user", "content": parte.text})
        elif parte.function_call:
            mensagens.append(
                {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {
                            "id": parte.function_call.id or parte.function_call.name,
                            "type": "function",
                            "function": {
                                "name": parte.function_call.name,
                                "arguments": json.dumps(
                                    dict(parte.function_call.args or {}), ensure_ascii=False
                                ),
                            },
                        }
                    ],
                }
            )
        elif parte.function_response:
            mensagens.append(
                {
                    "role": "tool",
                    "tool_call_id": parte.function_response.id
                    or parte.function_response.name,
                    "content": json.dumps(
                        parte.function_response.response or {}, ensure_ascii=False
                    ),
                }
            )

    return mensagens


def _normalizar_roles(mensagens: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Junta mensagens de texto consecutivas do mesmo papel.

    O template Jinja do Gemma 3 (e o de varias familias) exige alternancia
    estrita `user/assistant/user/assistant` e levanta 400 em
    `raise_exception("Conversation roles must alternate ...")` quando duas
    mensagens de texto do mesmo papel se seguem. O ADK, ao processar respostas
    de tools, produz exatamente isso: cada `function_response` vira um
    `Content(role='user')` proprio.

    Aqui a conversao e a unica saida possivel. As mensagens de role `tool` nao
    sao fundidas: a API OpenAI exige uma mensagem por `tool_call_id`, e
   模型的 template costuma esperar esse par assistant->tool intacto.
    """
    saida: list[dict[str, Any]] = []
    for mensagem in mensagens:
        papel = mensagem.get("role")
        if (
            saida
            and papel == "user"
            and saida[-1].get("role") == "user"
            and not mensagem.get("tool_calls")
            and not saida[-1].get("tool_calls")
        ):
            # Preserva a fronteira entre os blocos: sem um separador, o modelo
            # le as duas falas como um parágrafo só e perde onde uma termina.
            saida[-1] = {
                **saida[-1],
                "content": f"{saida[-1].get('content', '')}\n\n{mensagem.get('content', '')}",
            }
            continue
        saida.append(mensagem)
    return saida


def _schema_para_json(schema: types.Schema) -> dict[str, Any]:
    """Converte um `types.Schema` (google-genai) em JSON Schema da OpenAI.

    Só usado como fallback: quando o ADK liga o
    `FeatureName.JSON_SCHEMA_FOR_FUNC_DECL`, o schema ja vem pronto em
    `parameters_json_schema` e nao precisa desta conversao.
    """
    saida: dict[str, Any] = {}

    if schema.type is not None:
        # types.Type.STRING -> "string"; a OpenAI exige minusculo.
        saida["type"] = str(schema.type).rsplit(".", 1)[-1].lower()

    if schema.description:
        saida["description"] = schema.description
    if schema.format:
        saida["format"] = schema.format
    if schema.enum:
        saida["enum"] = list(schema.enum)
    if schema.nullable is not None:
        saida["nullable"] = schema.nullable

    if schema.properties:
        saida["properties"] = {
            nome: _schema_para_json(sub) for nome, sub in schema.properties.items()
        }
    if schema.required:
        saida["required"] = list(schema.required)
    if schema.items:
        saida["items"] = _schema_para_json(schema.items)
    if schema.any_of:
        saida["anyOf"] = [_schema_para_json(sub) for sub in schema.any_of]

    return saida


def _tools_para_openai(ferramentas: list[types.Tool] | None) -> list[dict[str, Any]]:
    """Traduz as `FunctionDeclaration` do ADK para o schema JSON da OpenAI.

    Sem o passo `parameters` correto, o llama.cpp recusa a request ou o modelo
    nunca chama a tool — e o agente trava sem erro visivel.
    """
    saida: list[dict[str, Any]] = []

    for tool in ferramentas or []:
        for decl in tool.function_declarations or []:
            # `parameters_json_schema` e o caminho normal: ja vem em JSON
            # Schema puro, com tipos em minusculo. `parameters` fica None
            # quando o ADK usa essa feature, e dai vem o bug classico de
            # mandar `properties: {}` e o modelo não saber o que preencher.
            if decl.parameters_json_schema:
                parametros: Any = decl.parameters_json_schema
            elif decl.parameters is not None:
                parametros = _schema_para_json(decl.parameters)
            else:
                parametros = None

            saida.append(
                {
                    "type": "function",
                    "function": {
                        "name": decl.name,
                        "description": decl.description or "",
                        "parameters": parametros or {"type": "object", "properties": {}},
                    },
                }
            )

    return saida


# ---------------------------------------------------------------------------
# Traducao: formato OpenAI -> ADK
# ---------------------------------------------------------------------------


def _resposta_para_partes(mensagem: dict[str, Any]) -> list[types.Part]:
    """Traduz a mensagem OpenAI de volta para `Part` do ADK."""
    partes: list[types.Part] = []

    if mensagem.get("content"):
        partes.append(types.Part.from_text(text=mensagem["content"]))

    for chamada in mensagem.get("tool_calls") or []:
        bruto = chamada.get("function", {}).get("arguments") or "{}"
        try:
            argumentos = json.loads(bruto)
        except json.JSONDecodeError:
            # Modelos pequenos truncam o JSON da tool. Preservamos o texto em
            # vez de estourar: o agente devolve a chamada e o log mostra o
            # problema, em vez de a execucao morrer aqui.
            argumentos = {"_raw": bruto}

        partes.append(
            types.Part.from_function_call(
                name=chamada.get("function", {}).get("name", ""),
                args=argumentos,
            )
        )

    return partes


# ---------------------------------------------------------------------------
# O modelo
# ---------------------------------------------------------------------------


# Bases do `LlamaCppLlm`, escolhidas no import.
#
# Um if/else direto na lista de bases (`class X(Mixin if FLAG else Base)`) parece
# funcionar, mas quebra o MRO: quando o mixin esta ligado, `BaseLlm` some da
# heranca e o objeto deixa de ser um modelo valido do ADK
# (MRO: LlamaCppLlm -> GemmaFunctionCallingMixin -> object). Montar a tupla
# primeiro resolve, e `BaseLlm` continua na cadeia.
_BASES: tuple[type, ...] = (
    (GemmaFunctionCallingMixin, BaseLlm) if EMULATE_TOOLS else (BaseLlm,)
)


class LlamaCppLlm(*_BASES):  # type: ignore[misc]
    """`BaseLlm` que delega a inferencia para um llama-server local.

    Implementar `BaseLlm` e o unico requisito do ADK para trocar o backend.
    O mixin do Gemma entra opcionalmente para emular tool calling; como os
    metodos dele operam so sobre `LlmRequest`/`LlmResponse`, funcionam igual
    aqui — ele nao depende da API do Google.
    """

    model: str = LOCAL_MODEL

    @classmethod
    def supported_models(cls) -> list[str]:
        return [r"local-.*", r"llamacpp-.*"]

    async def generate_content_async(
        self, llm_request: LlmRequest, stream: bool = False
    ) -> AsyncGenerator[LlmResponse, None]:
        mensagens: list[dict[str, Any]] = []

        if llm_request.config.system_instruction:
            mensagens.append(
                {
                    "role": "system",
                    "content": llm_request.config.system_instruction,
                }
            )

        for conteudo in llm_request.contents or []:
            mensagens.extend(_partes_para_mensagens(conteudo))

        ferramentas = _tools_para_openai(llm_request.config.tools)

        payload: dict[str, Any] = {
            "model": self.model,
            "messages": mensagens,
            "temperature": llm_request.config.temperature
            if llm_request.config.temperature is not None
            else 0.7,
            "max_tokens": llm_request.config.max_output_tokens or 2048,
        }
        if ferramentas:
            payload["tools"] = ferramentas
            payload["tool_choice"] = "auto"

        if EMULATE_TOOLS and ferramentas:
            # Deixa o mixin reescrever os contents e injetar as tools como
            # texto antes de montarmos as mensagens.
            self._move_function_calls_into_system_instruction(llm_request)
            mensagens = []
            if llm_request.config.system_instruction:
                mensagens.append(
                    {
                        "role": "system",
                        "content": llm_request.config.system_instruction,
                    }
                )
            for conteudo in llm_request.contents or []:
                mensagens.extend(_partes_para_mensagens(conteudo))
            payload["messages"] = mensagens
            payload.pop("tools", None)
            payload.pop("tool_choice", None)

        payload["messages"] = _normalizar_roles(mensagens)

        async with httpx.AsyncClient(timeout=TIMEOUT) as cliente:
            try:
                resposta = await cliente.post(
                    f"{BASE_URL}/chat/completions", json=payload
                )
                corpo = resposta.json()
                resposta.raise_for_status()
            except httpx.HTTPError as erro:
                # O corpo do erro do llama-server e onde esta a causa real. Sem
                # ele, um 400 por template Jinja (roles nao alternados) e
                # indistinguivel de um 400 por `--jinja` ausente — e o primeiro
                # nao tem nada a ver com o segundo.
                detalhe = ""
                if "corpo" in dir() and isinstance(corpo, dict) and corpo.get("error"):
                    detalhe = f" Detalhe do servidor: {str(corpo['error'])[:400]}"
                raise RuntimeError(
                    f"Falha ao falar com o llama-server em {BASE_URL}: {erro}. "
                    f"O servidor subiu? Ele foi iniciado com --jinja? "
                    f"(sem --jinja o campo tools e ignorado).{detalhe}"
                ) from erro

        escolhas = corpo.get("choices") or []
        if not escolhas:
            return

        partes = _resposta_para_partes(escolhas[0].get("message") or {})

        resposta_adk = LlmResponse(
            content=types.Content(role="model", parts=partes),
            usage_metadata=None,
        )

        if EMULATE_TOOLS:
            self._extract_function_calls_from_response(resposta_adk)

        yield resposta_adk
