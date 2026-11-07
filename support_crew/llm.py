"""LLM configuration shared by every agent in the crew."""

import json
import os

import httpx
from crewai import LLM
from crewai.llms.hooks.base import BaseInterceptor

DEFAULT_MODEL = "anthropic/claude-opus-5-5"
FALLBACK_BETA = "server-side-fallback-2026-07-01"


class MessagesRequestOptions(BaseInterceptor[httpx.Request, httpx.Response]):
    """Adds request options CrewAI's Anthropic provider doesn't expose.

    * ``output_config.effort`` - Claude Opus 5.5 defaults to ``medium``; make it explicit.
    * ``fallbacks: "default"`` - server-side fallback model if a request is declined
      by a safety classifier, so a ticket isn't lost to a ``refusal`` stop reason.
    """

    def __init__(self, effort: str | None, fallbacks: bool):
        self.effort = effort
        self.fallbacks = fallbacks

    def on_outbound(self, message: httpx.Request) -> httpx.Request:
        if message.method != "POST" or not message.url.path.endswith("/v1/messages"):
            return message
        body = json.loads(message.read() or b"{}")
        if self.effort:
            body.setdefault("output_config", {})["effort"] = self.effort
        headers = {k: v for k, v in message.headers.items() if k.lower() != "content-length"}
        if self.fallbacks:
            body["fallbacks"] = "default"
            betas = [b for b in headers.get("anthropic-beta", "").split(",") if b]
            headers["anthropic-beta"] = ",".join([*betas, FALLBACK_BETA])
        return httpx.Request(
            message.method, message.url, headers=headers, content=json.dumps(body).encode(),
            extensions=message.extensions,
        )

    def on_inbound(self, message: httpx.Response) -> httpx.Response:
        return message

    async def aon_outbound(self, message: httpx.Request) -> httpx.Request:
        return self.on_outbound(message)

    async def aon_inbound(self, message: httpx.Response) -> httpx.Response:
        return message


def build_llm() -> LLM:
    model = os.environ.get("SUPPORT_LLM_MODEL", DEFAULT_MODEL)
    kwargs = {"model": model}
    if model.startswith("groq/"):                     # Groq's OpenAI-compatible endpoint, so LiteLLM is not required
        kwargs = {"model": "openai/" + model[len("groq/"):], "base_url": "https://api.groq.com/openai/v1",
                  "api_key": os.environ["GROQ_API_KEY"]}
    if model.startswith(("anthropic/", "claude")):
        kwargs["interceptor"] = MessagesRequestOptions(
            effort=os.environ.get("SUPPORT_LLM_EFFORT", "medium") or None,
            fallbacks=os.environ.get("SUPPORT_LLM_FALLBACKS", "1") == "1",
        )
    return LLM(**kwargs)
