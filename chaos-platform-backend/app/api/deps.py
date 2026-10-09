from __future__ import annotations

from functools import lru_cache

from app.core.config import get_settings
from app.engines.registry import EngineRegistry


@lru_cache
def get_registry() -> EngineRegistry:
    """API-side registry. The API process keeps CHAOS_DOCKER_ENABLED=false, so it can never run Docker operations."""
    return EngineRegistry.from_settings(get_settings())
