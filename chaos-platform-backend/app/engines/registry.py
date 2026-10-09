from __future__ import annotations

from app.core.config import Settings
from app.core.exceptions import ValidationRejected
from app.engines.base import FaultEngine
from app.engines.chaos_mesh import ChaosMeshEngine
from app.engines.chaos_toolkit import ChaosToolkitEngine
from app.engines.pumba import PumbaEngine
from app.engines.toxiproxy import ToxiproxyEngine


class EngineRegistry:
    def __init__(self, engines: dict[str, FaultEngine]) -> None:
        self._engines = engines

    @classmethod
    def from_settings(cls, s: Settings) -> EngineRegistry:
        return cls({e.name: e for e in (ToxiproxyEngine(s), PumbaEngine(s), ChaosToolkitEngine(s), ChaosMeshEngine(s))})

    def get(self, name: str) -> FaultEngine:
        try:
            return self._engines[name]
        except KeyError as exc:
            raise ValidationRejected(f"unknown engine '{name}'", {"engines": sorted(self._engines)}) from exc

    def names(self) -> list[str]:
        return sorted(self._engines)
