"""Persistent aggregate spending reservations. No queries or source text on disk."""
from datetime import datetime, timezone
import json
import math
from pathlib import Path

from archer.ai_provider import AIError, resolve
from archer.sources import modelprices


class Budget:
    def __init__(self, settings, directory: Path):
        self.settings = settings
        self.path = directory / "ai-usage.json"
        self.state = None

    def load(self):
        day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if self.state is None:
            try:
                self.state = json.loads(self.path.read_text(encoding="utf-8"))
                if not isinstance(self.state, dict) or not isinstance(self.state.get("day"), str):
                    raise ValueError()
                for key in ("tokens", "usd"):
                    value = self.state.get(key)
                    if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
                        raise ValueError()
            except FileNotFoundError:
                self.state = {"day": day, "tokens": 0, "usd": 0.0}
            except (OSError, ValueError, TypeError):
                self.state = None
                raise AIError("Archer couldn't read its AI usage ledger. Fix data/ai-usage.json before generating.")
        if self.state["day"] != day:
            self.state = {"day": day, "tokens": 0, "usd": 0.0}
        return self.state

    def save(self):
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.path.with_suffix(".tmp")
            temporary.write_text(json.dumps(self.state), encoding="utf-8")
            temporary.replace(self.path)
        except OSError as exc:
            raise AIError("Archer couldn't save its spending limit. AI generation is stopped.") from exc

    @property
    def priced(self):
        return self.rates is not None

    @property
    def rates(self):
        s = self.settings
        if s.ai_input_price is not None and s.ai_output_price is not None:
            return s.ai_input_price, s.ai_output_price, s.ai_input_price
        r = resolve(s)
        if r.provider == "ollama":
            return 0.0, 0.0, 0.0  # runs on this computer
        if r.provider == "deepseek" and r.base == "https://api.deepseek.com" and r.model == "deepseek-flash":
            # Official peak ceilings, reviewed 2026-10-14. Off-peak bills can be lower.
            return .30, 1.20, .006
        if r.provider in ("openai", "anthropic", "openrouter", "deepseek"):
            # OpenRouter's public list carries the labs' own list prices; unknown models keep token caps only.
            price = modelprices.cached_price(r.model)
            if price:
                return price.input, price.output, price.cached if price.cached is not None else price.input
        return None

    def cost(self, inputs, outputs, cached=0):
        if not self.rates:
            return 0.0
        incoming, outgoing, cache = self.rates
        cached = max(0, min(inputs, cached))
        return ((inputs-cached) * incoming + cached * cache + outputs * outgoing) / 1_000_000

    def reserve(self, run, inputs, outputs):
        state, s = self.load(), self.settings
        total = inputs + outputs
        charge = self.cost(inputs, outputs)
        if run["input"] + inputs > run["input_limit"] or run["output"] + outputs > run["output_limit"]:
            raise AIError("This run reached its token budget. Narrow the question and try again.")
        if state["tokens"] + total > s.ai_daily_tokens:
            raise AIError("Archer's daily AI token limit is reached (resets at 00:00 UTC).")
        if self.priced and (state["usd"] + charge > s.ai_daily_usd or run["usd"] + charge > s.ai_run_usd):
            raise AIError("Archer's AI spending limit is reached.")
        state["tokens"] += total
        state["usd"] += charge
        run["input"] += inputs
        run["output"] += outputs
        run["usd"] += charge
        self.save()  # Reserve before the request; keep conservative reservation on interrupted billing.
        return (state["day"], inputs, outputs, charge)

    def settle(self, run, reservation, usage):
        prompt, completion = usage.get("prompt_tokens"), usage.get("completion_tokens")
        if type(prompt) is not int or type(completion) is not int or min(prompt, completion) < 0:
            return
        day, inputs, outputs, charge = reservation
        cached = usage.get("prompt_cache_hit_tokens", usage.get("prompt_tokens_details", {}).get("cached_tokens", 0))
        delta = self.cost(prompt, completion, cached if type(cached) is int else 0) - charge
        state = self.load()
        if state["day"] == day:
            state["tokens"] = max(0, state["tokens"] + prompt + completion - inputs - outputs)
            state["usd"] = max(0, state["usd"] + delta)
        run["input"] += prompt - inputs
        run["output"] += completion - outputs
        run["usd"] += delta
        self.save()
