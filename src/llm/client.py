from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Awaitable, Callable

import httpx

from src.config import (
    ANTHROPIC_API_KEY,
    ANTHROPIC_MODEL,
    LLM_PROVIDER,
    OPENROUTER_API_KEY,
    OPENROUTER_BACKUP_KEY,
    OPENROUTER_BASE_URL,
    OPENROUTER_MODEL,
)

logger = logging.getLogger(__name__)

RETRY_DELAYS = [1, 2, 4, 8, 16, 32, 60]
MAX_TOTAL_WAIT = 300  # 5 minutes


class LLMError(Exception):
    pass


# ── OpenRouter Client (OpenAI-compatible) ────────────────────


class OpenRouterClient:
    """LLM client for OpenRouter (OpenAI-compatible chat/completions API).

    Supports primary + backup API key with automatic failover.
    """

    def __init__(
        self,
        api_key: str = OPENROUTER_API_KEY,
        backup_key: str = OPENROUTER_BACKUP_KEY,
        base_url: str = OPENROUTER_BASE_URL,
        model: str = OPENROUTER_MODEL,
    ):
        self.api_keys = [k for k in [api_key, backup_key] if k]
        if not self.api_keys:
            raise LLMError("No OpenRouter API keys configured")
        self.base_url = base_url.rstrip("/")
        self.model = model

    async def _request(
        self,
        messages: list[dict],
        api_key: str,
        max_tokens: int = 8192,
        temperature: float = 0.3,
        tools: list[dict] | None = None,
    ) -> dict:
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://compliance152.local",
        }
        body: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        if tools:
            body["tools"] = tools

        async with httpx.AsyncClient(timeout=300) as client:
            resp = await client.post(
                f"{self.base_url}/chat/completions",
                headers=headers,
                json=body,
            )
            if resp.status_code == 429:
                raise _RateLimitError()
            if resp.status_code >= 500:
                raise _ServerError(resp.status_code)
            if resp.status_code == 401 or resp.status_code == 403:
                raise _AuthError(f"Auth failed ({resp.status_code})")
            if resp.status_code != 200:
                detail = resp.text[:500]
                raise LLMError(f"API error {resp.status_code}: {detail}")
            return resp.json()

    async def call(
        self,
        system_prompt: str,
        user_prompt: str,
        max_tokens: int = 8192,
        temperature: float = 0.3,
    ) -> str:
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]
        return await self._call_with_failover(messages, max_tokens, temperature)

    async def _call_with_failover(
        self,
        messages: list[dict],
        max_tokens: int,
        temperature: float,
        tools: list[dict] | None = None,
    ) -> str | dict:
        """Try primary key, on auth error switch to backup."""
        last_error: Exception | None = None

        for key_idx, api_key in enumerate(self.api_keys):
            key_label = "primary" if key_idx == 0 else "backup"
            total_waited = 0

            for attempt, delay in enumerate(RETRY_DELAYS):
                try:
                    data = await self._request(
                        messages, api_key, max_tokens, temperature, tools,
                    )
                    text = self._extract_text(data)
                    usage = data.get("usage", {})
                    logger.debug(
                        "LLM call OK (%s key): %d input, %d output tokens",
                        key_label,
                        usage.get("prompt_tokens", 0),
                        usage.get("completion_tokens", 0),
                    )
                    if tools:
                        return data  # return raw for tool handling
                    return text

                except _AuthError as e:
                    logger.warning("Auth error with %s key: %s", key_label, e)
                    last_error = e
                    break  # switch to next key

                except _RateLimitError:
                    total_waited += delay
                    if total_waited > MAX_TOTAL_WAIT:
                        logger.warning("Rate limit exceeded on %s key", key_label)
                        last_error = LLMError("Rate limit exceeded")
                        break  # switch to next key
                    logger.warning(
                        "Rate limited (%s key), retrying in %ds (attempt %d)",
                        key_label, delay, attempt + 1,
                    )
                    await asyncio.sleep(delay)

                except _ServerError as e:
                    total_waited += delay
                    if total_waited > MAX_TOTAL_WAIT:
                        last_error = LLMError(f"Server error {e.status_code}")
                        break
                    logger.warning(
                        "Server error %d (%s key), retrying in %ds",
                        e.status_code, key_label, delay,
                    )
                    await asyncio.sleep(delay)

                except (httpx.ConnectError, httpx.ReadTimeout):
                    total_waited += delay
                    if total_waited > MAX_TOTAL_WAIT:
                        last_error = LLMError("Connection error")
                        break
                    logger.warning("Connection error (%s key), retrying in %ds", key_label, delay)
                    await asyncio.sleep(delay)

        raise LLMError(f"All API keys exhausted. Last error: {last_error}")

    async def call_with_tools(
        self,
        system_prompt: str,
        user_prompt: str,
        tools: list[dict],
        tool_executor: Callable[[str, dict], Awaitable[str]],
        max_turns: int = 10,
        max_tokens: int = 8192,
        temperature: float = 0.3,
    ) -> str:
        # Convert Anthropic tool format to OpenAI tool format
        openai_tools = [_anthropic_tool_to_openai(t) for t in tools]

        messages: list[dict[str, Any]] = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]

        for turn in range(max_turns):
            data = await self._call_with_failover(
                messages, max_tokens, temperature, tools=openai_tools,
            )
            if isinstance(data, str):
                return data

            choice = data.get("choices", [{}])[0]
            message = choice.get("message", {})
            finish_reason = choice.get("finish_reason", "stop")

            # Add assistant message to history
            messages.append(message)

            tool_calls = message.get("tool_calls")
            if not tool_calls or finish_reason == "stop":
                return message.get("content", "") or ""

            # Execute tool calls
            for tc in tool_calls:
                func = tc.get("function", {})
                tool_name = func.get("name", "")
                try:
                    tool_input = json.loads(func.get("arguments", "{}"))
                except json.JSONDecodeError:
                    tool_input = {}

                logger.info("Tool call: %s(%s)", tool_name, str(tool_input)[:100])
                try:
                    result = await tool_executor(tool_name, tool_input)
                except Exception as e:
                    logger.error("Tool execution failed: %s", e)
                    result = f"[Error executing tool: {e}]"

                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.get("id", ""),
                    "content": result[:10000],
                })

        logger.warning("Max turns (%d) reached in tool call loop", max_turns)
        return messages[-1].get("content", "") if messages else ""

    @staticmethod
    def _extract_text(data: dict) -> str:
        choices = data.get("choices", [])
        if not choices:
            return ""
        return choices[0].get("message", {}).get("content", "") or ""


