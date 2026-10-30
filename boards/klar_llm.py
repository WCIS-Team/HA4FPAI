#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""klar_llm.py — reserved LLM hook for the klar voice stack.

STATUS: stub. `llm.enabled` is false in klar-voice.json, so nothing here runs.

Placement in the pipeline (this is the whole point of reserving it):

    mic -> VAD/ASR -> klar engine (device intent) -> HA executes
                              |
                              +--> if NO house command found:
                                     LLM answers the chit-chat, then TTS

Rules baked in:
  * the LLM NEVER decides device actions. Klar owns intents; the model only
    talks, or rewrites an already-decided reply into nicer speech.
  * it is OpenAI-compatible (`/v1/chat/completions`) so any local server
    (llama.cpp, vLLM, Ollama, LM Studio) or hosted API can be dropped in by
    filling base_url/api_key/model - no code change.
  * it must not run on this board for anything bigger than a toy model:
    993 MB RAM total, and piper TTS already needs 384 MB of headroom.
    Point `base_url` at another host.

Wire-up when a model becomes available:
    cfg = load_config()
    cfg["llm"].update(enabled=True, base_url="http://10.0.0.5:8080/v1",
                      api_key="...", model="qwen2.5-0.5b-instruct")
and call `LLMClient(cfg).chat(messages)` from the intent fallback branch.
"""
import json
import logging
import urllib.error
import urllib.request

from voice_kit import load_config

_LOGGER = logging.getLogger("klar_llm")


class LLMDisabled(Exception):
    """Raised when the LLM slot is not configured."""


class LLMClient:
    """Minimal OpenAI-compatible chat client for the reserved slot."""

    def __init__(self, cfg=None):
        self.cfg = (cfg or load_config())["llm"]

    @property
    def enabled(self):
        return bool(self.cfg.get("enabled")) and bool(self.cfg.get("base_url"))

    def chat(self, messages, timeout=30, max_tokens=256):
        """messages: [{'role': 'user'|'assistant'|'system', 'content': str}]."""
        if not self.enabled:
            raise LLMDisabled("llm slot disabled: set llm.enabled/base_url/model")
        url = self.cfg["base_url"].rstrip("/") + "/chat/completions"
        body = {
            "model": self.cfg.get("model") or "default",
            "messages": messages,
            "max_tokens": max_tokens,
        }
        headers = {"Content-Type": "application/json"}
        key = self.cfg.get("api_key")
        if key:
            headers["Authorization"] = "Bearer " + key
        req = urllib.request.Request(url, data=json.dumps(body).encode(),
                                     headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                data = json.load(r)
            return data["choices"][0]["message"]["content"]
        except urllib.error.URLError as e:
            _LOGGER.warning("llm request failed: %s", e)
            raise


def rewrite_reply(text, personality=None):
    """Hook called after klar returns speech, before TTS.

    Returns `text` unchanged while the slot is disabled. When enabled, ask the
    model to only rephrase - never to add actions.
    """
    try:
        c = LLMClient()
        if not c.enabled:
            return text
        sys_prompt = ("Rewrite the assistant reply so it sounds natural when spoken. "
                      "Do NOT add, remove or change any action, device name, or number. "
                      "Reply with the sentence only.")
        if personality:
            sys_prompt += " Personality: %s." % personality
        return c.chat([{"role": "system", "content": sys_prompt},
                       {"role": "user", "content": text}])
    except Exception as e:                     # noqa: BLE001 - never break TTS
        _LOGGER.info("llm rewrite skipped: %s", e)
        return text


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    cfg = load_config()["llm"]
    print("llm.enabled =", cfg.get("enabled"))
    print("base_url   =", cfg.get("base_url") or "(unset)")
    print("model      =", cfg.get("model") or "(unset)")
    print("scope      =", cfg.get("scope_hint"))
    if not cfg.get("enabled"):
        print("\nRESERVED, not active. This is expected right now.")
