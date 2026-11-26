"""`config sync` and `config check`: keep an instance's `config.toml` in step with the bot's config model.

Sync adds every key the file lacks as a comment (description, version that added it, default), so the code default
keeps applying until someone uncomments it. Keys the model no longer has are commented out with a `removed in` note,
since unknown keys fail validation. A key that is already there as a comment (`# key = …`, also under a commented-out
`# [table]` header) counts as present. Everything else in the file stays as it was.

Check reports what sync would change and validates the config, without writing.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from datetime import date, datetime, time
from pathlib import Path
from typing import Any

import msgspec
import tomlkit
from tomlkit.container import Container
from tomlkit.items import AoT, Comment, Item, Key, Table, Whitespace

from .config import ConfigError, ConfigField, ConfigSection, check_settings, config_fields, load_config

COMMENTED_KEY = re.compile(r"#\s*([A-Za-z0-9_-]+)\s*=")
COMMENTED_TABLE = re.compile(r"#\s*\[([A-Za-z0-9_.-]+)\]")
DESCRIPTION = re.compile(r"#.*\(added in [^()]*\)")

type BodyEntry = tuple[Key | None, Item]


@dataclass
class SyncReport:
    added: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)

    @property
    def in_sync(self) -> bool:
        return not (self.added or self.removed)


def dotted(prefix: str, key: str) -> str:
    return f"{prefix}.{key}" if prefix else key


def comments(lines: list[str]) -> list[BodyEntry]:
    return [(None, tomlkit.comment(line)) for line in lines]


def toml_item(value: Any) -> Item:
    if isinstance(value, dict):
        table = tomlkit.inline_table()
        for key, inner in value.items():
            table.append(key, toml_item(inner))
        return table
    if isinstance(value, list):
        array = tomlkit.array()
        for inner in value:
            array.append(toml_item(inner))
        return array
    return tomlkit.item(value)


def toml_value(value: Any) -> str:
    """`value` as TOML, in the form msgspec decodes back (durations as ISO 8601 strings, enums by value, …)."""
    return toml_item(msgspec.to_builtins(value, builtin_types=(datetime, date, time))).as_string()


@dataclass
class CommentedOut:
    names: set[str] = field(default_factory=set)  # dotted names of keys and tables
    # Where each commented-out table's comment block ends, for keys added to it later
    table_ends: dict[str, tuple[list[BodyEntry], int]] = field(default_factory=dict)


def commented_out(document: Container) -> CommentedOut:
    """The keys and tables that appear commented out, named after the table they would belong to."""
    found = CommentedOut()
    section = ""
    in_commented_table = False

    def visit(container: Container, name: str) -> None:
        nonlocal section, in_commented_table
        body = container.body
        for index, (key, item) in enumerate(body):
            if isinstance(item, Comment):
                text = item.as_string().strip()
                if table := COMMENTED_TABLE.fullmatch(text):
                    section, in_commented_table = table[1], True
                    found.names.add(section)
                elif assignment := COMMENTED_KEY.match(text):
                    found.names.add(dotted(section, assignment[1]))
                else:
                    continue
                if in_commented_table:
                    found.table_ends[section] = (body, index + 1)
            elif isinstance(item, Table) and key is not None:
                section, in_commented_table = dotted(name, key.key), False
                visit(item.value, section)

    visit(document, "")
    return found


def comment_text(item: Item) -> str:
    return item.as_string().strip() if isinstance(item, Comment) else ""


def insertion_index(body: list[BodyEntry]) -> int:
    """Where new keys go in a table's body: before its first sub-table, real or commented out, along with that
    table's description and the blank lines before it."""
    index = next(
        (
            i
            for i, (_, item) in enumerate(body)
            if isinstance(item, (Table, AoT)) or COMMENTED_TABLE.fullmatch(comment_text(item))
        ),
        len(body),
    )
    while index > 0 and (
        isinstance(body[index - 1][1], Whitespace) or DESCRIPTION.fullmatch(comment_text(body[index - 1][1]))
    ):
        index -= 1
    return index


@dataclass
class BodyEdit:
    body: list[BodyEntry]
    start: int
    stop: int
    entries: list[BodyEntry]


