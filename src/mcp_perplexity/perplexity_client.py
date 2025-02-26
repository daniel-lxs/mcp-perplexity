import os
import json
import logging
from typing import AsyncGenerator, Dict, List, Optional, Tuple

import httpx

# Setup logging
logger = logging.getLogger(__name__)

# Get API key and log whether it's set (without revealing the actual key)
PERPLEXITY_API_KEY = os.getenv("PERPLEXITY_API_KEY")
if PERPLEXITY_API_KEY:
    logger.info("PERPLEXITY_API_KEY is set")
else:
    logger.warning("PERPLEXITY_API_KEY is not set - API calls will fail with unauthorized errors")

PERPLEXITY_MODEL = os.getenv("PERPLEXITY_MODEL") or "sonar-pro"
PERPLEXITY_MODEL_ASK = os.getenv("PERPLEXITY_MODEL_ASK") or PERPLEXITY_MODEL
PERPLEXITY_MODEL_CHAT = os.getenv("PERPLEXITY_MODEL_CHAT") or PERPLEXITY_MODEL
PERPLEXITY_API_BASE_URL = "https://api.perplexity.ai"

SYSTEM_PROMPT = """You are an expert assistant providing accurate answers to technical questions.
Your responses must:
1. Be based on the most relevant web sources
2. Include source citations for all factual claims
3. If no relevant results are found, suggest 2-3 alternative search queries that might better uncover the needed information
4. Prioritize technical accuracy, especially for programming-related questions"""

TIMEOUT = 120.0

# Define model profiles with validation ranges
MODEL_PROFILES = {
    "sonar": {
        "temperature": 0.2,
        "top_p": 0.9,
    },
    "sonar-pro": {
        "temperature": 0.2,
        "top_p": 0.9,
    },
    "sonar-reasoning": {
        "temperature": 0.6,
        "top_p": 0.95,
    },
    "sonar-reasoning-pro": {
        "temperature": 0.6,
        "top_p": 0.95,
    },
}


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
        model = model or PERPLEXITY_MODEL
        profile = MODEL_PROFILES.get(model, {})
        request_body = {
            "model": model,
            "messages": messages,
            "stream": True,
        }

        if profile:
            # Apply validated profile settings
            for setting in ["temperature", "top_p"]:
                if setting in profile:
                    request_body[setting] = profile[setting]

        async with httpx.AsyncClient() as client:
            # Use streaming request with proper headers for SSE
            async with client.stream(
                "POST",
                f"{self.base_url}/chat/completions",
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                    "Accept": "text/event-stream",  # Explicitly request SSE format
                },
                json=request_body,
                timeout=TIMEOUT,
            ) as response:
                response.raise_for_status()
                
                citations = []
                usage = {}
                
                # Process the SSE stream line by line
                async for line in response.aiter_lines():
                    line = line.strip()
                    if not line or line == "data: [DONE]":
                        continue
                        
                    if line.startswith("data: "):
                        try:
                            data = json.loads(line[6:])
                            
                            # Extract usage information
                            if "usage" in data:
                                usage.update(data["usage"])
                                
                            # Extract citations
                            if "citations" in data:
                                citations = data["citations"]
                                
                            # Extract content
                            if data.get("choices"):
                                content = data["choices"][0].get("delta", {}).get("content", "")
                                # Even if content is empty, we should yield to maintain the stream
                                # This ensures progress reporting works correctly
                                if content is not None:
                                    yield content, citations.copy(), usage
                        except json.JSONDecodeError:
                            logger.warning(f"Failed to parse JSON from line: {line}")
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
            model=PERPLEXITY_MODEL_ASK,
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
            model=PERPLEXITY_MODEL_CHAT,
        ):
            yield content, citations, usage
