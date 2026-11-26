"""Slash commands from typed functions, from chance's `roll_command`.

A command is a plain function whose parameters carry their Discord description (and optionally choices) as
`Annotated` metadata; the annotation itself is anything discord.py accepts (`Range[int, 1, 99]`, `bool`, …):

    @command(desc="Roll some d6s.")
    def roll_dice(
        *,
        dice: Annotated[Range[int, 1, 99], Desc("Number of dice.")],
        label: Annotated[str, Desc("A label to describe the roll.")] = "",
    ) -> DiceRoll: ...

The library never calls the function itself: each command hands `(interaction, function, arguments)` to the bot's
`invoke`, which runs it and answers. `build_command_group` collects the commands of a package (subgroups named after
the modules under it) and/or an explicit list into one top-level group.
"""

from __future__ import annotations

import inspect
import logging
import sys
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass
from types import ModuleType
from typing import Annotated, Any, get_args, get_origin, get_type_hints

import discord
from discord import app_commands

from ..modules import iter_public_modules

log = logging.getLogger(__name__)

CommandFunc = Callable[..., Any]
Invoke = Callable[[discord.Interaction, CommandFunc, dict[str, Any]], Awaitable[None]]
CommandOrGroup = app_commands.Command[Any, ..., Any] | app_commands.Group

MAX_GROUP_CHILDREN = 25  # Discord's limit for subcommands and subgroups in one group


@dataclass(frozen=True)
class Desc:
    text: str


@dataclass(frozen=True)
class Choices:
    values: tuple[app_commands.Choice[Any], ...]


@dataclass(frozen=True)
class CommandMeta:
    group: str | None = None  # one level deep
    name: str | None = None
    desc: str | None = None


def command(*, group: str | None = None, name: str | None = None, desc: str | None = None):
    """Mark a function as a command for package discovery, optionally naming its subgroup, name and description."""
    meta = CommandMeta(group=group, name=name, desc=desc)

    def decorator(fn: CommandFunc) -> CommandFunc:
        setattr(fn, "__command_meta__", meta)
        return fn

    return decorator


def command_meta(fn: CommandFunc) -> CommandMeta:
    return getattr(fn, "__command_meta__", CommandMeta())


def qualified_name(fn: CommandFunc) -> str:
    return f"{fn.__module__}.{fn.__qualname__}"


def first_line(docstring: str | None) -> str | None:
    return docstring.strip().splitlines()[0] if docstring and docstring.strip() else None


def split_annotated(annotation: Any) -> tuple[Any, tuple[Any, ...]]:
    if get_origin(annotation) is Annotated:
        base, *metadata = get_args(annotation)
        return base, tuple(metadata)
    return annotation, ()


def find_metadata[T](metadata: tuple[Any, ...], kind: type[T]) -> T | None:
    return next((item for item in metadata if isinstance(item, kind)), None)


def build_callback(fn: CommandFunc, invoke: Invoke) -> Callable[..., Awaitable[None]]:
    """A discord.py callback taking `fn`'s parameters, with `Annotated` stripped (discord.py would read the metadata as
    a transformer) and the `Desc` and `Choices` applied."""
    hints = get_type_hints(fn, include_extras=True)
    parameters = [
        inspect.Parameter("interaction", inspect.Parameter.POSITIONAL_OR_KEYWORD, annotation=discord.Interaction)
    ]
    descriptions: dict[str, str] = {}
    choices: dict[str, list[app_commands.Choice[Any]]] = {}

    for parameter in inspect.signature(fn).parameters.values():
        annotation, metadata = split_annotated(hints.get(parameter.name, parameter.annotation))
        desc = find_metadata(metadata, Desc)
        if desc is None:
            raise TypeError(f"{qualified_name(fn)}: parameter {parameter.name!r} has no Desc(...)")
        descriptions[parameter.name] = desc.text
        if parameter_choices := find_metadata(metadata, Choices):
            choices[parameter.name] = list(parameter_choices.values)
        parameters.append(
            inspect.Parameter(
                parameter.name, inspect.Parameter.KEYWORD_ONLY, default=parameter.default, annotation=annotation
            )
        )

    async def callback(interaction: discord.Interaction, **arguments: Any) -> None:
        await invoke(interaction, fn, arguments)

    # discord.py reads the parameters through inspect.signature, which takes __signature__ as given. The callback
    # keeps its own __qualname__: discord.py takes a callback for a method (and skips a parameter) when its
    # __qualname__ differs from its __name__ outside a function's <locals>.
    callback.__signature__ = inspect.Signature(parameters)  # type: ignore[attr-defined]
    described = app_commands.describe(**descriptions)(callback)
    return app_commands.choices(**choices)(described) if choices else described


