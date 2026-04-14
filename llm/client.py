"""
LLM Client
==========
Multi-config Azure OpenAI client setup.
Exact mirror of asset-assist-production/src/modules/llm_client.ts.

Supported configs (same names, endpoints, and api_versions as production):
  - client_gpt4o_clae   : gpt-4o   @ corellm-openai.openai.azure.com
  - client_gpt4o_itam   : gpt-4o   @ ham-ai-agents-delivery.openai.azure.com
  - client_gpt41_itam   : gpt-4.1  @ ham-ai-agents-delivery.openai.azure.com  (default)
  - client_gpt52_azure  : gpt-5.2  @ arun-mlow8j4t-eastus2.openai.azure.com

Override active config via: LLM_CLIENT=client_gpt52_azure in .env
"""

from __future__ import annotations

import os
import yaml
from pathlib import Path
from openai import AzureOpenAI
from dotenv import load_dotenv

load_dotenv()

# ─── Config Definitions ───────────────────────────────────────────────────────

LLM_CONFIGS: list[dict] = [
    {
        "name": "client_gpt4o_clae",
        "azure_endpoint": "https://corellm-openai.openai.azure.com/",
        "api_key_env": "AZURE_OPENAI_API_KEY_CLAE",
        "api_version": "2024-02-15-preview",
        "model_name": "gpt-4o",
        "is_gpt52": False,
    },
    {
        "name": "client_gpt4o_itam",
        "azure_endpoint": "https://ham-ai-agents-delivery.openai.azure.com/",
        "api_key_env": "AZURE_OPENAI_API_KEY_ITAM",
        "api_version": "2024-12-01-preview",
        "model_name": "gpt-4o",
        "is_gpt52": False,
    },
    {
        "name": "client_gpt41_itam",
        "azure_endpoint": "https://ham-ai-agents-delivery.openai.azure.com/",
        "api_key_env": "AZURE_OPENAI_API_KEY_ITAM",
        "api_version": "2024-12-01-preview",
        "model_name": "gpt-4.1",
        "is_gpt52": False,
    },
    {
        "name": "client_gpt52_azure",
        "azure_endpoint": "https://arun-mlow8j4t-eastus2.openai.azure.com/",
        "api_key_env": "AZURE_OPENAI_API_KEY_GPT52",
        "api_version": "2025-03-01-preview",
        "model_name": "gpt-5.2-chat",
        "is_gpt52": True,
    },
]

DEFAULT_CLIENT_NAME = "client_gpt41_itam"

# ─── Internal State ───────────────────────────────────────────────────────────

_client_cache: dict[str, AzureOpenAI] = {}
_config: dict | None = None
_config_logged = False
_CONFIG_PATH = Path(__file__).parent.parent / "config.yaml"


# ─── Config Loader ────────────────────────────────────────────────────────────


def get_config() -> dict:
    """Load and cache config.yaml from project root."""
    global _config
    if _config is not None:
        return _config
    if not _CONFIG_PATH.exists():
        raise FileNotFoundError(f"config.yaml not found at {_CONFIG_PATH}")
    with open(_CONFIG_PATH, "r") as f:
        _config = yaml.safe_load(f)
    return _config


# ─── Active Config Resolution ─────────────────────────────────────────────────


def get_active_config_name() -> str:
    """Return the active LLM config name (env override → default)."""
    return os.getenv("LLM_CLIENT", DEFAULT_CLIENT_NAME)


def get_llm_config(config_name: str | None = None) -> dict:
    """Return the LLM config dict for the given (or active) config name."""
    name = config_name or get_active_config_name()
    cfg = next((c for c in LLM_CONFIGS if c["name"] == name), None)
    if cfg is None:
        available = ", ".join(c["name"] for c in LLM_CONFIGS)
        raise ValueError(f"Unknown LLM config: '{name}'. Available: {available}")
    return cfg


# ─── Client Factory ───────────────────────────────────────────────────────────


def get_client(config_name: str | None = None) -> AzureOpenAI:
    """
    Return a cached AzureOpenAI client for the given config (or active default).
    Reads the API key from the environment variable named in the config.
    """
    global _config_logged
    name = config_name or get_active_config_name()

    if name in _client_cache:
        return _client_cache[name]

    cfg = get_llm_config(name)
    api_key = os.getenv(cfg["api_key_env"])

    if not api_key:
        raise EnvironmentError(
            f"Missing env var '{cfg['api_key_env']}' required for config '{name}'.\n"
            f"Copy .env.example to .env and fill in your API keys."
        )

    client = AzureOpenAI(
        api_key=api_key,
        azure_endpoint=cfg["azure_endpoint"],
        api_version=cfg["api_version"],
    )
    _client_cache[name] = client

    if not _config_logged:
        _config_logged = True
        token_param = "max_completion_tokens" if cfg["is_gpt52"] else "max_tokens"
        temp_note = "default(1)" if cfg["is_gpt52"] else os.getenv("LLM_TEMPERATURE", "0.1")
        print(
            f"[LLM][CONFIG] client={name} model={cfg['model_name']} "
            f"endpoint={cfg['azure_endpoint']} api_version={cfg['api_version']} "
            f"{token_param}={os.getenv('LLM_MAX_TOKENS', '4096')} temperature={temp_note}"
        )

    return client


def get_model_name(config_name: str | None = None) -> str:
    """Return the model/deployment name for the given (or active) config."""
    return get_llm_config(config_name)["model_name"]


def is_gpt52(config_name: str | None = None) -> bool:
    """Return True if the active config is GPT-5.2 (which has special API constraints)."""
    return get_llm_config(config_name)["is_gpt52"]
