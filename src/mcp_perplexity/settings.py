import os
from typing import Dict, Any
from pathlib import Path

# Debug settings
DEBUG_LOGS = os.getenv('DEBUG_LOGS', 'false').lower() == 'true'

# Web UI settings
WEB_UI_ENABLED = os.getenv('WEB_UI_ENABLED', 'false').lower() == 'true'
WEB_UI_PORT = int(os.getenv('WEB_UI_PORT', '8050'))
WEB_UI_HOST = os.getenv('WEB_UI_HOST', '127.0.0.1')

# Database settings
def get_default_db_path() -> str:
    """Get default database path in user's home directory."""
    data_dir = Path.home() / ".mcp-perplexity" / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    return str(data_dir / "chats.db")

DB_PATH = os.getenv('DB_PATH', get_default_db_path())

# Default system prompt
DEFAULT_SYSTEM_PROMPT = """You are an expert assistant providing accurate answers to technical questions.
Your responses must:
1. Be based on the most relevant web sources
2. Include source citations for all factual claims
3. If no relevant results are found, suggest 2-3 alternative search queries that might better uncover the needed information
4. Prioritize technical accuracy, especially for programming-related questions"""

# System prompt (can be overridden by env var)
SYSTEM_PROMPT = os.getenv('SYSTEM_PROMPT', DEFAULT_SYSTEM_PROMPT)

# Model profiles with their default settings
MODEL_PROFILES: Dict[str, Dict[str, float]] = {
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
    "sonar-deep-research": {
        "temperature": 0.6,
        "top_p": 0.95,
    },
    "r1-1776": {
        "temperature": 0.6,
        "top_p": 0.95,
    },
}

# Default model to use
DEFAULT_MODEL = "sonar-pro"

# Model settings
PERPLEXITY_MODEL = os.getenv("PERPLEXITY_MODEL", DEFAULT_MODEL)
PERPLEXITY_MODEL_ASK = os.getenv("PERPLEXITY_MODEL_ASK", PERPLEXITY_MODEL)
PERPLEXITY_MODEL_CHAT = os.getenv("PERPLEXITY_MODEL_CHAT", PERPLEXITY_MODEL)

# API defaults
DEFAULT_API_BASE_URL = "https://api.perplexity.ai"
DEFAULT_TIMEOUT = 120.0

# API settings
API_KEY = os.getenv('PERPLEXITY_API_KEY')
API_BASE_URL = os.getenv('PERPLEXITY_API_BASE_URL', DEFAULT_API_BASE_URL)
API_TIMEOUT = float(os.getenv('TIMEOUT', DEFAULT_TIMEOUT))

# Environment variable mappings
ENV_MAPPINGS = {
    'PERPLEXITY_API_KEY': 'api_key',
    'PERPLEXITY_MODEL': 'model',
    'PERPLEXITY_MODEL_ASK': 'model_ask',
    'PERPLEXITY_MODEL_CHAT': 'model_chat',
    'PERPLEXITY_API_BASE_URL': 'api_base_url',
    'TIMEOUT': 'timeout',
    'SYSTEM_PROMPT': 'system_prompt',
}

# Default settings (used when no env vars are present)
DEFAULT_SETTINGS = {
    'api_base_url': DEFAULT_API_BASE_URL,
    'timeout': str(DEFAULT_TIMEOUT),
    'system_prompt': DEFAULT_SYSTEM_PROMPT,
    'model': DEFAULT_MODEL,
    'model_ask': os.getenv('PERPLEXITY_MODEL_ASK', DEFAULT_MODEL),
    'model_chat': os.getenv('PERPLEXITY_MODEL_CHAT', DEFAULT_MODEL),
    'override_env_vars': 'false',
}

# Add temperature and top_p from model profile
_profile = MODEL_PROFILES.get(DEFAULT_MODEL, {})
DEFAULT_SETTINGS.update({
    'temperature': str(_profile.get('temperature', 0.2)),
    'top_p': str(_profile.get('top_p', 0.9)),
})

def get_env_value(key: str) -> str:
    """Get environment variable value with appropriate default."""
    setting_key = ENV_MAPPINGS.get(key)
    if setting_key:
        return os.getenv(key, DEFAULT_SETTINGS.get(setting_key, ''))
    return ''

def get_settings_with_env_defaults() -> Dict[str, Any]:
    """Get all settings with environment variables as defaults."""
    settings = DEFAULT_SETTINGS.copy()
    
    # Override with environment variables if they exist
    for env_var, setting_key in ENV_MAPPINGS.items():
        env_value = os.getenv(env_var)
        if env_value is not None:
            settings[setting_key] = env_value
            
    return settings 