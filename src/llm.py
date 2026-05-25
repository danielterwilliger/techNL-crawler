# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Reusable LLM client with a model-rotation "quota solver" (BYO credential).

This is the open, reusable piece of the LLM machinery — the *technique* for
surviving free-tier rate limits, not a secret. The credential itself is never
here: the default engine shells out to the `gemini` CLI, which authenticates via
its own OAuth (`~/.gemini/`). Operators (you, or anyone who forks this) supply
their own login; nothing is committed.

Quota solver: try the first model in the ladder; on a rate-limit / quota / clean
abort, rotate to the next model and back off exponentially. This lets a single
free-tier account stretch much further across many calls.

Configuration (env):
  TECHNL_LLM_ENGINE   "cli" (default) | "mock"
  GEMINI_MODELS       comma-separated model ladder (overrides the default)
  GEMINI_BIN          gemini CLI binary name/path (default "gemini")

Programmatic mock (for tests): set llm.MOCK_RESPONDER to a callable
(prompt, model) -> str | RateLimit-raising, and TECHNL_LLM_ENGINE=mock.
"""

import os
import re
import subprocess
import time

DEFAULT_LADDER = ["gemini-3.5-flash", "gemini-3.1-flash-lite",
                  "gemini-2.5-flash", "gemini-2.0-flash"]

# Substrings that mean "try a different model / back off" — rate limits AND
# model-availability errors (so an invalid/unavailable model rotates down the
# ladder instead of hard-failing the whole call, important on the free tier).
RATE_LIMIT_SIGNS = ["429", "quota", "rate limit", "resource_exhausted",
                    "exhausted", "overloaded", "unavailable", "try again",
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


def _cli_engine(prompt: str, model: str, system: str | None, timeout: int) -> str:
    """Invoke the gemini CLI non-interactively. Raise RateLimited / LLMError."""
    gemini = os.environ.get("GEMINI_BIN", "gemini")
    full = f"{system}\n\n{prompt}" if system else prompt
    # --skip-trust: required for headless/automated use (no interactive folder-trust prompt).
    cmd = [gemini, "--skip-trust", "-m", model, "-p", full, "--yolo"]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError as e:
        raise LLMError(f"gemini CLI not found ({gemini}); is it installed + logged in?") from e
    except subprocess.TimeoutExpired as e:
        raise RateLimited(f"gemini CLI timed out after {timeout}s") from e

    out, err = (proc.stdout or "").strip(), (proc.stderr or "").strip()
    if proc.returncode != 0:
        if _looks_rate_limited(err) or _looks_rate_limited(out):
            raise RateLimited(err or out)
        raise LLMError(f"gemini CLI exited {proc.returncode}: {err[:300]}")
    # Some quota aborts exit 0 with an empty/diagnostic body.
    if not out or _looks_rate_limited(out):
        raise RateLimited(out or "empty response (suspected quota abort)")
    return out


def _mock_engine(prompt: str, model: str, system: str | None, timeout: int) -> str:
    if MOCK_RESPONDER is None:
        raise LLMError("TECHNL_LLM_ENGINE=mock but llm.MOCK_RESPONDER is unset")
    return MOCK_RESPONDER(prompt, model)


def _engine():
    return _mock_engine if os.environ.get("TECHNL_LLM_ENGINE") == "mock" else _cli_engine


def llm_call(prompt: str, system: str | None = None, *, ladder: list[str] | None = None,
             max_attempts: int = 6, base_backoff: float = 8.0, timeout: int = 120,
             sleep=time.sleep) -> str:
    """Call the LLM with model rotation + exponential backoff. Returns text.

    Raises LLMError if every attempt fails.
    """
    ladder = ladder or model_ladder()
    engine = _engine()
    backoff = base_backoff
    last_err = None

    for attempt in range(max_attempts):
        model = ladder[attempt % len(ladder)]
        try:
            return engine(prompt, model, system, timeout)
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
