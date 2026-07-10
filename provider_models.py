"""
Single source of truth for provider names/labels and their selectable model
lists, shared by providers.py (fallback chain) and config_dialog.py (UI).
"""

PROVIDERS = ["ollama", "gemini", "nvidia", "cerebras", "openai", "xai", "custom"]

PROVIDER_LABELS = {
    "ollama": "Ollama",
    "gemini": "Gemini",
    "nvidia": "NVIDIA",
    "cerebras": "Cerebras",
    "openai": "OpenAI",
    "xai": "xAI (Grok)",
    "custom": "Custom (OpenAI-compatible)",
}

MODEL_OPTIONS = {
    "ollama": ["gemma4", "gpt-oss:20b", "gpt-oss:120b"],
    "gemini": ["gemini-3.5-flash", "gemini-3.1-flash-lite"],
    "nvidia": [
        "deepseek-ai/deepseek-v4-flash",
        "google/gemma-4-31b-it",
        "openai/gpt-oss-120b",
    ],
    "cerebras": ["gpt-oss-120b", "gemma-4-31b"],
    "openai": ["gpt-5.1", "gpt-5-mini"],
    "xai": ["grok-4", "grok-3-mini"],
    "custom": [],
}
