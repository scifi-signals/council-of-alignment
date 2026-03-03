"""Configuration — models, API keys, routing."""

import os
from dotenv import load_dotenv

load_dotenv()

OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY", "")
XAI_API_KEY = os.getenv("XAI_API_KEY", "")
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN", "")

# GitHub OAuth (user auth — separate from GITHUB_TOKEN which is for repo access)
GITHUB_CLIENT_ID = os.getenv("GITHUB_CLIENT_ID", "")
GITHUB_CLIENT_SECRET = os.getenv("GITHUB_CLIENT_SECRET", "")
SESSION_SECRET = os.getenv("SESSION_SECRET", "")
if not SESSION_SECRET:
    import secrets as _secrets
    SESSION_SECRET = _secrets.token_urlsafe(64)
    import warnings
    warnings.warn("SESSION_SECRET not set — using random value (sessions won't survive restart)")
BASE_URL = os.getenv("BASE_URL", "http://localhost:8890")

# BYOK encryption key for storing user API keys at rest (Fernet symmetric encryption)
ENCRYPTION_KEY = os.getenv("ENCRYPTION_KEY", "")
if not ENCRYPTION_KEY:
    raise RuntimeError("ENCRYPTION_KEY not set. Generate one with: python -c \"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\"")

# Free tier: how many convenes a user gets before needing their own API key
FREE_CONVENE_LIMIT = 3

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"

MODELS = {
    "claude": {
        "id": "anthropic/claude-3.7-sonnet",
        "name": "Claude",
        "color": "#7C5CFF",
        "provider": "anthropic",
    },
    "chatgpt": {
        "id": "openai/gpt-4o",
        "name": "ChatGPT",
        "color": "#1FD08C",
        "provider": "openai",
    },
    "gemini": {
        "id": "google/gemini-2.5-pro",
        "name": "Gemini",
        "color": "#4DA3FF",
        "provider": "google",
    },
    "grok": {
        "id": "x-ai/grok-3",
        "name": "Grok",
        "color": "#FF9B42",
        "provider": "xai",
    },
}

# Direct API model IDs (used when routing without OpenRouter)
DIRECT_MODEL_IDS = {
    "claude": "claude-sonnet-4-20250514",
    "chatgpt": "gpt-5",
    "gemini": "gemini-2.5-pro",
    "grok": "grok-3",
}

# Direct API base URLs
DIRECT_BASE_URLS = {
    "openai": "https://api.openai.com/v1",
    "xai": "https://api.x.ai/v1",
    "google": "https://generativelanguage.googleapis.com/v1beta/openai",
}

DB_PATH = os.path.join(os.path.dirname(__file__), "data", "council.db")


def get_council_models(lead: str) -> list[str]:
    """Return the 3 model keys that aren't the lead."""
    return [k for k in MODELS if k != lead]


def get_api_key_for_provider(provider: str) -> str:
    """Return the direct API key for a provider."""
    return {
        "anthropic": ANTHROPIC_API_KEY,
        "openai": OPENAI_API_KEY,
        "google": GOOGLE_API_KEY,
        "xai": XAI_API_KEY,
    }.get(provider, "")


def use_openrouter() -> bool:
    """Whether to route through OpenRouter."""
    return bool(OPENROUTER_API_KEY)
