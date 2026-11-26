"""Package walking shared by the codec registry and command discovery."""

from __future__ import annotations

import importlib
import pkgutil
from collections.abc import Iterator
from types import ModuleType


def iter_public_modules(package: ModuleType) -> Iterator[ModuleType]:
    """Import and yield every module under `package`, at any depth. Package `__init__` modules and modules whose name
    starts with `_` are skipped."""
    for info in pkgutil.walk_packages(package.__path__, package.__name__ + "."):
        if info.ispkg:
            continue
        leaf = info.name.rsplit(".", 1)[-1]
        if leaf.startswith("_"):
            continue
        yield importlib.import_module(info.name)
