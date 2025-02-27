import os
import json
import logging
from typing import AsyncGenerator, Dict, List, Optional, Tuple

import httpx

from .settings import (
    API_KEY,
    API_BASE_URL,
    API_TIMEOUT,
    SYSTEM_PROMPT,
    MODEL_PROFILES,
    DEBUG_LOGS,
    PERPLEXITY_MODEL,
    PERPLEXITY_MODEL_ASK,
    PERPLEXITY_MODEL_CHAT,
)

# Setup logging
logger = logging.getLogger(__name__)

if DEBUG_LOGS:
    logger.setLevel(logging.INFO)
else:
    logger.setLevel(logging.CRITICAL)

logger.propagate = False

# Get API key and log whether it's set (without revealing the actual key)
if API_KEY:
    logger.info("API_KEY is set")
else:
    logger.warning("API_KEY is not set - API calls will fail with unauthorized errors")

TIMEOUT = API_TIMEOUT

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
    def __init__(self, api_key: Optional[str] = None, base_url: Optional[str] = None, 
                 settings: Optional[Dict[str, str]] = None, override_env_vars: bool = False):
        self.settings = settings or {}
        self.override_env_vars = override_env_vars
        
        # Determine which source to use for settings
        if override_env_vars:
            # When overriding env vars, use settings with fallback to env vars
            self.api_key = self.settings.get('api_key', API_KEY)
            self.base_url = self.settings.get('api_base_url', API_BASE_URL)
            self.system_prompt = self.settings.get('system_prompt', SYSTEM_PROMPT)
            self.model = self.settings.get('model', PERPLEXITY_MODEL)
            self.model_ask = self.settings.get('model_ask', self.settings.get('model', PERPLEXITY_MODEL_ASK))
            self.model_chat = self.settings.get('model_chat', self.settings.get('model', PERPLEXITY_MODEL_CHAT))
        else:
            # When not overriding, use env vars with fallback to settings
            self.api_key = api_key or API_KEY
            self.base_url = base_url or API_BASE_URL
            self.system_prompt = SYSTEM_PROMPT
            self.model = PERPLEXITY_MODEL
            self.model_ask = PERPLEXITY_MODEL_ASK
            self.model_chat = PERPLEXITY_MODEL_CHAT
            
        # These settings are always applied regardless of override flag
        self.timeout = float(self.settings.get('timeout', TIMEOUT))
        self.temperature = float(self.settings.get('temperature', 0.7))
        self.top_p = float(self.settings.get('top_p', 0.9))
            
        if not self.api_key:
            raise ValueError("Perplexity API key is required")

    async def _stream_completion(
        self,
        messages: List[Dict[str, str]],
        model: Optional[str] = None,
        temperature: Optional[float] = None,
        top_p: Optional[float] = None,
        system_prompt: Optional[str] = None,
    ) -> AsyncGenerator[Tuple[str, List[str], Dict[str, int]], None]:
        """
        Stream completion from Perplexity API.

        Args:
            messages: List of message dictionaries with 'role' and 'content'
            model: Optional model override
            temperature: Optional temperature override
            top_p: Optional top_p override
            system_prompt: Optional system prompt override

        Yields:
            Tuple of (content_chunk, citations, usage_stats)
        """
        model = model or self.model
        
        # If system_prompt is provided and the first message is a system message,
        # update it with the provided system prompt
        if system_prompt and messages and messages[0].get('role') == 'system':
            messages = [{"role": "system", "content": system_prompt}] + messages[1:]
        
        # Build request body with appropriate settings
        request_body = {
            "model": model,
            "messages": messages,
            "stream": True,
        }
        
        # Apply temperature and top_p settings
        # If override_env_vars is True, we always use our explicit settings
        # If override_env_vars is False, we use model profiles if available
        profile = MODEL_PROFILES.get(model, {})
        
        # Use provided temperature/top_p if given, otherwise use defaults
        if temperature is not None:
            request_body["temperature"] = temperature
        elif self.override_env_vars or model not in MODEL_PROFILES:
            # Use explicit settings
            request_body["temperature"] = self.temperature
        else:
            # Use model profile settings
            request_body["temperature"] = profile.get("temperature", self.temperature)
            
        if top_p is not None:
            request_body["top_p"] = top_p
        elif self.override_env_vars or model not in MODEL_PROFILES:
            # Use explicit settings
            request_body["top_p"] = self.top_p
        else:
            # Use model profile settings
            request_body["top_p"] = profile.get("top_p", self.top_p)

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
                timeout=self.timeout,
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
        profile_settings: Optional[Dict[str, any]] = None,
    ) -> AsyncGenerator[Tuple[str, List[str], Dict[str, int]], None]:
        """
        Send a one-off question to Perplexity.

        Args:
            query: The question to ask
            profile_settings: Optional dictionary with profile settings (model, temperature, top_p, system_prompt)

        Yields:
            Tuple of (content_chunk, citations, usage_stats)
        """
        # Use system prompt from profile if provided, otherwise use default
        system_prompt = profile_settings.get('system_prompt', self.system_prompt) if profile_settings else self.system_prompt
        
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": query}
        ]

        # Extract settings from profile if provided
        model = profile_settings.get('model', self.model_ask) if profile_settings else self.model_ask
        temperature = profile_settings.get('temperature') if profile_settings else None
        top_p = profile_settings.get('top_p') if profile_settings else None

        async for content, citations, usage in self._stream_completion(
            messages,
            model=model,
            temperature=temperature,
            top_p=top_p,
            system_prompt=system_prompt,
        ):
            yield content, citations, usage

    async def chat(
        self,
        messages: List[Dict[str, str]],
        profile_settings: Optional[Dict[str, any]] = None,
    ) -> AsyncGenerator[Tuple[str, List[str], Dict[str, int]], None]:
        """
        Continue a chat conversation with Perplexity.

        Args:
            messages: List of previous messages with 'role' and 'content'
            profile_settings: Optional dictionary with profile settings (model, temperature, top_p, system_prompt)

        Yields:
            Tuple of (content_chunk, citations, usage_stats)
        """
        # Use system prompt from profile if provided, otherwise use default
        system_prompt = profile_settings.get('system_prompt', self.system_prompt) if profile_settings else self.system_prompt
        
        # Check if first message is already a system message
        if messages and messages[0].get('role') == 'system':
            # Replace existing system message with our system prompt
            full_messages = [{"role": "system", "content": system_prompt}] + messages[1:]
        else:
            # Add system message at the beginning
            full_messages = [{"role": "system", "content": system_prompt}] + messages

        # Extract settings from profile if provided
        model = profile_settings.get('model', self.model_chat) if profile_settings else self.model_chat
        temperature = profile_settings.get('temperature') if profile_settings else None
        top_p = profile_settings.get('top_p') if profile_settings else None

        async for content, citations, usage in self._stream_completion(
            full_messages,
            model=model,
            temperature=temperature,
            top_p=top_p,
            system_prompt=system_prompt,
        ):
            yield content, citations, usage
