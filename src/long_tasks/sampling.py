"""A bounded OpenAI-compatible sampler, including swarm-of-experts endpoints."""

from __future__ import annotations

import os
from dataclasses import asdict, dataclass
from urllib.parse import urlsplit

import httpx

from .maker import Sample, strict_json


@dataclass(frozen=True)
class SamplingConfig:
    base_url: str = "http://127.0.0.1:8000/v1"
    model: str = "local-single"
    api_key_env: str = "MAKER_API_KEY"
    temperature: float = 0.1
    seed: int = 11
    max_tokens: int = 256
    timeout_seconds: float = 120
    max_response_bytes: int = 65536

    def __post_init__(self):
        url = urlsplit(self.base_url)
        if not url.hostname or url.username or url.password or url.query or url.fragment:
            raise ValueError("Use a base URL without credentials, query or fragment")
        if url.scheme != "https" and not (
            url.scheme == "http" and url.hostname in {"localhost", "127.0.0.1", "::1"}
        ):
            raise ValueError("HTTPS required except on loopback")
        _ = url.port
        if not self.model or len(self.model) > 256:
            raise ValueError("Invalid model")
        if not 0 <= self.temperature <= 2 or not 0 < self.timeout_seconds <= 3600:
            raise ValueError("Invalid sampling temperature or timeout")
        for name, low, high in [
            ("seed", 0, 2**31 - 1),
            ("max_tokens", 1, 32768),
            ("max_response_bytes", 256, 1048576),
        ]:
            if type(getattr(self, name)) is not int or not low <= getattr(self, name) <= high:
                raise ValueError(f"Invalid {name}")


class HTTPSampler:
    def __init__(
        self, config: SamplingConfig, *, transport: httpx.AsyncBaseTransport | None = None
    ):
        self.config = config
        key = os.environ.get(config.api_key_env)
        if not key and urlsplit(config.base_url).hostname not in {"localhost", "127.0.0.1", "::1"}:
            raise ValueError(f"Set {config.api_key_env} for the remote endpoint")
        self.client = httpx.AsyncClient(
            timeout=config.timeout_seconds,
            trust_env=False,
            follow_redirects=False,
            transport=transport,
            headers={"Authorization": f"Bearer {key or 'local'}"},
        )

    @property
    def identity(self):
        return {"implementation": "openai-compatible-v1", **asdict(self.config)}

    async def sample(self, prompt: str, index: int) -> Sample:
        config = self.config
        payload = {
            "model": config.model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": config.temperature,
            "seed": (config.seed + index) % (2**31),
            "max_tokens": config.max_tokens,
            "stream": False,
        }
        async with self.client.stream(
            "POST", config.base_url.rstrip("/") + "/chat/completions", json=payload
        ) as response:
            if response.status_code != 200:
                raise RuntimeError(f"Model endpoint returned HTTP {response.status_code}")
            data = bytearray()
            async for chunk in response.aiter_bytes():
                data.extend(chunk)
                if len(data) > config.max_response_bytes:
                    raise ValueError("Model response exceeds the byte limit")
        value = strict_json(data.decode("utf-8"))
        try:
            choice = value["choices"][0]
            text = choice["message"]["content"]
            finish = choice["finish_reason"]
            if not isinstance(text, str) or not isinstance(finish, str):
                raise ValueError("Expected a text completion")
            usage = value.get("usage")
            if usage is not None and (
                not isinstance(usage, dict)
                or any(
                    type(v) is not int or v < 0
                    for k, v in usage.items()
                    if k in {"prompt_tokens", "completion_tokens", "total_tokens"}
                )
            ):
                raise ValueError("Invalid usage")
            return Sample(text, finish, usage)
        except (KeyError, IndexError, TypeError) as exc:
            raise ValueError("Malformed model response") from exc

    async def close(self):
        await self.client.aclose()
