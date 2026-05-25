# /// script
# requires-python = ">=3.11"
# dependencies = ["httpx"]
# ///
"""Reusable LLM client with a model-rotation "quota solver" (BYO credential).

This is the open, reusable piece of the LLM machinery — the *technique* for
surviving free-tier rate limits, not a secret. The credential is never here:
operators supply their own and nothing is committed.

Two engines:
  * "api" (default when GEMINI_API_KEY is set): direct Gemini REST call —
    one fast round-trip, optional Google Search grounding. Best for the unattended
    producer; the agentic CLI is far too slow for simple structured queries.
  * "cli": shells out to the `gemini` CLI (OAuth via ~/.gemini/), for operators
    who prefer OAuth over an API key.

Quota solver: try the first model in the ladder; on a rate-limit / quota / 503
high-demand / model-unavailable response, rotate to the next model and back off
exponentially. Lets a single free-tier account stretch across many calls.

Configuration (env):
  GEMINI_API_KEY      enables the "api" engine (the producer secret)
  TECHNL_LLM_ENGINE   force "api" | "cli" | "mock" (else auto: api if key else cli)
  GEMINI_MODELS       comma-separated model ladder (overrides the default)
  GEMINI_BIN          gemini CLI binary (cli engine; default "gemini")

Programmatic mock (for tests): set llm.MOCK_RESPONDER to a callable
(prompt, model) -> str | RateLimit-raising, and TECHNL_LLM_ENGINE=mock.
"""

import os
import re
import subprocess
import time

API_BASE = "https://generativelanguage.googleapis.com/v1beta/models"

# Default model ladder, ordered cheapest-on-quota first so light models absorb most
# calls and we only rotate up to heavier ones when limited. Override per-operator with
# GEMINI_MODELS (their account's actual access may differ — probe it). NB: on the OAuth
# Code Assist tier the daily quota is largely shared across models, so a longer ladder
# adds resilience to transient/per-model limits, not more total daily capacity.
# Deliberately excludes gemini-3.5-flash (heaviest quota consumer).
DEFAULT_LADDER = ["gemini-3.1-flash-lite", "gemini-3-flash-preview", "gemini-2.5-flash"]

# Substrings that mean "try a different model / back off" — rate limits, transient
# server errors, AND model-availability errors (so the ladder degrades instead of
# hard-failing; important on the free tier).
RATE_LIMIT_SIGNS = ["429", "quota", "rate limit", "resource_exhausted",
                    "exhausted", "overloaded", "unavailable", "try again",
                    "high demand", "503", "500", "internal error",
                    "not found", "does not exist", "not supported", "unsupported",
                    "no such model", "invalid model", "404"]

# Test hook: callable(prompt, model) -> str; raise RateLimited to simulate limits.
MOCK_RESPONDER = None


class RateLimited(Exception):
    """Raised by an engine when the call hit a quota/rate limit (retry/rotate)."""


class LLMError(Exception):
    """Non-retryable failure (CLI missing, bad invocation, etc.)."""


def model_ladder() -> list[str]:
    env = os.environ.get("GEMINI_MODELS", "").strip()
    return [m.strip() for m in env.split(",") if m.strip()] or list(DEFAULT_LADDER)


def _looks_rate_limited(text: str) -> bool:
    lo = (text or "").lower()
    return any(sign in lo for sign in RATE_LIMIT_SIGNS)


def _api_engine(prompt: str, model: str, system: str | None, timeout: int, ground: bool) -> str:
    """Direct Gemini REST call. One round-trip; optional Google Search grounding."""
    import httpx
    key = os.environ.get("GEMINI_API_KEY")
    if not key:
        raise LLMError("GEMINI_API_KEY not set (required for the api engine)")
    body: dict = {"contents": [{"parts": [{"text": prompt}]}]}
    if system:
        body["systemInstruction"] = {"parts": [{"text": system}]}
    if ground:
        body["tools"] = [{"google_search": {}}]
    try:
        r = httpx.post(f"{API_BASE}/{model}:generateContent",
                       params={"key": key}, json=body, timeout=timeout)
    except httpx.TimeoutException as e:
        raise RateLimited(f"api timed out after {timeout}s") from e
    except Exception as e:
        raise LLMError(f"api request failed: {e}") from e

    if r.status_code != 200:
        if r.status_code in (429, 500, 503) or _looks_rate_limited(r.text):
            raise RateLimited(f"HTTP {r.status_code}: {r.text[:120]}")
        raise LLMError(f"api HTTP {r.status_code}: {r.text[:200]}")
    try:
        cand = r.json()["candidates"][0]
        text = "".join(p.get("text", "") for p in cand["content"]["parts"]).strip()
    except Exception as e:
        raise LLMError(f"unparseable api response: {r.text[:200]}") from e
    if not text:
        raise RateLimited("empty api response")
    return text


