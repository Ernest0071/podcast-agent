from pathlib import Path

import pytest

from podcast_agent.config import Settings
from podcast_agent.ingest import load_episode
from podcast_agent.kb import KnowledgeBase
from podcast_agent.tools import AgentState

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def settings() -> Settings:
    s = Settings()
    s.fact_check.web_search = False
    return s


@pytest.fixture
def kb() -> KnowledgeBase:
    return KnowledgeBase.load(ROOT / "data" / "knowledge_base.json")


@pytest.fixture
def ep001():
    return load_episode(ROOT / "data" / "transcripts" / "ep001_remote_work.json")


@pytest.fixture
def state(ep001, settings, kb) -> AgentState:
    return AgentState(episode=ep001, settings=settings, kb=kb)