def build_command(fn: CommandFunc, invoke: Invoke, *, strip_prefix: str = "") -> app_commands.Command[Any, ..., Any]:
    """The command for `fn`: named by its `@command(name=)` or its function name minus `strip_prefix`, described by its
    `@command(desc=)` or the first line of its docstring."""
    meta = command_meta(fn)
    description = meta.desc or first_line(inspect.getdoc(fn))
    if description is None:
        raise TypeError(f"{qualified_name(fn)}: no @command(desc=...) and no docstring")
    return app_commands.Command(
        name=meta.name or fn.__name__.removeprefix(strip_prefix),
        description=description,
        callback=build_callback(fn, invoke),
    )


def find_commands(package: ModuleType) -> list[CommandFunc]:
    """The `@command` functions defined in `package`'s public modules; a function a module only imports is left to the
    module that defines it."""
    return [
        obj
        for module in iter_public_modules(package)
        for obj in vars(module).values()
        if inspect.isfunction(obj) and hasattr(obj, "__command_meta__") and obj.__module__ == module.__name__
    ]


def group_from_module(module_name: str, package_name: str) -> str | None:
    """The first module segment under the package: `package.magic.alchemy` → `magic`."""
    if not module_name.startswith(package_name + "."):
        return None
    return module_name.removeprefix(package_name + ".").split(".", 1)[0]


class CommandGroupBuilder:
    """One top-level group plus its subgroups, refusing name clashes and overfull groups with errors that name the
    function responsible (discord.py's own errors only name the command)."""

    def __init__(self, root: app_commands.Group, invoke: Invoke, package: ModuleType | None) -> None:
        self.root = root
        self.invoke = invoke
        self.package = package
        self.subgroups: dict[str, app_commands.Group] = {}
        self.origins: dict[str, str] = {}  # qualified command or subgroup name → the function that added it
        self.command_count = 0

    def add(self, fn: CommandFunc, group_name: str | None, *, strip_prefix: str = "") -> None:
        parent = self.root if group_name is None else self.subgroup(group_name, fn)
        self.attach(parent, build_command(fn, self.invoke, strip_prefix=strip_prefix), qualified_name(fn))
        self.command_count += 1

    def subgroup(self, name: str, fn: CommandFunc) -> app_commands.Group:
        if (group := self.subgroups.get(name)) is not None:
            return group
        group = app_commands.Group(name=name, description=self.subgroup_description(name))
        self.attach(self.root, group, f"the {name!r} subgroup, opened by {qualified_name(fn)}")
        self.subgroups[name] = group
        return group

    def subgroup_description(self, name: str) -> str:
        module = sys.modules.get(f"{self.package.__name__}.{name}") if self.package else None
        docstring = inspect.getdoc(module) if module else None
        return first_line(docstring) or f"{name.title()} commands"

    def attach(self, parent: app_commands.Group, child: CommandOrGroup, origin: str) -> None:
        path = f"{parent.qualified_name} {child.name}"
        if (taken_by := self.origins.get(path)) is not None:
            raise ValueError(f"{origin}: /{path} is already taken by {taken_by}")
        if len(parent.commands) >= MAX_GROUP_CHILDREN:
            raise ValueError(
                f"{origin}: /{parent.qualified_name} already has {MAX_GROUP_CHILDREN} commands and subgroups, "
                "Discord's maximum"
            )
        parent.add_command(child)
        self.origins[path] = origin


def build_command_group(
    name: str,
    description: str,
    invoke: Invoke,
    *,
    package: ModuleType | None = None,
    strip_prefix: str = "",
    functions: Iterable[CommandFunc] = (),
    allowed_installs: app_commands.AppInstallationType | None = None,
    allowed_contexts: app_commands.AppCommandContext | None = None,
) -> app_commands.Group:
    """A top-level group holding the `@command` functions found in `package` and the given `functions`.

    Found commands go into a subgroup named after their module under `package` (unless `@command(group=)` says
    otherwise), described by that module's docstring, and lose `strip_prefix` from their function name. Listed
    functions need no `@command`; they go to the top level unless `@command(group=)` names a subgroup.
    """
    root = app_commands.Group(
        name=name, description=description, allowed_installs=allowed_installs, allowed_contexts=allowed_contexts
    )
    builder = CommandGroupBuilder(root, invoke, package)
    if package is not None:
        for fn in find_commands(package):
            group = command_meta(fn).group or group_from_module(fn.__module__, package.__name__)
            builder.add(fn, group, strip_prefix=strip_prefix)
    for fn in functions:
        builder.add(fn, command_meta(fn).group)
    log.info(
        "Built /%s with %d command(s) across %d subgroup(s)", name, builder.command_count, len(builder.subgroups)
    )
    return root
