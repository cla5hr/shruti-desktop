"""LLM chat client. One function, three transports:

- stub    — deterministic output for tests (LLM_MODE=stub)
- ollama  — native /api/chat so options.num_ctx APPLIES (ollama's OpenAI-compat
            endpoint ignores context size and silently truncates at 4096!)
- openai  — standard /v1/chat/completions (llama.cpp on the Spark sets its
            context at launch, so no per-request ctx needed)
"""

import httpx

from shruti_core.settings import get_settings


class LLMError(RuntimeError):
    pass


def estimate_tokens(text: str) -> int:
    """Conservative heuristic (~3 chars/token for English) — used for ctx budgeting."""
    return max(1, len(text) // 3)


def _is_ollama(base_url: str) -> bool:
    return ":11434" in base_url


def _auth_headers() -> dict | None:
    key = get_settings().llm_api_key
    return {"Authorization": f"Bearer {key}"} if key else None


def chat(
    messages: list[dict],
    *,
    max_tokens: int = 1400,
    temperature: float = 0.3,
    timeout: float = 600.0,
) -> str:
    settings = get_settings()
    if settings.llm_mode == "stub":
        return _stub(messages)

    base = settings.llm_base_url.rstrip("/")
    try:
        if _is_ollama(base):
            root = base[: -len("/v1")] if base.endswith("/v1") else base
            resp = httpx.post(
                f"{root}/api/chat",
                json={
                    "model": settings.llm_model,
                    "messages": messages,
                    "stream": False,
                    "options": {
                        "num_ctx": settings.llm_max_ctx,
                        "temperature": temperature,
                        "num_predict": max_tokens,
                    },
                },
                timeout=timeout,
            )
            resp.raise_for_status()
            return resp.json()["message"]["content"]

        resp = httpx.post(
            f"{base}/chat/completions",
            json={
                "model": settings.llm_model,
                "messages": messages,
                "max_tokens": max_tokens,
                "temperature": temperature,
            },
            headers=_auth_headers(),
            timeout=timeout,
        )
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"]
    except httpx.HTTPError as exc:
        raise LLMError(
            f"LLM request failed ({exc.__class__.__name__}) - is {base} up "
            f"and model {settings.llm_model!r} pulled?"
        ) from exc


def chat_stream(
    messages: list[dict],
    *,
    max_tokens: int = 1200,
    temperature: float = 0.3,
    timeout: float = 600.0,
):
    """Yield content deltas. Same three transports as chat()."""
    import json as _json

    settings = get_settings()
    if settings.llm_mode == "stub":
        answer = _stub_answer(messages)
        for i in range(0, len(answer), 24):
            yield answer[i : i + 24]
        return

    base = settings.llm_base_url.rstrip("/")
    try:
        if _is_ollama(base):
            root = base[: -len("/v1")] if base.endswith("/v1") else base
            with httpx.stream(
                "POST",
                f"{root}/api/chat",
                json={
                    "model": settings.llm_model,
                    "messages": messages,
                    "stream": True,
                    "options": {
                        "num_ctx": settings.llm_max_ctx,
                        "temperature": temperature,
                        "num_predict": max_tokens,
                    },
                },
                timeout=timeout,
            ) as resp:
                resp.raise_for_status()
                for line in resp.iter_lines():
                    if not line:
                        continue
                    payload = _json.loads(line)
                    delta = payload.get("message", {}).get("content", "")
                    if delta:
                        yield delta
                    if payload.get("done"):
                        return
        else:
            with httpx.stream(
                "POST",
                f"{base}/chat/completions",
                json={
                    "model": settings.llm_model,
                    "messages": messages,
                    "max_tokens": max_tokens,
                    "temperature": temperature,
                    "stream": True,
                },
                headers=_auth_headers(),
                timeout=timeout,
            ) as resp:
                resp.raise_for_status()
                for line in resp.iter_lines():
                    if not line or not line.startswith("data:"):
                        continue
                    data = line[len("data:") :].strip()
                    if data == "[DONE]":
                        return
                    delta = (
                        _json.loads(data).get("choices", [{}])[0].get("delta", {}).get("content")
                    )
                    if delta:
                        yield delta
    except httpx.HTTPError as exc:
        raise LLMError(
            f"LLM stream failed ({exc.__class__.__name__}) - is {base} up "
            f"and model {settings.llm_model!r} pulled?"
        ) from exc


def _stub_answer(messages: list[dict]) -> str:
    question = next((m["content"] for m in reversed(messages) if m.get("role") == "user"), "")
    return (
        f"Stub answer to: {question[:80]} — the immediate priority is the avionics "
        "bench test [0:28]. Also see the budget report action [0:36]."
    )


def _stub(messages: list[dict]) -> str:
    user_chars = sum(len(m.get("content", "")) for m in messages if m.get("role") == "user")
    return (
        "## Summary\n\nStub minutes generated for testing.\n\n"
        "## Key Points\n\n- Stub point one\n- Stub point two\n\n"
        "## Decisions\n\n- Stub decision\n\n"
        "## Action Items\n\n- **Stub Owner** — stub action item\n\n"
        "## Next Steps\n\n- Stub next step\n\n"
        f"<!-- stub:prompt_chars={user_chars} -->"
    )
