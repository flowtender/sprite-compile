"""Tagged-dataclass serialization: registered dataclasses to plain data for msgspec (msgpack) and back.

Encoded dataclasses carry their tag under "type"; decoding looks the tag up in the codec's registry and rebuilds
fields using the class's type hints. Enum members are encoded by name. Other msgspec-native values (datetime,
timedelta, UUID, Decimal, sets, bytes, …) pass through to msgspec and are rebuilt from the field's type hint.

`check_hints()` (run by `build_registry`) rejects field types the codec can't round-trip, before anything is saved.
"""

from __future__ import annotations

import sys
from dataclasses import fields, is_dataclass
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from enum import Enum
from types import ModuleType, NoneType, UnionType
from typing import Annotated, Any, Callable, Literal, TypeVar, Union, get_args, get_origin, get_type_hints
from uuid import UUID

import msgspec

from .modules import iter_public_modules

TAG_KEY = "type"

PLAIN_TYPES = (str, int, float, bool, NoneType)
# Encoded by msgspec as strings, bytes or msgpack extensions; rebuilt with msgspec.convert
NATIVE_SCALARS = (datetime, date, time, timedelta, UUID, Decimal, bytes, bytearray)
CONTAINERS = (list, tuple, set, frozenset, dict)


def own_tag(cls: type) -> str:
    # Only the class's own __tag__ counts; an inherited one would decode as the parent class.
    return vars(cls).get("__tag__", cls.__name__)


def check_no_tag_field(cls: type) -> None:
    """Raise if a dataclass has a field that would overwrite the codec's tag."""
    if is_dataclass(cls) and any(f.name == TAG_KEY for f in fields(cls)):
        raise TypeError(f"{cls.__qualname__} has a field named {TAG_KEY!r}, which the codec uses for its tag")


def union_members(hint: Any) -> tuple[Any, ...]:
    return get_args(hint) if get_origin(hint) in (Union, UnionType) else (hint,)


def without_none(hint: Any) -> Any:
    """`X | None` as `X`, so the value is decoded with X's hint; other hints unchanged."""
    members = [m for m in union_members(hint) if m is not NoneType]
    return members[0] if len(members) == 1 else hint


def enum_member(value: Any, hint: Any) -> Enum | None:
    """The member named `value` of the enum in `hint` (or in its union members), if there is one."""
    if not isinstance(value, str):
        return None
    for candidate in union_members(hint):
        if isinstance(candidate, type) and issubclass(candidate, Enum) and value in candidate.__members__:
            return candidate[value]
    return None


def encode_key(key: Any) -> Any:
    return key.name if isinstance(key, Enum) else key


# Dataclasses sprite-compile itself stores (e.g. the Discord frontend's webhook handles), by tag. Every codec knows
# them, so bots don't register them; a bot class under one of these tags is rejected.
LIBRARY_TYPES: dict[str, type] = {}


def library_type(tag: str) -> Callable[[type], type]:
    """Register one of sprite-compile's own dataclasses with every codec, under `tag`."""

    def decorator(cls: type) -> type:
        check_no_tag_field(cls)
        LIBRARY_TYPES[tag] = cls
        setattr(cls, "__tag__", tag)
        return cls

    return decorator


def library_tag_clash(tag: str, cls: type) -> str | None:
    library_class = LIBRARY_TYPES.get(tag)
    if library_class is None or library_class is cls:
        return None
    return f"{cls.__qualname__}: the tag {tag!r} belongs to sprite-compile's {library_class.__qualname__}"


