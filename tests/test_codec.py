from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from enum import Enum
from typing import Any
from uuid import UUID

import codec_fixtures
import codec_fixtures_tag_clash
import msgspec
import pytest

from sprite_compile.codec import LIBRARY_TYPES, MessageCodec, library_type


@dataclass(frozen=True)
class Die:
    sides: int
    label: str = ""


@dataclass
class Pool:
    dice: list[Die]
    by_id: dict[int, Die]
    pair: tuple[Die, Die]
    rest: tuple[Die, ...]
    best: Die | None = None
    notes: list[str] = field(default_factory=list)


@dataclass
class Box[T]:
    item: T
    history: list[T]


@dataclass
class Coin:
    heads: bool


@dataclass
class Hand:
    held: list[Die | Coin]
    by_name: dict[str, list[Die]]


@dataclass
class Node:
    name: str
    children: list[Node] = field(default_factory=list)


@dataclass(frozen=True)
class TierSpec:
    label: str
    dice: int


class Tier(Enum):
    LOW = TierSpec("Low", 3)
    HIGH = TierSpec("High", 5)


@dataclass
class Lifestyle:
    tier: Tier
    fallback: Tier | None = None
    costs: dict[Tier, int] = field(default_factory=dict)


@dataclass
class Native:
    at: datetime
    day: date
    delay: timedelta
    maybe_delay: timedelta | None
    id: UUID
    price: Decimal
    raw: bytes
    tags: set[str]
    frozen: frozenset[int]
    dice: set[Die]


@dataclass(frozen=True)
class Step:
    n: int
    roll: Die


@dataclass
class Run:
    steps: tuple[Step, ...]


@dataclass
class Weapon:
    type: str


class NotData:
    pass


@dataclass
class Unencodable:
    anything: Any
    items: list
    stranger: Weapon
    thing: NotData
    nested: dict[str, list[NotData]]


def make_codec(*classes: type) -> MessageCodec:
    codec = MessageCodec()
    for cls in classes:
        codec.registry[cls.__name__] = cls
    return codec


def round_trip(codec: MessageCodec, obj: object) -> object:
    encoded = msgspec.msgpack.decode(msgspec.msgpack.encode(codec.dict_from_dataclass(obj)))
    return codec.dataclass_from_dict(encoded)


def test_nested_dataclasses_round_trip():
    codec = make_codec(Die, Pool)
    pool = Pool(
        dice=[Die(6), Die(20, "d20")],
        by_id={1: Die(4), 22: Die(8)},
        pair=(Die(10), Die(12)),
        rest=(Die(2), Die(3), Die(100)),
        best=Die(20),
        notes=["crit"],
    )

    assert round_trip(codec, pool) == pool


def test_int_dict_keys_come_back_as_ints_from_json():
    codec = make_codec(Die, Pool)
    pool = Pool(dice=[], by_id={7: Die(6)}, pair=(Die(1), Die(1)), rest=())

    restored = codec.dataclass_from_dict(json.loads(json.dumps(codec.dict_from_dataclass(pool))))

    assert list(restored.by_id) == [7]


def test_generic_dataclass_round_trips():
    codec = make_codec(Die, Box)
    box = Box(item=Die(6), history=[Die(4), Die(8)])

    assert round_trip(codec, box) == box


def test_union_members_decode_by_their_own_tag():
    codec = make_codec(Die, Coin, Hand)
    hand = Hand(held=[Die(6), Coin(True)], by_name={"left": [Die(4)], "right": []})

    assert round_trip(codec, hand) == hand


def test_self_referential_dataclass_round_trips():
    codec = make_codec(Node)
    tree = Node("root", [Node("a", [Node("a1")]), Node("b")])

    assert round_trip(codec, tree) == tree


def test_msgspec_native_types_round_trip():
    codec = make_codec(Die, Native)
    native = Native(
        at=datetime(2026, 11, 20, 12, 30, tzinfo=UTC),
        day=date(2026, 11, 20),
        delay=timedelta(hours=6),
        maybe_delay=timedelta(days=2),
        id=UUID("12345678-1234-5678-1234-567812345678"),
        price=Decimal("1.50"),
        raw=b"\x00\xff",
        tags={"a", "b"},
        frozen=frozenset({1, 2}),
        dice={Die(6), Die(20)},
    )

    assert round_trip(codec, native) == native


def test_enum_members_round_trip_by_name():
    codec = make_codec(Lifestyle)
    lifestyle = Lifestyle(tier=Tier.LOW, fallback=Tier.HIGH, costs={Tier.LOW: 100, Tier.HIGH: 500})

    assert codec.dict_from_dataclass(lifestyle)["tier"] == "LOW"
    assert round_trip(codec, lifestyle) == lifestyle


