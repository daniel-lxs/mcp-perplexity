import os
import json
from typing import AsyncGenerator, Dict, List, Optional, Tuple

import httpx

PERPLEXITY_API_KEY = os.getenv("PERPLEXITY_API_KEY")
PERPLEXITY_MODEL = os.getenv("PERPLEXITY_MODEL")
PERPLEXITY_MODEL_ASK = os.getenv("PERPLEXITY_MODEL_ASK")
PERPLEXITY_MODEL_CHAT = os.getenv("PERPLEXITY_MODEL_CHAT")
PERPLEXITY_API_BASE_URL = "https://api.perplexity.ai"

SYSTEM_PROMPT = """You are an expert assistant providing accurate answers to technical questions.
Your responses must:
1. Be based on the most relevant web sources
2. Include source citations for all factual claims
3. If no relevant results are found, suggest 2-3 alternative search queries that might better uncover the needed information
4. Prioritize technical accuracy, especially for programming-related questions"""


class PerplexityClient:
    def __init__(self, api_key: Optional[str] = None, base_url: Optional[str] = None):
        self.api_key = api_key or PERPLEXITY_API_KEY
        self.base_url = base_url or PERPLEXITY_API_BASE_URL
        if not self.api_key:
            raise ValueError("Perplexity API key is required")

    async def _stream_completion(
        self,
        messages: List[Dict[str, str]],
        model: Optional[str] = None,
    ) -> AsyncGenerator[Tuple[str, List[str], Dict[str, int]], None]:
        """
        Stream completion from Perplexity API.

        Args:
            messages: List of message dictionaries with 'role' and 'content'
            model: Optional model override

        Yields:
            Tuple of (content_chunk, citations, usage_stats)
        """
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{self.base_url}/chat/completions",
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": model or PERPLEXITY_MODEL,
                    "messages": messages,
                    "stream": True
                },
                timeout=30.0,
            )
            response.raise_for_status()

            citations = []
            usage = {}

            async for chunk in response.aiter_text():
                for line in chunk.split('\n'):
                    line = line.strip()
                    if line.startswith("data: "):
                        try:
                            data = json.loads(line[6:])
                            if "usage" in data:
                                usage.update(data["usage"])
                            if "citations" in data:
                                citations.extend(data["citations"])
                            if data.get("choices"):
                                content = data["choices"][0].get(
                                    "delta", {}).get("content", "")
                                if content:
                                    yield content, list(dict.fromkeys(citations)), usage
                        except json.JSONDecodeError:
                            continue

    async def ask(
        self,
        query: str,
    ) -> AsyncGenerator[Tuple[str, List[str], Dict[str, int]], None]:
        """
        Send a one-off question to Perplexity.

        Args:
            query: The question to ask

        Yields:
            Tuple of (content_chunk, citations, usage_stats)
        """
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": query}
        ]

        async for content, citations, usage in self._stream_completion(
            messages,
            model=PERPLEXITY_MODEL_ASK or PERPLEXITY_MODEL,
        ):
            yield content, citations, usage

    async def chat(
        self,
        messages: List[Dict[str, str]],
    ) -> AsyncGenerator[Tuple[str, List[str], Dict[str, int]], None]:
        """
        Continue a chat conversation with Perplexity.

        Args:
            messages: List of previous messages with 'role' and 'content'

        Yields:
            Tuple of (content_chunk, citations, usage_stats)
        """
        system_message = {"role": "system", "content": SYSTEM_PROMPT}
        full_messages = [system_message] + messages

        async for content, citations, usage in self._stream_completion(
            full_messages,
            model=PERPLEXITY_MODEL_CHAT or PERPLEXITY_MODEL,
        ):
            yield content, citations, usage
