"""Chat clients for every supported AI provider, with bounded output and no hidden retries.

Three wire protocols cover the providers:
- OpenAI chat completions: DeepSeek (recommended), OpenAI, OpenRouter and any compatible server.
- Anthropic Messages: Claude.
- Ollama's native /api/chat: local models. Its OpenAI-compatible endpoint can't set the context
  window, and Ollama's default window silently truncates Archer's evidence.

`plan` returns an OpenAI-shaped assistant message (`tool_calls` with JSON-string arguments) and
`generate` streams answer blocks, whichever protocol answered, so ai_service stays provider-neutral.
"""
from dataclasses import dataclass
import json
import re

import httpx

from archer import tls


class AIError(Exception):
    pass


@dataclass(frozen=True)
class ProviderInfo:
    label: str
    protocol: str  # openai | anthropic | ollama
    base: str
    model: str  # recommended default; empty means the person chooses one
    key: str = ""  # Settings field holding the key; empty means no key is needed
    models: tuple[str, ...] = ()
    key_url: str = ""
    blurb: str = ""


PROVIDERS = {
    "deepseek": ProviderInfo("DeepSeek", "openai", "https://api.deepseek.com", "deepseek-flash", "deepseek_api_key",
        ("deepseek-flash", "deepseek-chat", "deepseek-reasoner"), "https://platform.deepseek.com/api_keys",
        "Fast, very low cost, and Archer's spending caps know its prices."),
    "openai": ProviderInfo("OpenAI", "openai", "https://api.openai.com/v1", "gpt-5-mini", "openai_api_key",
        ("gpt-5-mini", "gpt-5", "gpt-5-nano", "gpt-4.1-mini"), "https://platform.openai.com/api-keys",
        "GPT models with an OpenAI API key."),
    "anthropic": ProviderInfo("Claude", "anthropic", "https://api.anthropic.com", "claude-haiku-5-5", "anthropic_api_key",
        ("claude-haiku-5-5", "claude-sonnet-5-5", "claude-opus-5-5"), "https://console.anthropic.com/settings/keys",
        "Anthropic's Claude models. Haiku is quick and inexpensive; Sonnet writes deeper research."),
    "ollama": ProviderInfo("Ollama", "ollama", "http://127.0.0.1:11434", "", "",
        (), "https://ollama.com/download",
        "Local models on this computer. Free and private; needs a capable GPU for good answers."),
    "openrouter": ProviderInfo("OpenRouter", "openai", "https://openrouter.ai/api/v1", "openrouter/auto", "openrouter_api_key",
        ("openrouter/auto", "deepseek/deepseek-chat", "openai/gpt-5-mini", "anthropic/claude-haiku-5.5"), "https://openrouter.ai/keys",
        "One key for hundreds of models from every major lab."),
    "custom": ProviderInfo("Custom", "openai", "", "", "ai_api_key", (), "",
        "Any OpenAI-compatible server: LM Studio, vLLM, llama.cpp, Groq, Together, Mistral…"),
}


@dataclass(frozen=True)
class Resolved:
    provider: str
    info: ProviderInfo
    base: str
    model: str
    key: str


def resolve(settings, provider: str | None = None, key: str | None = None, base: str | None = None,
            model: str | None = None) -> Resolved:
    """The provider, address, model and key Archer will actually use; blanks mean the provider's own."""
    name = provider or settings.ai_provider
    info = PROVIDERS.get(name, PROVIDERS["deepseek"])
    own = name == settings.ai_provider
    return Resolved(name, info,
        (base or (settings.ai_base_url if own else "") or info.base).rstrip("/"),
        model or (settings.ai_model if own else "") or info.model,
        key if key is not None else (getattr(settings, info.key) if info.key else ""))


def readiness(settings) -> tuple[bool, str]:
    """Whether AI can run, and if not, what's missing, worded for the person."""
    r = resolve(settings)
    if r.info.key and r.provider != "custom" and not r.key:
        return False, f"Add your {r.info.label} API key in Archer's settings (AI) to enable answers."
    if not r.base:
        return False, "Add your AI server's address in Archer's settings (AI)."
    if not r.model:
        return False, f"Choose which {r.info.label} model to use in Archer's settings (AI)."
    return True, ""


def loads_object(text: str) -> dict:
    """Parse the answer object, tolerating code fences or a sentence around it."""
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.S).strip()
    try:
        value = json.loads(text)
    except ValueError:
        start, end = text.find("{"), text.rfind("}")
        if start < 0 or end <= start:
            raise
        value = json.loads(text[start:end + 1])
    if not isinstance(value, dict):
        raise ValueError("not an object")
    return value