class DocumentSync:
    # Edits go straight into the parsed bodies, which leaves tomlkit's key index stale. So they are collected during
    # the walk and applied after it (`apply`), and the document is only rendered afterwards.

    def __init__(self, document: Container, version: str) -> None:
        self.version = version
        self.commented = commented_out(document)
        self.report = SyncReport()
        self.edits: list[BodyEdit] = []
        self.appended: list[BodyEntry] = []  # commented-out tables, for the end of the document
        self.filled: set[str] = set()  # a table can be split into several parts; its missing keys go into the first

    def sync(
        self, part: Container, merged: Any, section_type: type[ConfigSection], name: str, has_header: bool
    ) -> None:
        fields = {f.key: f for f in config_fields(section_type)}
        body = part.body
        for index, (key, item) in enumerate(body):
            if key is None:
                continue
            config_field = fields.get(key.key)
            full_name = dotted(name, key.key)
            if config_field is None:
                replacement = comments([f"removed in {self.version}:", *self.rendered(key, item, full_name)])
                self.edits.append(BodyEdit(body, index, index + 1, replacement))
                self.report.removed.append(full_name)
            elif config_field.section is not None and isinstance(item, Table):
                self.sync(item.value, merged[key.key], config_field.section, full_name, not item.is_super_table())

        if name in self.filled:
            return
        self.filled.add(name)
        absent = [f for f in fields.values() if f.key not in merged]
        leaf_lines = self.add_missing(absent, name)
        if leaf_lines and has_header:
            at = insertion_index(body)
            self.edits.append(BodyEdit(body, at, at, comments(leaf_lines)))
        elif leaf_lines:
            # A table without its own header (only `[name.sub]` exists): new keys need a commented-out header.
            self.append_block([f"[{name}]", *leaf_lines])

    def fill_commented_table(self, section_type: type[ConfigSection], name: str) -> None:
        leaf_lines = self.add_missing(config_fields(section_type), name)
        if leaf_lines:
            body, at = self.commented.table_ends[name]
            self.edits.append(BodyEdit(body, at, at, comments(leaf_lines)))

    def add_missing(self, absent: list[ConfigField], name: str) -> list[str]:
        """Report the fields that aren't even commented out; return the lines for the plain keys among them, and
        add the tables (or fill commented-out ones) at the end of the document."""
        leaf_lines = []
        for f in absent:
            full_name = dotted(name, f.key)
            if f.section is not None and full_name in self.commented.table_ends:
                self.fill_commented_table(f.section, full_name)
            elif full_name in self.commented.names:
                continue
            else:
                self.report.added.append(full_name)
                if f.section is None:
                    leaf_lines += self.leaf_lines(f)
                else:
                    self.append_section(f, full_name)
        return leaf_lines

    def apply(self, document: Container) -> None:
        # Back to front, so earlier indices in the same body stay valid.
        for edit in sorted(self.edits, key=lambda e: (e.start, e.stop), reverse=True):
            edit.body[edit.start : edit.stop] = edit.entries
        document.body.extend(self.appended)

    def leaf_lines(self, config_field: ConfigField) -> list[str]:
        if config_field.required:
            return [self.described(config_field, "required"), f"{config_field.key} ="]
        if config_field.default is None:
            return [self.described(config_field, "unset by default"), f"{config_field.key} ="]
        return [self.described(config_field), f"{config_field.key} = {toml_value(config_field.default)}"]

    def append_section(self, config_field: ConfigField, full_name: str) -> None:
        assert config_field.section is not None
        inner = config_fields(config_field.section)
        note = "required" if config_field.required else ""
        lines = [self.described(config_field, note), f"[{full_name}]"]
        self.append_block(lines + [line for f in inner if f.section is None for line in self.leaf_lines(f)])
        for f in inner:
            if f.section is not None:
                self.append_section(f, dotted(full_name, f.key))

    def append_block(self, lines: list[str]) -> None:
        self.appended += [(None, tomlkit.nl()), *comments(lines)]

    @staticmethod
    def described(config_field: ConfigField, note: str = "") -> str:
        assert config_field.setting is not None
        details = f"added in {config_field.setting.added}" + (f"; {note}" if note else "")
        return f"{config_field.setting.description} ({details})"

    @staticmethod
    def rendered(key: Key, item: Item, full_name: str) -> list[str]:
        if isinstance(item, Table):
            text = f"[{full_name}]\n{item.value.as_string()}"
        else:
            single = tomlkit.document()
            single.append(key, item)
            text = single.as_string()
        return [line for line in text.splitlines() if line.strip()]


def synced_text(config_type: type[ConfigSection], text: str, version: str) -> tuple[str, SyncReport]:
    check_settings(config_type)
    if text and not text.endswith("\n"):
        text += "\n"
    document = tomlkit.parse(text)
    sync = DocumentSync(document, version)
    sync.sync(document, document, config_type, "", has_header=True)
    sync.apply(document)
    return document.as_string(), sync.report


def read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return ""


def write_atomically(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("w", encoding="utf-8") as file:
        file.write(text)
        file.flush()
        os.fsync(file.fileno())
    temporary.replace(path)


def sync_config(config_type: type[ConfigSection], path: Path, version: str) -> SyncReport:
    """Add missing keys as comments and comment out removed ones; the file is only written when it changes."""
    text = read_text(path)
    new_text, report = synced_text(config_type, text, version)
    if new_text != text:
        write_atomically(path, new_text)
    return report


def check_config(config_type: type[ConfigSection], path: Path, version: str) -> list[str]:
    """Everything wrong with the file: validation errors, and keys sync would add or comment out."""
    _, report = synced_text(config_type, read_text(path), version)
    problems = [f"{path}: missing {key} (run config sync)" for key in report.added]
    problems += [f"{path}: {key} is no longer a setting (run config sync)" for key in report.removed]
    try:
        load_config(config_type, path)
    except ConfigError as error:
        problems.append(str(error))
    return problems


def run_config_command(action: str, config_type: type[ConfigSection], path: Path, version: str) -> int:
    """`config sync` or `config check`; returns the exit status."""
    if action == "sync":
        report = sync_config(config_type, path, version)
        for key in report.added:
            print(f"Added (commented out): {key}")
        for key in report.removed:
            print(f"Commented out, removed in {version}: {key}")
        return 0
    if action == "check":
        problems = check_config(config_type, path, version)
        for problem in problems:
            print(problem)
        return 1 if problems else 0
    print(f"Unknown config command: {action!r} (expected sync or check)")
    return 2
