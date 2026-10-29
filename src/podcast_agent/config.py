"""Run settings.

Tunable behaviour lives in `config.yaml` (and CLI overrides), never in environment
variables. The environment is only used for credentials (ANTHROPIC_API_KEY).
In the hosted deployment these same fields move to an admin-editable settings
table; see design/DEPLOYMENT.md.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal, Optional

import yaml
from pydantic import BaseModel, Field


Provider = Literal["openrouter", "anthropic"]

# Credentials are read from the environment by provider; nothing else is.
API_KEY_ENV = {"openrouter": "OPENROUTER_API_KEY", "anthropic": "ANTHROPIC_API_KEY"}
DEFAULT_BASE_URL = {"openrouter": "https://openrouter.ai/api", "anthropic": None}


class ModelSettings(BaseModel):
    # Both providers speak the Anthropic Messages API, so one agent loop serves
    # any model OpenRouter routes to (GLM, Claude, GPT, Gemini, ...).
    provider: Provider = "openrouter"
    name: str = "z-ai/glm-5.3-flash"
    base_url: Optional[str] = None  # defaults per provider
    effort: Literal["low", "medium", "high", "xhigh", "max"] = "high"
    max_tokens: int = 16000
    show_thinking: bool = True  # log reasoning as agent thinking
    # Anthropic-only features; ignored for other providers.
    server_side_fallback: bool = False
    prompt_caching: bool = True
    # USD per million tokens, used only for the cost estimate in run metadata.
    input_price_per_mtok: float = 0.15
    output_price_per_mtok: float = 0.50
    cache_read_price_per_mtok: float = 0.03
    cache_write_price_per_mtok: float = 0.15
    web_search_price_per_1k: float = 12.0

    def resolved_base_url(self) -> Optional[str]:
        return self.base_url or DEFAULT_BASE_URL[self.provider]


class AgentSettings(BaseModel):
    max_turns: int = 30
    summary_min_words: int = 200
    summary_max_words: int = 300
    takeaways_count: int = 5
    min_quotes: int = 3
    max_quotes: int = 6
    min_topics: int = 4
    max_topics: int = 10
    min_claims: int = 5
    max_claims: int = 12


class FactCheckSettings(BaseModel):
    knowledge_base_path: str = "data/knowledge_base.json"
    kb_top_k: int = 3
    web_search: bool = True
    web_search_max_uses: int = 8
    # OpenRouter only: auto | native | exa | parallel | perplexity | firecrawl
    web_search_engine: str = "auto"
    web_search_max_results: int = 5


class Settings(BaseModel):
    model: ModelSettings = Field(default_factory=ModelSettings)
    agent: AgentSettings = Field(default_factory=AgentSettings)
    fact_check: FactCheckSettings = Field(default_factory=FactCheckSettings)
    concurrency: int = 1


def load_settings(path: Optional[Path]) -> Settings:
    if path and path.exists():
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        return Settings.model_validate(data)
    return Settings()