class JSONBlocks:
    """Yield complete JSON objects from the answer's blocks array, never partial HTML/text.

    A top-level "visuals" object written before "blocks" is parsed as soon as it closes, so the
    image verdict can apply before any text renders.
    """
    def __init__(self):
        self.text = ""
        self.position = None
        self.visuals = None

    def feed(self, text):
        self.text += text
        if len(self.text) > 100_000:
            raise AIError("The provider response exceeded its limit.")
        if self.position is None and self.visuals is None:
            match = re.search(r'"visuals"\s*:\s*', self.text)
            if match:
                try:
                    item, _ = json.JSONDecoder().raw_decode(self.text, match.end())
                    self.visuals = item if isinstance(item, dict) else {}
                except ValueError:
                    pass
        if self.position is None:
            match = re.search(r'"blocks"\s*:\s*\[', self.text)
            if not match:
                return []
            self.position = match.end()
        out = []
        while True:
            while self.position < len(self.text) and self.text[self.position] in " \n\r\t,":
                self.position += 1
            try:
                item, end = json.JSONDecoder().raw_decode(self.text, self.position)
            except ValueError:
                return out
            if not isinstance(item, dict):
                raise AIError("The provider returned an invalid answer block.")
            out.append(item)
            self.position = end


def http_reason(status: int, label: str = "AI") -> str:
    # Never leak provider bodies, request prompts, or API keys into logs/UI.
    reason = {400: "provider refused the request (check the model name)", 401: "API key was rejected",
              402: "provider balance is exhausted", 403: "API key isn't allowed to use this model",
              404: "model or address wasn't found", 429: "provider is rate-limiting requests",
              529: "provider is overloaded"}.get(status, "provider is unavailable")
    return f"{label}: {reason} (HTTP {status})."


JSON_ONLY = "\n\nReply with the single JSON object only: no code fences and no text before or after it."