# ── Anthropic Client (native SDK) ───────────────────────────


class AnthropicClient:
    """LLM client using the official Anthropic SDK (for direct Anthropic API access)."""

    def __init__(
        self,
        api_key: str = ANTHROPIC_API_KEY,
        model: str = ANTHROPIC_MODEL,
    ):
        import anthropic
        self._anthropic = anthropic
        self.client = anthropic.AsyncAnthropic(api_key=api_key)
        self.model = model

    async def call(
        self,
        system_prompt: str,
        user_prompt: str,
        max_tokens: int = 8192,
        temperature: float = 0.3,
    ) -> str:
        total_waited = 0

        for attempt, delay in enumerate(RETRY_DELAYS):
            try:
                response = await self.client.messages.create(
                    model=self.model,
                    max_tokens=max_tokens,
                    temperature=temperature,
                    system=system_prompt,
                    messages=[{"role": "user", "content": user_prompt}],
                )
                text = response.content[0].text
                logger.debug(
                    "LLM call OK (Anthropic): %d input tokens, %d output tokens",
                    response.usage.input_tokens,
                    response.usage.output_tokens,
                )
                return text

            except self._anthropic.RateLimitError:
                total_waited += delay
                if total_waited > MAX_TOTAL_WAIT:
                    raise LLMError("Rate limit exceeded, max wait time reached")
                logger.warning("Rate limited, retrying in %ds (attempt %d)", delay, attempt + 1)
                await asyncio.sleep(delay)

            except self._anthropic.APIStatusError as e:
                if e.status_code >= 500:
                    total_waited += delay
                    if total_waited > MAX_TOTAL_WAIT:
                        raise LLMError(f"Server error {e.status_code}, max wait time reached")
                    logger.warning("Server error %d, retrying in %ds", e.status_code, delay)
                    await asyncio.sleep(delay)
                else:
                    raise LLMError(f"API error: {e.status_code} {e.message}") from e

            except self._anthropic.APIConnectionError:
                total_waited += delay
                if total_waited > MAX_TOTAL_WAIT:
                    raise LLMError("Connection error, max wait time reached")
                logger.warning("Connection error, retrying in %ds", delay)
                await asyncio.sleep(delay)

        raise LLMError("All retry attempts exhausted")

    async def call_with_tools(
        self,
        system_prompt: str,
        user_prompt: str,
        tools: list[dict],
        tool_executor: Callable[[str, dict], Awaitable[str]],
        max_turns: int = 10,
        max_tokens: int = 8192,
        temperature: float = 0.3,
    ) -> str:
        messages: list[dict[str, Any]] = [{"role": "user", "content": user_prompt}]

        response = None
        for turn in range(max_turns):
            response = await self.client.messages.create(
                model=self.model,
                max_tokens=max_tokens,
                temperature=temperature,
                system=system_prompt,
                tools=tools,
                messages=messages,
            )

            if response.stop_reason == "end_turn":
                return self._extract_text(response)

            if response.stop_reason == "tool_use":
                messages.append({"role": "assistant", "content": response.content})

                tool_results = []
                for block in response.content:
                    if block.type == "tool_use":
                        logger.info("Tool call: %s(%s)", block.name, str(block.input)[:100])
                        try:
                            result = await tool_executor(block.name, block.input)
                        except Exception as e:
                            logger.error("Tool execution failed: %s", e)
                            result = f"[Error executing tool: {e}]"

                        tool_results.append({
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": result[:10000],
                        })

                messages.append({"role": "user", "content": tool_results})
            else:
                return self._extract_text(response)

        logger.warning("Max turns (%d) reached in tool call loop", max_turns)
        return self._extract_text(response) if response else ""

    @staticmethod
    def _extract_text(response) -> str:
        parts = []
        for block in response.content:
            if hasattr(block, "text"):
                parts.append(block.text)
        return "\n".join(parts) if parts else ""


