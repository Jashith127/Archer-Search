"""DeepSeek's chat-completions JSON stream, with bounded output and no hidden retries."""
import json
import re

import httpx


class AIError(Exception):
    pass


class JSONBlocks:
    """Yield complete JSON objects from the answer's blocks array, never partial HTML/text."""
    def __init__(self):
        self.text = ""
        self.position = None

    def feed(self, text):
        self.text += text
        if len(self.text) > 100_000:
            raise AIError("The provider response exceeded its limit.")
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


class Provider:
    def __init__(self, settings, client=None):
        self.settings = settings
        self.client = client

    async def plan(self, system, payload, tools, max_output=650):
        body = {"model": self.settings.ai_model, "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False)}],
            "tools": tools, "tool_choice": "auto", "max_tokens": max_output,
            "thinking": {"type": "disabled"}, "stream": False}
        async def request(client):
            response = await client.post(self.settings.ai_base_url.rstrip("/") + "/chat/completions",
                headers={"Authorization": "Bearer " + self.settings.deepseek_api_key}, json=body)
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
        if self.client:
            return await request(self.client)
        async with httpx.AsyncClient(timeout=25, trust_env=False) as client:
            return await request(client)

    async def generate(self, system, payload, max_output, on_block=None):
        s = self.settings
        if not s.deepseek_api_key:
            raise AIError("Add DEEPSEEK_API_KEY to .env and restart Archer to enable AI.")
        body = {
            "model": s.ai_model, "messages": [{"role": "system", "content": system},
                {"role": "user", "content": json.dumps(payload, ensure_ascii=False)}],
            "response_format": {"type": "json_object"}, "max_tokens": max_output,
            "thinking": {"type": "disabled"}, "stream": True,
            "stream_options": {"include_usage": True},
        }
        if self.client:
            return await self._request(self.client, body, on_block)
        async with httpx.AsyncClient(timeout=35, trust_env=False) as client:
            return await self._request(client, body, on_block)

    async def _request(self, client, body, on_block):
        parser, usage, finish = JSONBlocks(), {}, None
        async with client.stream("POST", self.settings.ai_base_url.rstrip("/") + "/chat/completions",
            headers={"Authorization": "Bearer " + self.settings.deepseek_api_key}, json=body) as response:
            if response.status_code != 200:
                # Never leak provider bodies, request prompts, or API keys into logs/UI.
                reason = {401: "API key was rejected", 402: "provider balance is exhausted",
                          429: "provider is rate-limiting requests"}.get(response.status_code, "provider is unavailable")
                raise AIError(f"AI {reason} (HTTP {response.status_code}).")
            async for line in response.aiter_lines():
                if not line.startswith("data:"):
                    continue
                raw = line[5:].strip()
                if raw == "[DONE]":
                    break
                try:
                    chunk = json.loads(raw)
                    if chunk.get("usage"):
                        usage = chunk["usage"]
                    for choice in chunk.get("choices", []):
                        finish = choice.get("finish_reason") or finish
                        content = choice.get("delta", {}).get("content") or ""
                        for block in parser.feed(content):
                            if on_block:
                                on_block(block)
                except (ValueError, TypeError, KeyError) as exc:
                    raise AIError("AI returned an unreadable response. Try again.") from exc
        if finish != "stop":
            raise AIError("The answer was interrupted or reached its output limit. Any visible text is incomplete.")
        try:
            output = json.loads(parser.text)
            if not isinstance(output, dict):
                raise ValueError()
        except ValueError as exc:
            raise AIError("AI returned incomplete JSON. Try again.") from exc
        return output, usage
