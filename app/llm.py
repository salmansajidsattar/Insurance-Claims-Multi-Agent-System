"""Talking to the local models (Ollama, OpenAI-compatible API).

ask_json(system, user, image)  -> dict     chat model, reply must be JSON
embed(texts)                   -> vectors  embedding model (for RAG search)

Tests replace both with fakes via use_fake(), so no Ollama is needed to run them.
"""
import base64
import json
from pathlib import Path

from app import config

_fake_ask = None
_fake_embed = None


def use_fake(ask=None, embed=None) -> None:
    global _fake_ask, _fake_embed
    _fake_ask, _fake_embed = ask, embed


def _client():
    from openai import OpenAI
    return OpenAI(base_url=config.LLM_BASE_URL, api_key="ollama", timeout=config.TIMEOUT_S)


def ask_json(system: str, user: str, image_path: str | None = None, model: str | None = None) -> dict:
    if _fake_ask:
        return _fake_ask(system, user, image_path)

    content = [{"type": "text", "text": user}]
    if image_path:
        b64 = base64.b64encode(Path(image_path).read_bytes()).decode()
        content.append({"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}})

    reply = _client().chat.completions.create(
        model=model or config.TEXT_MODEL,
        messages=[{"role": "system", "content": system}, {"role": "user", "content": content}],
        response_format={"type": "json_object"},
        temperature=0,
    )
    return json.loads(reply.choices[0].message.content)


def embed(texts: list[str]) -> list[list[float]]:
    if _fake_embed:
        return _fake_embed(texts)
    reply = _client().embeddings.create(model=config.EMBED_MODEL, input=texts)
    return [item.embedding for item in reply.data]