def test_optional_enum_left_empty_round_trips():
    codec = make_codec(Lifestyle)
    lifestyle = Lifestyle(tier=Tier.HIGH)

    assert round_trip(codec, lifestyle) == lifestyle


def test_untagged_dataclasses_are_rebuilt_from_hints():
    # How msgspec stored dataclasses that the old codec passed through untagged, e.g. inside tuples
    codec = make_codec(Die, Step, Run)
    payload = {"type": "Run", "steps": [{"n": 1, "roll": {"sides": 6, "label": "d6"}}]}

    assert codec.dataclass_from_dict(payload) == Run(steps=(Step(1, Die(6, "d6")),))


def test_missing_fields_fall_back_to_defaults():
    codec = make_codec(Die)

    assert codec.dataclass_from_dict({"type": "Die", "sides": 6}) == Die(6)


def test_alias_decodes_old_tag():
    codec = MessageCodec()
    codec.alias("OldDie")(Die)

    assert codec.dataclass_from_dict({"type": "OldDie", "sides": 6}) == Die(6)


def test_tag_taken_by_another_class_is_rejected():
    codec = MessageCodec()
    codec.alias("OldDie")(Die)

    @dataclass
    class Coin:
        heads: bool

    with pytest.raises(TypeError, match="Coin: the tag 'OldDie' is already taken by Die"):
        codec.alias("OldDie")(Coin)
    codec.alias("OldDie")(Die)  # the same class again is fine


def test_registered_tag_is_not_inherited_by_subclasses():
    codec = MessageCodec()

    @codec.register("custom-base")
    @dataclass
    class Base:
        value: int

    @dataclass
    class Child(Base):
        extra: int = 0

    codec.registry["Child"] = Child

    assert codec.dict_from_dataclass(Base(1))["type"] == "custom-base"
    assert codec.dataclass_from_dict(codec.dict_from_dataclass(Child(1, 2))) == Child(1, 2)


def test_unknown_tag_raises():
    with pytest.raises(ValueError, match="Unknown type tag: Nope"):
        MessageCodec().dataclass_from_dict({"type": "Nope"})


@pytest.mark.parametrize("obj", [Die(6), [Die(6)], (Die(6),), {Die(6)}, {"k": Die(6)}], ids=repr)
def test_encoding_unregistered_dataclass_raises(obj):
    with pytest.raises(TypeError, match="Die isn't registered"):
        MessageCodec().dict_from_dataclass(obj)


def test_build_registry_takes_only_dataclasses_defined_in_public_modules():
    codec = MessageCodec()
    codec.build_registry([codec_fixtures])

    assert set(codec.registry) == {"Circle", "Ring"}


@pytest.mark.parametrize(
    "attach",
    [
        lambda codec, cls: codec.register("weapon")(cls),
        lambda codec, cls: codec.alias("OldWeapon")(cls),
    ],
    ids=["register", "alias"],
)
def test_field_named_like_the_tag_is_rejected_at_registration(attach):
    with pytest.raises(TypeError, match="Weapon has a field named 'type'"):
        attach(MessageCodec(), Weapon)


def test_build_registry_checks_hints():
    with pytest.raises(TypeError, match="Weapon.type: named 'type'"):
        MessageCodec().build_registry([codec_fixtures_tag_clash])


def test_check_hints_accepts_everything_the_codec_round_trips():
    make_codec(Die, Pool, Box, Coin, Hand, Node, Lifestyle, Native, Step, Run).check_hints()


def test_check_hints_lists_every_problem():
    codec = make_codec(Unencodable)

    with pytest.raises(TypeError) as error:
        codec.check_hints()

    problems = str(error.value).splitlines()[1:]
    assert [p.split(":")[0].strip() for p in problems] == [
        "Unencodable.anything",
        "Unencodable.items",
        "Unencodable.stranger",
        "Unencodable.thing",
        "Unencodable.nested",
    ]


@pytest.fixture
def library_die(monkeypatch: pytest.MonkeyPatch) -> type:
    """A sprite-compile dataclass, registered with every codec for this test only."""
    monkeypatch.setitem(LIBRARY_TYPES, "LibraryDie", None)

    @library_type("LibraryDie")
    @dataclass(frozen=True)
    class LibraryDie:
        sides: int

    return LibraryDie


def test_library_types_round_trip_through_any_codec(library_die: type):
    codec = MessageCodec()

    encoded = codec.dict_from_dataclass([library_die(20)])
    assert codec.decode_with_hint(encoded, list[library_die]) == [library_die(20)]


def test_bot_class_under_a_library_tag_is_rejected(library_die: type):
    with pytest.raises(TypeError, match="the tag 'LibraryDie' belongs to sprite-compile's"):
        MessageCodec().register("LibraryDie")(Die)