def _cli_engine(prompt: str, model: str, system: str | None, timeout: int, ground: bool) -> str:
    """Invoke the gemini CLI non-interactively. Raise RateLimited / LLMError."""
    gemini = os.environ.get("GEMINI_BIN", "gemini")
    full = f"{system}\n\n{prompt}" if system else prompt
    # --skip-trust: required for headless/automated use (no interactive folder-trust prompt).
    cmd = [gemini, "--skip-trust", "-m", model, "-p", full, "--yolo"]
    # Force the OAuth / Code Assist path: select GCA and drop any stray API key so
    # the CLI authenticates with ~/.gemini/oauth_creds.json (refresh-token renewed).
    env = {**os.environ, "GOOGLE_GENAI_USE_GCA": "true", "GEMINI_CLI_TRUST_WORKSPACE": "true"}
    env.pop("GEMINI_API_KEY", None)
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, env=env)
    except FileNotFoundError as e:
        raise LLMError(f"gemini CLI not found ({gemini}); is it installed + logged in?") from e
    except subprocess.TimeoutExpired as e:
        raise RateLimited(f"gemini CLI timed out after {timeout}s") from e

    out, err = (proc.stdout or "").strip(), (proc.stderr or "").strip()
    if proc.returncode != 0:
        if _looks_rate_limited(err) or _looks_rate_limited(out):
            raise RateLimited(err or out)
        raise LLMError(f"gemini CLI exited {proc.returncode}: {err[:300]}")
    if not out or _looks_rate_limited(out):
        raise RateLimited(out or "empty response (suspected quota abort)")
    return out


def _mock_engine(prompt: str, model: str, system: str | None, timeout: int, ground: bool) -> str:
    if MOCK_RESPONDER is None:
        raise LLMError("TECHNL_LLM_ENGINE=mock but llm.MOCK_RESPONDER is unset")
    return MOCK_RESPONDER(prompt, model)


def _engine():
    forced = os.environ.get("TECHNL_LLM_ENGINE")
    if forced == "mock":
        return _mock_engine
    if forced == "cli":
        return _cli_engine
    if forced == "api":
        return _api_engine
    # auto: prefer the fast direct API when a key is present, else the CLI (OAuth).
    return _api_engine if os.environ.get("GEMINI_API_KEY") else _cli_engine


def llm_call(prompt: str, system: str | None = None, *, ladder: list[str] | None = None,
             ground: bool = False, max_attempts: int = 6, base_backoff: float = 8.0,
             timeout: int = 120, sleep=time.sleep) -> str:
    """Call the LLM with model rotation + exponential backoff. Returns text.

    ground=True enables Google Search grounding (api engine) for web-lookup tasks.
    Raises LLMError if every attempt fails.
    """
    ladder = ladder or model_ladder()
    engine = _engine()
    backoff = base_backoff
    last_err = None

    for attempt in range(max_attempts):
        model = ladder[attempt % len(ladder)]
        try:
            return engine(prompt, model, system, timeout, ground)
        except RateLimited as e:
            last_err = e
            if attempt < max_attempts - 1:
                print(f"  [llm] {model} rate-limited ({str(e)[:60]}); "
                      f"rotating + backing off {backoff:.0f}s")
                sleep(backoff)
                backoff *= 2
        except LLMError as e:
            last_err = e
            break  # non-retryable

    raise LLMError(f"all {max_attempts} attempts failed; last: {last_err}")


if __name__ == "__main__":
    import sys
    print(llm_call(sys.argv[1] if len(sys.argv) > 1 else "Say OK in one word."))
