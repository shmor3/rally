"""Rally core modular foundation."""

from __future__ import annotations

from pathlib import Path
import sys
import types

_SUBMODULE_NAMES = ("model", "store", "process", "providers.base", "providers", "cli")

import rally_core.model
import rally_core.store
import rally_core.process
import rally_core.providers.base
import rally_core.providers
import rally_core.cli

_OWNER_MAP: dict[str, str] = {}
for _sub_name in _SUBMODULE_NAMES:
    _mod = sys.modules.get(f"rally_core.{_sub_name}")
    if _mod is not None:
        for _name in dir(_mod):
            if not (_name.startswith("__") and _name.endswith("__")) and _name not in _OWNER_MAP:
                _OWNER_MAP[_name] = _sub_name


def _owner_module(name: str):
    if name in _OWNER_MAP:
        return sys.modules.get(f"rally_core.{_OWNER_MAP[name]}")
    for sub in _SUBMODULE_NAMES:
        mod = sys.modules.get(f"rally_core.{sub}")
        if mod is not None and hasattr(mod, name):
            _OWNER_MAP[name] = sub
            return mod
    return None


class _CompatibilityModule(types.ModuleType):
    """Dynamic two-way synchronization compatibility proxy."""

    def __getattribute__(self, name: str) -> object:
        if name.startswith("__") and name.endswith("__"):
            return super().__getattribute__(name)
        if name in {"_SUBMODULE_NAMES", "_OWNER_MAP", "_CompatibilityModule"}:
            return super().__getattribute__(name)
        if name in _SUBMODULE_NAMES:
            return sys.modules.get(f"rally_core.{name}")
        owner = _owner_module(name)
        if owner is not None:
            if hasattr(owner, name):
                return getattr(owner, name)
            raise AttributeError(f"module {self.__name__!r} has no attribute {name!r}")
        return super().__getattribute__(name)

    def __setattr__(self, name: str, value: object) -> None:
        if name.startswith("__") and name.endswith("__"):
            super().__setattr__(name, value)
            return
        if name in {"_SUBMODULE_NAMES", "_OWNER_MAP", "_CompatibilityModule"}:
            super().__setattr__(name, value)
            return
        if name in _SUBMODULE_NAMES:
            super().__setattr__(name, value)
            sys.modules[f"rally_core.{name}"] = value
            return
        super().__setattr__(name, value)
        owner = _owner_module(name)
        if owner is not None:
            setattr(owner, name, value)
        for sub in _SUBMODULE_NAMES:
            mod = sys.modules.get(f"rally_core.{sub}")
            if mod is not None and mod is not owner and hasattr(mod, name):
                setattr(mod, name, value)

    def __delattr__(self, name: str) -> None:
        owner = _owner_module(name)
        deleted = False
        if owner is not None and hasattr(owner, name):
            delattr(owner, name)
            deleted = True
        if name in self.__dict__:
            super().__delattr__(name)
            deleted = True
        if not deleted:
            raise AttributeError(f"module {self.__name__!r} has no attribute {name!r}")

    def __dir__(self):
        names = set(super().__dir__())
        for sub in _SUBMODULE_NAMES:
            mod = sys.modules.get(f"rally_core.{sub}")
            if mod is not None:
                names.update(dir(mod))
        return sorted(names)


_current_mod = sys.modules[__name__]
_current_mod.__class__ = _CompatibilityModule

for _name in _OWNER_MAP:
    _owner = _owner_module(_name)
    if _owner is not None and hasattr(_owner, _name):
        _current_mod.__dict__[_name] = getattr(_owner, _name)

__all__ = [
    "JobRequest",
    "ProviderOutcome",
    "Job",
    "JobStore",
    "JobManager",
    "atomic_write",
    "worker_main",
    "provider_main",
    "sentinel_main",
]
