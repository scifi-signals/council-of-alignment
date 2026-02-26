"""Multi-model API dispatcher — OpenRouter or direct API routing."""

import asyncio
import time
import httpx
from anthropic import AsyncAnthropic
from openai import AsyncOpenAI
from config import (
    MODELS, DIRECT_MODEL_IDS, DIRECT_BASE_URLS,
    OPENROUTER_API_KEY, OPENROUTER_BASE_URL,
    use_openrouter, get_api_key_for_provider,
)

TIMEOUT = 120  # seconds


class ModelDispatcher:
    def __init__(self):
        self.total_tokens_in = 0
        self.total_tokens_out = 0
        self.total_cost = 0.0

    async def chat(self, model_key: str, messages: list[dict], system: str = None) -> dict:
        """Send messages to a model. Returns {"content": str, "tokens_in": int, "tokens_out": int, "cost": float}."""
        if use_openrouter():
            try:
                return await self._chat_openrouter(model_key, messages, system)
            except RuntimeError as e:
                if "402" in str(e) or "credits" in str(e).lower():
                    # OpenRouter credits exhausted — fall back to direct API
                    import logging
                    logging.getLogger(__name__).warning("OpenRouter credits low, falling back to direct API for %s", model_key)
                    return await self._chat_direct(model_key, messages, system)
                raise
        return await self._chat_direct(model_key, messages, system)

    async def _chat_openrouter(self, model_key: str, messages: list[dict], system: str = None) -> dict:
        """Route through OpenRouter (OpenAI-compatible)."""
        model_id = MODELS[model_key]["id"]
        payload = {"model": model_id, "messages": []}
        if system:
            payload["messages"].append({"role": "system", "content": system})
        payload["messages"].extend(messages)

        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            resp = await client.post(
                f"{OPENROUTER_BASE_URL}/chat/completions",
                headers={
                    "Authorization": f"Bearer {OPENROUTER_API_KEY}",
                    "Content-Type": "application/json",
                },
                json=payload,
            )
            if resp.status_code != 200:
                error_body = resp.text
                raise RuntimeError(f"OpenRouter {resp.status_code}: {error_body}")
            data = resp.json()

        content = data["choices"][0]["message"]["content"]
        usage = data.get("usage", {})
        tokens_in = usage.get("prompt_tokens", 0)
        tokens_out = usage.get("completion_tokens", 0)
        cost = self._estimate_cost(model_key, tokens_in, tokens_out)

        self.total_tokens_in += tokens_in
        self.total_tokens_out += tokens_out
        self.total_cost += cost

        return {"content": content, "tokens_in": tokens_in, "tokens_out": tokens_out, "cost": cost}

    async def _chat_direct(self, model_key: str, messages: list[dict], system: str = None) -> dict:
        """Route to provider's native API."""
        provider = MODELS[model_key]["provider"]
        api_key = get_api_key_for_provider(provider)
        if not api_key:
            raise ValueError(f"No API key for {provider}. Set {provider.upper()}_API_KEY or OPENROUTER_API_KEY.")

        if provider == "anthropic":
            return await self._chat_anthropic(model_key, messages, system, api_key)
        else:
            return await self._chat_openai_compat(model_key, messages, system, api_key, provider)

    async def _chat_anthropic(self, model_key: str, messages: list[dict], system: str, api_key: str) -> dict:
        """Claude via Anthropic SDK (different message format)."""
        client = AsyncAnthropic(api_key=api_key)
        kwargs = {
            "model": DIRECT_MODEL_IDS[model_key],
            "max_tokens": 16384,
            "messages": messages,
        }
        if system:
            kwargs["system"] = system

        resp = await client.messages.create(**kwargs)
        content = resp.content[0].text
        tokens_in = resp.usage.input_tokens
        tokens_out = resp.usage.output_tokens
        cost = self._estimate_cost(model_key, tokens_in, tokens_out)

        self.total_tokens_in += tokens_in
        self.total_tokens_out += tokens_out
        self.total_cost += cost

        return {"content": content, "tokens_in": tokens_in, "tokens_out": tokens_out, "cost": cost}

    async def _chat_openai_compat(self, model_key: str, messages: list[dict], system: str, api_key: str, provider: str) -> dict:
        """GPT, Gemini, Grok via OpenAI-compatible SDK."""
        base_url = DIRECT_BASE_URLS.get(provider)
        client = AsyncOpenAI(api_key=api_key, base_url=base_url)

        full_messages = []
        if system:
            full_messages.append({"role": "system", "content": system})
        full_messages.extend(messages)

        resp = await client.chat.completions.create(
            model=DIRECT_MODEL_IDS[model_key],
            messages=full_messages,
            max_tokens=16384,
        )
        content = resp.choices[0].message.content
        usage = resp.usage
        tokens_in = usage.prompt_tokens if usage else 0
        tokens_out = usage.completion_tokens if usage else 0
        cost = self._estimate_cost(model_key, tokens_in, tokens_out)

        self.total_tokens_in += tokens_in
        self.total_tokens_out += tokens_out
        self.total_cost += cost

        return {"content": content, "tokens_in": tokens_in, "tokens_out": tokens_out, "cost": cost}

    async def dispatch_to_council(
        self,
        council_models: list[str],
        system_prompt: str,
        briefing: str,
    ) -> dict[str, dict]:
        """Send briefing to all Council members in parallel.
        Returns {model_key: {"content": str, "tokens_in": int, "tokens_out": int, "cost": float}}
        """
        messages = [{"role": "user", "content": briefing}]

        async def _call(model_key: str) -> tuple[str, dict]:
            try:
                result = await self.chat(model_key, messages, system=system_prompt)
                return model_key, result
            except Exception as e:
                return model_key, {"content": f"[Error from {MODELS[model_key]['name']}: {e}]", "tokens_in": 0, "tokens_out": 0, "cost": 0}

        results = await asyncio.gather(*[_call(m) for m in council_models])
        return dict(results)

    def _estimate_cost(self, model_key: str, tokens_in: int, tokens_out: int) -> float:
        """Rough cost estimate per model (dollars)."""
        # Approximate $/1M token rates (input/output)
        rates = {
            "claude": (3.0, 15.0),
            "chatgpt": (2.0, 8.0),
            "gemini": (1.25, 10.0),
            "grok": (3.0, 15.0),
        }
        in_rate, out_rate = rates.get(model_key, (3.0, 15.0))
        return (tokens_in * in_rate + tokens_out * out_rate) / 1_000_000

    def get_cost_summary(self) -> str:
        return f"Tokens: {self.total_tokens_in:,} in / {self.total_tokens_out:,} out | Est. cost: ${self.total_cost:.4f}"
