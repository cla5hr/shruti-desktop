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


def list_models(base_url: str, api_key: str = "", timeout: float = 8.0) -> tuple[list[str], str]:
    """Model names a provider offers, as (models, error_detail).

    Tries the OpenAI-standard `GET /v1/models` first — Groq, OpenAI, llama.cpp,
    vLLM and Ollama's compat layer all implement it — then falls back to Ollama's
    native `/api/tags` for older servers. Powers the model dropdown in Settings,
    so people pick from a list instead of typing an exact id from memory."""
    base = (base_url or "").strip().rstrip("/")
    if not base.startswith(("http://", "https://")):
        return [], "Enter the base URL first (http:// or https://, with the port if any)"
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else None
    limits = httpx.Timeout(timeout, connect=5.0)
    root = base[: -len("/v1")] if base.endswith("/v1") else base
    attempts = [(f"{base}/models", "openai"), (f"{root}/api/tags", "ollama")]
    detail = ""
    for url, shape in attempts:
        try:
            resp = httpx.get(url, headers=headers, timeout=limits)
            resp.raise_for_status()
            payload = resp.json()
            if shape == "openai":
                names = [m["id"] for m in payload.get("data", []) if m.get("id")]
            else:
                names = [m["name"] for m in payload.get("models", []) if m.get("name")]
            if names:
                # embedding-only models can't chat — offering them just invites a
                # failed summary later
                return sorted(n for n in names if "embed" not in n.lower()), ""
            detail = "The server returned an empty model list"
        except httpx.HTTPStatusError as exc:
            code = exc.response.status_code
            detail = (
                f"The API key was rejected (HTTP {code})"
                if code in (401, 403)
                else f"Server replied with HTTP {code}"
            )
            if code in (401, 403):
                break  # a bad key won't work on the fallback either
        except httpx.ConnectError:
            detail = "Could not reach that URL — check the address, port and that it's running"
            break
        except httpx.TimeoutException:
            detail = f"No reply within {timeout:.0f}s — server busy or a firewall in between"
            break
        except Exception as exc:
            detail = f"Unexpected error: {exc.__class__.__name__}"
    return [], detail or "This server doesn't publish a model list — type the model name"


def probe(base_url: str, model: str, api_key: str = "", timeout: float = 45.0) -> tuple[bool, str]:
    """Test a provider config WITHOUT saving it: one tiny chat call. Returns
    (ok, human-readable detail) — powers the Settings 'Test connection' button.

    The read timeout is generous on purpose: a 20-30B model cold-loading into a
    GPU server can take ~30s for its FIRST reply, and the old 10s limit reported
    a perfectly good server as unreachable. Connection failures still surface in
    seconds via the separate connect timeout."""
    base = (base_url or "").strip().rstrip("/")
    if not base.startswith(("http://", "https://")):
        return False, "Base URL must start with http:// or https:// (include the port if any)"
    if not model.strip():
        return False, "Enter a model name"
    messages = [{"role": "user", "content": "Reply with exactly: OK"}]
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else None
    limits = httpx.Timeout(timeout, connect=5.0)
    try:
        if _is_ollama(base):
            root = base[: -len("/v1")] if base.endswith("/v1") else base
            # quick pre-check: Ollama-family servers list their models, which gives a
            # far better message than a bare 404 from the chat endpoint
            try:
                tags = httpx.get(f"{root}/api/tags", timeout=4.0)
                tags.raise_for_status()
                installed = [m["name"] for m in tags.json().get("models", [])]
                if installed and model not in installed:
                    have = ", ".join(installed[:8])
                    more = "…" if len(installed) > 8 else ""
                    return False, (
                        f"Server reached, but it doesn't have '{model}'. "
                        f"Available: {have}{more}"
                    )
            except httpx.HTTPError:
                pass  # proxied/older servers may hide /api/tags — let the chat call decide
            resp = httpx.post(
                f"{root}/api/chat",
                json={
                    "model": model,
                    "messages": messages,
                    "stream": False,
                    "options": {"num_predict": 8},
                },
                timeout=limits,
            )
            resp.raise_for_status()
            reply = resp.json()["message"]["content"]
        else:
            resp = httpx.post(
                f"{base}/chat/completions",
                json={"model": model, "messages": messages, "max_tokens": 8},
                headers=headers,
                timeout=limits,
            )
            resp.raise_for_status()
            reply = resp.json()["choices"][0]["message"]["content"]
        return True, f"Connected — {model} replied: {reply.strip()[:40] or 'OK'}"
    except httpx.HTTPStatusError as exc:
        code = exc.response.status_code
        if code in (401, 403):
            return False, f"Server reached, but the API key was rejected (HTTP {code})"
        if code == 404:
            return False, (
                "Server reached, but model or path not found (HTTP 404) — check the "
                "model name and that the URL ends in /v1"
            )
        return False, f"Server replied with HTTP {code}"
    except httpx.ConnectError:
        return False, (
            "Could not reach that URL — check the address and port, and that the "
            "server is running"
        )
    except httpx.ConnectTimeout:
        return False, (
            "Could not reach that URL (connect timed out) — check the address and "
            "port, and that a firewall isn't blocking it"
        )
    except httpx.TimeoutException:
        return False, (
            f"Server reached, but no reply within {timeout:.0f}s — a large model "
            "may still be loading into memory; wait a minute and test again"
        )
    except Exception as exc:
        return False, f"{exc.__class__.__name__}: {exc}"


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
