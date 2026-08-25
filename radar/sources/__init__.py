"""Source registry.

`build_all` turns a Config into the list of Sources to run. Adding a new
KIND of source means adding a module here; adding another instance of an
existing kind is a config edit.
"""
from __future__ import annotations

from ..config import Config
from .base import Source, collect
from . import boards, joblists

__all__ = ["Source", "collect", "build_all", "boards", "joblists"]


def build_all(cfg: Config) -> list[Source]:
    sources: list[Source] = list(joblists.from_config(cfg.job_lists))
    if cfg.check_boards:
        sources += boards.from_config(cfg.companies)
    return sources