class Provider:
    def __init__(self, settings, client=None):
        self.settings = settings
        self.client = client

    async def _with_client(self, timeout, work):
        if self.client:
            return await work(self.client)
        async with httpx.AsyncClient(timeout=timeout, trust_env=False, verify=tls.context()) as client:
            return await work(client)

    # ------------------------------------------------------------- planning

    async def plan(self, system, payload, tools, max_output=650):
        r = resolve(self.settings)
        user = json.dumps(payload, ensure_ascii=False)
        if r.info.protocol == "anthropic":
            return await self._with_client(30, lambda client: self._plan_anthropic(client, r, system, user, tools, max_output))
        if r.info.protocol == "ollama":
            return await self._with_client(120, lambda client: self._plan_ollama(client, r, system, user, tools, max_output))
        body = {"model": r.model, "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
                "tools": tools, "tool_choice": "auto", "stream": False, **self._openai_extras(r, max_output)}

        async def request(client):
            response = await client.post(r.base + "/chat/completions", headers=self._openai_headers(r), json=body)
            if response.status_code != 200:
                raise AIError(f"The AI planner is unavailable (HTTP {response.status_code}).")
            try:
                data = response.json()
                choice = data["choices"][0]
                if choice["finish_reason"] not in ("stop", "tool_calls"):
                    raise AIError("The research plan reached its output limit. Try a narrower question.")
                return choice["message"], data.get("usage", {})
            except (ValueError, KeyError, IndexError, TypeError) as exc:
                raise AIError("The AI planner returned an invalid response.") from exc
        return await self._with_client(30, request)

    async def _plan_anthropic(self, client, r, system, user, tools, max_output):
        body = {"model": r.model, "max_tokens": max_output, "system": system,
                "messages": [{"role": "user", "content": user}],
                "tools": [{"name": t["function"]["name"], "description": t["function"].get("description", ""),
                           "input_schema": t["function"]["parameters"]} for t in tools],
                "tool_choice": {"type": "auto"}}
        response = await client.post(r.base + "/v1/messages", headers=self._anthropic_headers(r), json=body)
        if response.status_code != 200:
            raise AIError(f"The AI planner is unavailable (HTTP {response.status_code}).")
        try:
            data = response.json()
            if data.get("stop_reason") == "max_tokens":
                raise AIError("The research plan reached its output limit. Try a narrower question.")
            text, calls = "", []
            for block in data["content"]:
                if block.get("type") == "text":
                    text += block.get("text", "")
                elif block.get("type") == "tool_use":
                    calls.append({"id": block.get("id", ""), "type": "function", "function": {
                        "name": block["name"], "arguments": json.dumps(block.get("input") or {})}})
            return {"role": "assistant", "content": text, "tool_calls": calls}, self._anthropic_usage(data.get("usage") or {})
        except (ValueError, KeyError, TypeError, AttributeError) as exc:
            raise AIError("The AI planner returned an invalid response.") from exc

    async def _plan_ollama(self, client, r, system, user, tools, max_output):
        body = {"model": r.model, "stream": False, "tools": tools, "keep_alive": "10m",
                "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
                "options": {"num_ctx": self.settings.ollama_context, "num_predict": max_output}}
        response = await client.post(r.base + "/api/chat", json=body)
        if response.status_code == 400:
            # This local model can't call tools; research continues with the searches it already has.
            return {"role": "assistant", "content": "", "tool_calls": []}, {}
        if response.status_code != 200:
            raise AIError(f"Ollama couldn't plan the research (HTTP {response.status_code}).")
        try:
            data = response.json()
            message = data["message"]
            calls = [{"id": f"call_{i}", "type": "function", "function": {
                "name": call["function"]["name"],
                "arguments": json.dumps(call["function"].get("arguments") or {})}}
                for i, call in enumerate(message.get("tool_calls") or [])]
            return {"role": "assistant", "content": message.get("content", ""), "tool_calls": calls}, self._ollama_usage(data)
        except (ValueError, KeyError, TypeError, AttributeError) as exc:
            raise AIError("Ollama returned an invalid response.") from exc

    # ------------------------------------------------------------ answering

    async def generate(self, system, payload, max_output, on_block=None, on_visuals=None):
        ok, message = readiness(self.settings)
        if not ok:
            raise AIError(message)
        r = resolve(self.settings)
        user = json.dumps(payload, ensure_ascii=False)
        stream = {"anthropic": self._stream_anthropic, "ollama": self._stream_ollama}.get(r.info.protocol, self._stream_openai)
        timeout = 180 if r.info.protocol == "ollama" else 45
        return await self._with_client(timeout, lambda client: self._finish(stream(client, r, system, user, max_output), on_block, on_visuals))

    async def _finish(self, chunks, on_block, on_visuals):
        """Feed streamed text into the block parser; the stream yields (text, usage, finish)."""
        parser, usage, finish = JSONBlocks(), {}, None
        async for text, chunk_usage, chunk_finish in chunks:
            if chunk_usage:
                usage = {**usage, **chunk_usage}
            finish = chunk_finish or finish
            if not text:
                continue
            seen = parser.visuals is not None
            for block in parser.feed(text):
                if on_block:
                    on_block(block)
            if not seen and parser.visuals is not None and on_visuals:
                on_visuals(parser.visuals)
        if finish != "stop":
            raise AIError("The answer was interrupted or reached its output limit. Any visible text is incomplete.")
        try:
            output = loads_object(parser.text)
        except ValueError as exc:
            raise AIError("AI returned incomplete JSON. Try again.") from exc
        return output, usage

    async def _stream_openai(self, client, r, system, user, max_output):
        body = {"model": r.model, "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
                "stream": True, "stream_options": {"include_usage": True}, **self._openai_extras(r, max_output)}
        if r.provider == "custom":
            body["messages"][0]["content"] += JSON_ONLY  # not every compatible server supports JSON mode
        else:
            body["response_format"] = {"type": "json_object"}
        async with client.stream("POST", r.base + "/chat/completions", headers=self._openai_headers(r), json=body) as response:
            if response.status_code != 200:
                raise AIError(http_reason(response.status_code, r.info.label))
            async for line in response.aiter_lines():
                if not line.startswith("data:"):
                    continue
                raw = line[5:].strip()
                if raw == "[DONE]":
                    break
                try:
                    chunk = json.loads(raw)
                    usage = chunk.get("usage") or {}
                    choices = chunk.get("choices") or []
                    if not choices:
                        yield "", usage, None
                    for choice in choices:
                        yield (choice.get("delta") or {}).get("content") or "", usage, choice.get("finish_reason")
                except (ValueError, TypeError, KeyError, AttributeError) as exc:
                    raise AIError("AI returned an unreadable response. Try again.") from exc

    async def _stream_anthropic(self, client, r, system, user, max_output):
        body = {"model": r.model, "max_tokens": max_output, "stream": True,
                # Cached system prompt: repeat answers pay a tenth of the input price for it.
                "system": [{"type": "text", "text": system + JSON_ONLY, "cache_control": {"type": "ephemeral"}}],
                "messages": [{"role": "user", "content": user}]}
        async with client.stream("POST", r.base + "/v1/messages", headers=self._anthropic_headers(r), json=body) as response:
            if response.status_code != 200:
                raise AIError(http_reason(response.status_code, "Claude"))
            usage = {}
            async for line in response.aiter_lines():
                if not line.startswith("data:"):
                    continue
                try:
                    event = json.loads(line[5:].strip())
                    kind = event.get("type")
                    if kind == "message_start":
                        usage = dict(event["message"].get("usage") or {})
                        yield "", self._anthropic_usage(usage), None
                    elif kind == "content_block_delta" and event["delta"].get("type") == "text_delta":
                        yield event["delta"].get("text", ""), None, None
                    elif kind == "message_delta":
                        usage.update(event.get("usage") or {})
                        stop = event["delta"].get("stop_reason")
                        yield "", self._anthropic_usage(usage), {"end_turn": "stop", "stop_sequence": "stop"}.get(stop, stop)
                    elif kind == "error":
                        raise AIError("Claude stopped with an error: " + str((event.get("error") or {}).get("type", "unknown")) + ".")
                except (ValueError, TypeError, KeyError, AttributeError) as exc:
                    raise AIError("Claude returned an unreadable response. Try again.") from exc

    async def _stream_ollama(self, client, r, system, user, max_output):
        body = {"model": r.model, "stream": True, "format": "json", "keep_alive": "10m",
                "messages": [{"role": "system", "content": system + JSON_ONLY}, {"role": "user", "content": user}],
                "options": {"num_ctx": self.settings.ollama_context, "num_predict": max_output}}
        async with client.stream("POST", r.base + "/api/chat", json=body) as response:
            if response.status_code == 404:
                raise AIError(f"Ollama doesn't have “{r.model}”. Run `ollama pull {r.model}` or pick another model.")
            if response.status_code != 200:
                raise AIError(http_reason(response.status_code, "Ollama"))
            async for line in response.aiter_lines():
                if not line.strip():
                    continue
                try:
                    chunk = json.loads(line)
                    if chunk.get("error"):
                        raise AIError("Ollama stopped: " + str(chunk["error"])[:160])
                    text = (chunk.get("message") or {}).get("content") or ""
                    if chunk.get("done"):
                        yield text, self._ollama_usage(chunk), chunk.get("done_reason") or "stop"
                    else:
                        yield text, None, None
                except (ValueError, TypeError, AttributeError) as exc:
                    raise AIError("Ollama returned an unreadable response. Try again.") from exc

    # -------------------------------------------------------------- helpers

    def _openai_extras(self, r, max_output):
        if r.provider == "deepseek":
            return {"max_tokens": max_output, "thinking": {"type": "disabled"}}
        if r.provider == "openai":
            extras = {"max_completion_tokens": max_output}
            if re.match(r"^(gpt-5|o\d)", r.model):
                extras["reasoning_effort"] = "low"  # reasoning tokens count against the output cap
            return extras
        return {"max_tokens": max_output}

    @staticmethod
    def _openai_headers(r):
        headers = {"Authorization": "Bearer " + r.key} if r.key else {}
        if r.provider == "openrouter":
            headers.update({"HTTP-Referer": "https://github.com/Jashith127/Archer", "X-Title": "Archer"})
        return headers

    @staticmethod
    def _anthropic_headers(r):
        return {"x-api-key": r.key, "anthropic-version": "2023-06-01"}

    @staticmethod
    def _anthropic_usage(usage):
        read = usage.get("cache_read_input_tokens") or 0
        prompt = (usage.get("input_tokens") or 0) + read + (usage.get("cache_creation_input_tokens") or 0)
        out = {"prompt_tokens": prompt, "prompt_tokens_details": {"cached_tokens": read}}
        if "output_tokens" in usage:
            out["completion_tokens"] = usage["output_tokens"]
        return out

    @staticmethod
    def _ollama_usage(data):
        return {"prompt_tokens": int(data.get("prompt_eval_count") or 0), "completion_tokens": int(data.get("eval_count") or 0)}


async def list_models(client: httpx.AsyncClient, r: Resolved) -> tuple[int, list[str]]:
    """List a provider's models: a free call that also proves the key works. Returns (status, ids)."""
    if r.info.protocol == "anthropic":
        response = await client.get(r.base + "/v1/models", params={"limit": 100}, headers=Provider._anthropic_headers(r), timeout=8)
    elif r.info.protocol == "ollama":
        response = await client.get(r.base + "/api/tags", timeout=4)
        if response.status_code == 200:
            return 200, [m.get("name") for m in response.json().get("models", []) if isinstance(m, dict) and isinstance(m.get("name"), str)]
        return response.status_code, []
    else:
        response = await client.get(r.base + "/models", headers=Provider._openai_headers(r), timeout=8)
    if response.status_code != 200:
        return response.status_code, []
    return 200, [m.get("id") for m in response.json().get("data", []) if isinstance(m, dict) and isinstance(m.get("id"), str)]