class MessageCodec:
    def __init__(self) -> None:
        self.registry: dict[str, type] = {}  # the bot's own classes; LIBRARY_TYPES are known too
        self._hint_cache: dict[type, dict[str, Any]] = {}

    def build_registry(self, packages: list[ModuleType]) -> None:
        """Register every dataclass defined in the given packages' public modules under its tag, then
        `check_hints()`. Dataclasses a module only imports are left to the package that defines them."""
        for pkg in packages:
            for mod in iter_public_modules(pkg):
                for obj in vars(mod).values():
                    if isinstance(obj, type) and is_dataclass(obj) and obj.__module__ == mod.__name__:
                        self.claim_tag(own_tag(obj), obj)
        self.check_hints()

    def claim_tag(self, tag: str, cls: type) -> None:
        if (taken_by := self.registry.get(tag)) not in (None, cls):
            raise TypeError(f"{cls.__qualname__}: the tag {tag!r} is already taken by {taken_by.__qualname__}")
        self.registry[tag] = cls

    def add(self, tag: str, cls: type) -> None:
        check_no_tag_field(cls)
        if clash := library_tag_clash(tag, cls):
            raise TypeError(clash)
        self.claim_tag(tag, cls)

    def register(self, tag: str) -> Callable[[type], type]:
        """Register a class under `tag` and encode it with that tag."""

        def decorator(cls: type) -> type:
            self.add(tag, cls)
            setattr(cls, "__tag__", tag)
            return cls

        return decorator

    def alias(self, tag: str) -> Callable[[type], type]:
        """Also decode `tag` as this class, e.g. a renamed class's old name."""

        def decorator(cls: type) -> type:
            self.add(tag, cls)
            return cls

        return decorator

    def lookup(self, tag: str) -> type | None:
        return LIBRARY_TYPES.get(tag) or self.registry.get(tag)

    def is_registered(self, cls: type) -> bool:
        return self.lookup(own_tag(cls)) is cls

    def check_hints(self) -> None:
        """Raise listing every field of every registered dataclass whose type the codec can't round-trip, and every
        class registered under one of sprite-compile's own tags."""
        problems = [clash for tag, cls in self.registry.items() if (clash := library_tag_clash(tag, cls))]
        for cls in dict.fromkeys([*LIBRARY_TYPES.values(), *self.registry.values()]):
            type_hints = self._type_hints(cls)
            for f in fields(cls):
                where = f"{cls.__qualname__}.{f.name}"
                if f.name == TAG_KEY:
                    problems.append(f"{where}: named {TAG_KEY!r}, which the codec uses for its tag")
                problems.extend(f"{where}: {p}" for p in self._hint_problems(type_hints.get(f.name, Any)))
        if problems:
            raise TypeError("The codec can't round-trip these classes or fields:\n  " + "\n  ".join(problems))

    def decode_with_hint(self, value: Any, hint: Any) -> Any:
        # If it's a tagged dict, dispatch regardless of hint
        if isinstance(value, dict) and TAG_KEY in value:
            return self.dataclass_from_dict(value)

        member = enum_member(value, hint)
        if member is not None:
            return member

        hint = without_none(hint)
        origin = get_origin(hint)

        if origin is tuple:
            if isinstance(value, (list, tuple)):
                args = get_args(hint)
                if len(args) == 2 and args[1] is ...:
                    item_t = args[0]
                    return tuple(self.decode_with_hint(x, item_t) for x in value)
                if args:
                    out = []
                    for i, x in enumerate(value):
                        t = args[i] if i < len(args) else Any
                        out.append(self.decode_with_hint(x, t))
                    return tuple(out)
                return tuple(value)
            return value

        if origin in (set, frozenset) and isinstance(value, list):
            (item_t,) = get_args(hint) or (Any,)
            return origin(self.decode_with_hint(x, item_t) for x in value)

        if isinstance(value, list):
            if origin is list:
                (item_t,) = get_args(hint) or (Any,)
                return [self.decode_with_hint(x, item_t) for x in value]
            return [self.decode_with_hint(x, Any) for x in value]

        if isinstance(value, dict):
            # Untagged dict where a dataclass belongs: msgspec encoded the dataclass directly (an encoder bug, as in
            # older chance records); the hint still says which class it was.
            if isinstance(hint, type) and is_dataclass(hint):
                return self._build(hint, value)

            if origin is dict:
                key_t, val_t = get_args(hint) or (Any, Any)

                def coerce_key(k: Any) -> Any:
                    member = enum_member(k, key_t)
                    if member is not None:
                        return member
                    # JSON turns int keys into strings; only convert clean integer strings back
                    if key_t is int and isinstance(k, str):
                        try:
                            return int(k)
                        except ValueError:
                            return k
                    return k

                return {coerce_key(k): self.decode_with_hint(val, val_t) for k, val in value.items()}

            # No hint: do NOT coerce keys; just recurse values
            return {k: self.decode_with_hint(val, Any) for k, val in value.items()}

        if hint in NATIVE_SCALARS and not isinstance(value, hint):
            return msgspec.convert(value, hint, strict=False)

        return value

    def dataclass_from_dict(self, obj: Any) -> Any:
        # Don't guess at objects without a tag
        if not (isinstance(obj, dict) and TAG_KEY in obj):
            return obj

        tag = obj[TAG_KEY]
        if not isinstance(tag, str):
            raise ValueError(f"{TAG_KEY} must be a string")

        cls = self.lookup(tag)
        if cls is None:
            raise ValueError(f"Unknown type tag: {tag}")

        return self._build(cls, obj)

    def dict_from_dataclass(self, obj: Any) -> Any:
        if isinstance(obj, Enum):
            return obj.name
        if is_dataclass(obj) and not isinstance(obj, type):
            cls = type(obj)
            if not self.is_registered(cls):
                raise TypeError(f"{cls.__qualname__} isn't registered with this codec, so it couldn't be decoded")
            out = {TAG_KEY: own_tag(cls)}
            for f in fields(obj):
                out[f.name] = self.dict_from_dataclass(getattr(obj, f.name))
            return out
        if isinstance(obj, (list, tuple, set, frozenset)):
            return [self.dict_from_dataclass(x) for x in obj]
        if isinstance(obj, dict):
            return {encode_key(k): self.dict_from_dataclass(v) for k, v in obj.items()}
        return obj

    def _build(self, cls: type, obj: dict[str, Any]) -> Any:
        type_hints = self._type_hints(cls)
        kwargs = {}
        for f in fields(cls):
            if f.name in obj:
                hint = type_hints.get(f.name, Any)
                kwargs[f.name] = self.decode_with_hint(obj[f.name], hint)
        return cls(**kwargs)

    def _hint_problems(self, hint: Any) -> list[str]:
        if hint is Any or hint is object:
            return [f"{hint!r} doesn't say what to rebuild; use a concrete type"]
        if hint is None or hint in PLAIN_TYPES or hint in NATIVE_SCALARS or isinstance(hint, TypeVar):
            # A generic's type parameter can't be checked here; its concrete type is only known per instance.
            return []
        if hint in CONTAINERS:
            return [f"bare {hint.__name__} doesn't say what its items are"]

        origin = get_origin(hint)
        if origin is Literal:
            return []
        if origin is Annotated:
            return self._hint_problems(get_args(hint)[0])
        if origin in (Union, UnionType) or origin in CONTAINERS:
            return [p for arg in get_args(hint) if arg is not ... for p in self._hint_problems(arg)]

        cls = origin if origin is not None else hint
        if isinstance(cls, type) and issubclass(cls, Enum):
            return []
        if isinstance(cls, type) and is_dataclass(cls):
            if not self.is_registered(cls):
                return [f"{cls.__qualname__} isn't registered with this codec"]
            return [p for arg in get_args(hint) for p in self._hint_problems(arg)]
        return [f"{hint!r} isn't a type the codec or msgspec can round-trip"]

    def _type_hints(self, cls: type) -> dict[str, Any]:
        """Resolve postponed annotations and forward refs (including PEP 695 type parameters) to real types."""
        type_hints = self._hint_cache.get(cls)
        if type_hints is None:
            globalns = vars(sys.modules[cls.__module__])
            localns = dict(vars(cls))
            for tp in getattr(cls, "__type_params__", ()):
                localns[tp.__name__] = tp
            type_hints = get_type_hints(cls, globalns=globalns, localns=localns)
            self._hint_cache[cls] = type_hints
        return type_hints