# ── Internal helpers ─────────────────────────────────────────


class _RateLimitError(Exception):
    pass


class _ServerError(Exception):
    def __init__(self, status_code: int):
        self.status_code = status_code
        super().__init__(f"Server error {status_code}")


class _AuthError(Exception):
    pass


def _anthropic_tool_to_openai(tool: dict) -> dict:
    """Convert Anthropic tool definition to OpenAI function-calling format."""
    return {
        "type": "function",
        "function": {
            "name": tool.get("name", ""),
            "description": tool.get("description", ""),
            "parameters": tool.get("input_schema", {}),
        },
    }


# ── Factory & convenience functions ──────────────────────────


_client: OpenRouterClient | AnthropicClient | None = None


def get_client() -> OpenRouterClient | AnthropicClient:
    global _client
    if _client is None:
        if LLM_PROVIDER == "anthropic":
            if not ANTHROPIC_API_KEY:
                raise LLMError("ANTHROPIC_API_KEY is required when LLM_PROVIDER=anthropic")
            logger.info("Using Anthropic client (model: %s)", ANTHROPIC_MODEL)
            _client = AnthropicClient()
        else:
            if not OPENROUTER_API_KEY:
                raise LLMError("OPENROUTER_API_KEY is required when LLM_PROVIDER=openrouter")
            backup = "yes" if OPENROUTER_BACKUP_KEY else "no"
            logger.info(
                "Using OpenRouter client (model: %s, backup key: %s)",
                OPENROUTER_MODEL, backup,
            )
            _client = OpenRouterClient()
    return _client


async def call_llm(
    system_prompt: str,
    user_prompt: str,
    max_tokens: int = 8192,
    temperature: float = 0.3,
) -> str:
    return await get_client().call(system_prompt, user_prompt, max_tokens, temperature)


async def call_llm_with_tools(
    system_prompt: str,
    user_prompt: str,
    tools: list[dict],
    tool_executor: Callable[[str, dict], Awaitable[str]],
    max_turns: int = 10,
    max_tokens: int = 8192,
    temperature: float = 0.3,
) -> str:
    """Convenience function for agentic tool-use calls."""
    return await get_client().call_with_tools(
        system_prompt, user_prompt, tools, tool_executor,
        max_turns, max_tokens, temperature,
    )
