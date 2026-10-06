"""LLM transport. Only provider-specific request shaping lives here; returns raw text."""
import json, logging, os, threading, time, urllib.error, urllib.request

from . import store

log = logging.getLogger("murmur")
BREAKER_S = 60  # skip a failed primary this long when a fallback is enabled
_down_until = 0.0  # monotonic time until which the primary is skipped
_local = threading.local()  # route of the last chat() on this thread


def route():
    """"primary" | "fallback": which provider answered the last chat() call made on THIS thread."""
    return getattr(_local, "route", "primary")


class Starting(OSError):
    """The bundled model is still starting: serve this request from the fallback, without tripping the breaker."""


def _post(url, body, headers, timeout):
    req = urllib.request.Request(url, json.dumps(body).encode(), {"Content-Type": "application/json", **headers})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def _transient(e):
    """Worth falling back on: connection refused, timeouts, DNS, HTTP 5xx. A 4xx is a config error and is raised as-is."""
    return e.code >= 500 if isinstance(e, urllib.error.HTTPError) else isinstance(e, OSError)  # URLError and timeouts are OSErrors


def _call(llm, system, user, cfg=None):
    """One system+user turn against one provider table (llm["provider"]: "openai" (LM Studio/Ollama/...), "anthropic",
    or "builtin": the managed llama-server of localllm, started lazily; needs the full cfg for [builtin])."""
    timeout = llm.get("timeout", 15)
    msgs = [{"role": "user", "content": user}]
    if llm.get("provider", "openai") == "anthropic":
        body = {"model": llm["model"], "max_tokens": llm.get("max_tokens", 2048), "temperature": 0.2,
                "system": system, "messages": msgs}
        key = os.environ.get(llm.get("api_key_env", "ANTHROPIC_API_KEY"), "")
        d = _post(llm["url"], body, {"x-api-key": key, "anthropic-version": "2023-06-01"}, timeout)
        return "".join(b.get("text", "") for b in d["content"])
    url = llm.get("url")
    if llm.get("provider") == "builtin":
        from . import localllm
        if not localllm.enabled(cfg or {}):
            raise OSError("bundled model disabled ([builtin] enabled = false)")  # transient: the fallback serves it
        if store.runtime.get("llm_starting") and threading.current_thread().name != localllm.WARMUP_THREAD:
            raise Starting("bundled model starting")  # warm-up in progress: serve this one from the fallback, don't queue behind it
        try:
            url = localllm.ready_url(cfg or {}) + "/chat/completions"  # never blocks on a start or a restart
        except RuntimeError as e:
            if "starting" in str(e):
                raise Starting(str(e)) from e  # no model / crash / not healthy: treat as a transient outage so the fallback + breaker apply
            raise OSError(str(e)) from e
    body = {"model": llm.get("model", "builtin"), "temperature": 0.2, "reasoning_effort": "none",
            "messages": [{"role": "system", "content": system}] + msgs}
    return _post(url, body, {}, timeout)["choices"][0]["message"]["content"]


def chat(cfg, system, user):
    """One system+user turn on [llm]; when [llm] fallback is true, a transient failure retries on [llm_fallback].
    After a primary failure the primary is skipped for BREAKER_S seconds. Raises only when every enabled provider fails."""
    global _down_until
    fb = cfg.get("llm_fallback") if cfg["llm"].get("fallback") else None
    if not (fb and time.monotonic() < _down_until):
        try:
            out = _call(cfg["llm"], system, user, cfg)
            _local.route = "primary"
            return out
        except Exception as e:
            if not _transient(e):
                raise
            if not isinstance(e, Starting):  # a start in progress is not an outage: no 60 s breaker
                _down_until = time.monotonic() + BREAKER_S
            if not fb:
                raise
            log.warning("primary LLM failed (%s); using the fallback for %ds", e, BREAKER_S)
    out = _call(fb, system, user, cfg)
    _local.route = "fallback"
    return out
