"""Bounded event parsing of conversation arrays, wrappers, and single graphs."""

import importlib
import math
from collections.abc import Iterator
from dataclasses import dataclass
from decimal import Decimal
from typing import Protocol, cast

from pydantic import JsonValue

from ..base.streams import BinaryReader
from ..report_models import (
    ConversationInspection,
    InspectionLimits,
    UnsupportedConversationStructure,
)
from .reconnaissance import (
    KNOWN_FIELDS,
    ConversationObserver,
    kind,
    safe_label,
)


class ConversationJsonError(ValueError):
    """Malformed or ambiguous JSON; exception text never contains source content."""


class ConversationLimitError(ValueError):
    """A record exceeds a parser bound; the component has incomplete coverage."""


class _BasicParser(Protocol):
    def __call__(
        self, stream: BinaryReader, *, use_float: bool, buf_size: int
    ) -> Iterator[tuple[str, object]]: ...


@dataclass(frozen=True)
class StreamInspection:
    """Complete content-free observations; partial statistics are never returned."""

    structure: ConversationInspection
    shape: dict[str, str]


class _BoundedReader:
    def __init__(self, stream: BinaryReader, maximum: int) -> None:
        self._stream = stream
        self._maximum = maximum
        self._bytes = 0
        self._first = True

    def reset(self) -> None:
        # Account for at most one prefetched parser buffer on either side of a record.
        self._bytes = 0

    def read(self, size: int = -1) -> bytes:
        if size == 0:
            return b""
        payload = self._stream.read(min(size if size > 0 else 65536, 65536))
        self._bytes += len(payload)
        if self._bytes > self._maximum + 2 * 65536:
            raise ConversationLimitError("Conversation byte limit exceeded.")
        if self._first:
            payload = payload.removeprefix(b"\xef\xbb\xbf")
            self._first = False
        return payload


class _Events:
    def __init__(self, stream: BinaryReader, limits: InspectionLimits) -> None:
        module = importlib.import_module("ijson")
        # ijson is an untyped dependency boundary, narrowed to the actual API used here.
        parse = cast(_BasicParser, module.basic_parse)
        self.error_type = cast(type[Exception], module.JSONError)
        self.reader = _BoundedReader(stream, limits.max_conversation_bytes)
        self.events = parse(self.reader, use_float=False, buf_size=65536)
        self.limits = limits
        self.event_count = 0
        self.value_bytes = 0

    def reset(self) -> None:
        self.reader.reset()
        self.event_count = 0
        self.value_bytes = 0

    def next(self) -> tuple[str, object]:
        event, value = next(self.events)
        self.event_count += 1
        if isinstance(value, str):
            self.value_bytes += len(value.encode("utf-8"))
        if self.value_bytes > self.limits.max_conversation_bytes:
            raise ConversationLimitError("Conversation string limit exceeded.")
        if self.event_count > self.limits.max_json_events:
            raise ConversationLimitError("Conversation event limit exceeded.")
        return event, value

    def value(self, first: tuple[str, object], depth: int = 0) -> JsonValue:
        if depth > self.limits.max_json_depth:
            raise ConversationLimitError("JSON nesting limit exceeded.")
        event, value = first
        if event == "start_map":
            result: dict[str, JsonValue] = {}
            while (entry := self.next())[0] != "end_map":
                key_event, key = entry
                if key_event != "map_key" or not isinstance(key, str) or key in result:
                    raise ConversationJsonError("Duplicate or invalid JSON key.")
                result[key] = self.value(self.next(), depth + 1)
            return result
        if event == "start_array":
            items: list[JsonValue] = []
            while (entry := self.next())[0] != "end_array":
                items.append(self.value(entry, depth + 1))
            return items
        if event == "number" and isinstance(value, Decimal):
            number = float(value)
            if not math.isfinite(number):
                raise ConversationJsonError("Nonfinite JSON number.")
            return number
        if event in {"string", "number", "boolean", "null"}:
            if value is None or isinstance(value, (str, bool, int)):
                return value
            if isinstance(value, float) and math.isfinite(value):
                return value
        raise ConversationJsonError("Invalid JSON event.")

    def array(self, observer: ConversationObserver) -> None:
        while True:
            self.reset()
            entry = self.next()
            if entry[0] == "end_array":
                return
            observer.observe(self.value(entry))

    def root(self) -> StreamInspection:
        observer = ConversationObserver()
        first = self.next()
        if first[0] == "start_array":
            self.array(observer)
            return StreamInspection(observer.finish("array"), {"root_type": "array"})
        if first[0] != "start_map":
            value = self.value(first)
            return StreamInspection(
                UnsupportedConversationStructure(root_type=kind(value)), {"root_type": kind(value)}
            )
        fields: dict[str, JsonValue] = {}
        shape: dict[str, str] = {}
        wrapper = False
        while (entry := self.next())[0] != "end_map":
            event, key = entry
            if event != "map_key" or not isinstance(key, str) or key in fields:
                raise ConversationJsonError("Duplicate or invalid root key.")
            if len(fields) >= 10000:
                raise ConversationLimitError("Root field limit exceeded.")
            value_start = self.next()
            if key == "conversations" and value_start[0] == "start_array":
                wrapper = True
                self.array(observer)
                fields[key] = []
                shape[safe_label(key, KNOWN_FIELDS)] = "array"
            else:
                # A single conversation is bounded as a whole; wrapper metadata is also bounded.
                value = self.value(value_start)
                fields[key] = value
                shape[safe_label(key, KNOWN_FIELDS)] = kind(value)
        if wrapper:
            return StreamInspection(observer.finish("object_with_conversations_array"), shape)
        if isinstance(fields.get("mapping"), dict):
            observer.observe(fields)
            return StreamInspection(observer.finish("single_conversation"), shape)
        return StreamInspection(UnsupportedConversationStructure(root_type="object"), shape)


def inspect_conversation_stream(stream: BinaryReader, limits: InspectionLimits) -> StreamInspection:
    """Inspect one conversation at a time, rejecting duplicates and trailing corruption.

    Invalid or oversized components raise content-free typed errors, discarding all
    partial counts. Numeric timestamps remain unresolved representation observations.
    Per-record byte accounting permits two 64 KiB parser buffers of read-ahead.
    """
    events = _Events(stream, limits)
    try:
        result = events.root()
        if next(events.events, None) is not None:
            raise ConversationJsonError("Trailing JSON data.")
        return result
    except (events.error_type, StopIteration, UnicodeError, OverflowError) as error:
        raise ConversationJsonError("Invalid conversation JSON.") from error
