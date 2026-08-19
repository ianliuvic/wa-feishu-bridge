"""OpenAI-compatible DeepSeek chat client.

Capability only: not wired into the webhook forwarding flow yet. Use it later
for message summarization or WhatsApp auto-reply.
"""

import logging

import httpx

logger = logging.getLogger("bridge.llm")


class DeepSeekClient:
    def __init__(
        self,
        api_key: str,
        base_url: str = "https://api.deepseek.com",
        model: str = "deepseek-v4-flash",
    ):
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model = model

    def chat(
        self,
        user_text: str,
        system: str | None = None,
        temperature: float = 0.7,
        max_tokens: int = 1024,
    ) -> str:
        url = f"{self.base_url}/chat/completions"
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": user_text})
        body = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        resp = httpx.post(url, json=body, headers=headers, timeout=60)
        try:
            data = resp.json()
        except ValueError:
            resp.raise_for_status()
            raise
        if resp.status_code >= 400 or data.get("error"):
            raise RuntimeError(f"DeepSeek error: status={resp.status_code} body={data}")
        return data["choices"][0]["message"]["content"]
