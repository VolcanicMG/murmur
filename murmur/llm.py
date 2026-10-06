"""LLM transport. Only provider-specific request shaping lives here; returns raw text."""
import json, os, urllib.request


def _post(url, body, headers, timeout):
    req = urllib.request.Request(url, json.dumps(body).encode(), {"Content-Type": "application/json", **headers})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def chat(cfg, system, user):
    """One system+user turn. cfg["llm"]["provider"]: "openai" (LM Studio/Ollama/...) or "anthropic"."""
    llm = cfg["llm"]
    timeout = llm.get("timeout", 15)
    msgs = [{"role": "user", "content": user}]
    if llm.get("provider", "openai") == "anthropic":
        body = {"model": llm["model"], "max_tokens": llm.get("max_tokens", 2048), "temperature": 0.2,
                "system": system, "messages": msgs}
        key = os.environ.get(llm.get("api_key_env", "ANTHROPIC_API_KEY"), "")
        d = _post(llm["url"], body, {"x-api-key": key, "anthropic-version": "2023-06-01"}, timeout)
        return "".join(b.get("text", "") for b in d["content"])
    body = {"model": llm["model"], "temperature": 0.2, "reasoning_effort": "none",
            "messages": [{"role": "system", "content": system}] + msgs}
    return _post(llm["url"], body, {}, timeout)["choices"][0]["message"]["content"]
