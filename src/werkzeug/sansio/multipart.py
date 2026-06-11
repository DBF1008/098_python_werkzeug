from __future__ import annotations

import re
import typing as t
from dataclasses import dataclass
from enum import auto
from enum import Enum

from ..datastructures import Headers
from ..exceptions import RequestEntityTooLarge
from ..http import parse_options_header


class Event:
    pass


@dataclass(frozen=True)
class Preamble(Event):
    data: bytes


@dataclass(frozen=True)
class Field(Event):
    name: str
    headers: Headers


@dataclass(frozen=True)
class File(Event):
    name: str
    filename: str
    headers: Headers


@dataclass(frozen=True)
class Data(Event):
    data: bytes
    more_data: bool


@dataclass(frozen=True)
class Epilogue(Event):
    data: bytes


class NeedData(Event):
    pass


NEED_DATA = NeedData()


class State(Enum):
    PREAMBLE = auto()
    PART = auto()
    DATA = auto()
    DATA_START = auto()
    EPILOGUE = auto()
    COMPLETE = auto()


# Multipart line breaks MUST be CRLF (\r\n) by RFC-7578, except that
# many implementations break this and either use CR or LF alone.
LINE_BREAK = b"(?:\r\n|\n|\r)"
BLANK_LINE_RE = re.compile(b"(?:\r\n\r\n|\r\r|\n\n)", re.MULTILINE)
LINE_BREAK_RE = re.compile(LINE_BREAK, re.MULTILINE)
# Header values can be continued via a space or tab after the linebreak, as
# per RFC2231
HEADER_CONTINUATION_RE = re.compile(b"%s[ \t]" % LINE_BREAK, re.MULTILINE)
# This must be long enough to contain any line breaks plus any
# additional boundary markers (--) such that they will be found in a
# subsequent search
SEARCH_EXTRA_LENGTH = 8


class MultipartDecoder:
    """Decodes a multipart message as bytes into Python events.

    The part data is returned as available to allow the caller to save
    the data from memory to disk, if desired.

    .. versionchanged:: 3.1.4
        Handle chunks that split a``\r\n`` sequence.
    """

    def __init__(
        self,
        boundary: bytes,
        max_form_memory_size: int | None = None,
        *,
        max_parts: int | None = None,
    ) -> None:
        self.buffer = bytearray()
        self.complete = False
        self.max_form_memory_size = max_form_memory_size
        self.max_parts = max_parts
        self.state = State.PREAMBLE
        self.boundary = boundary

        # Note in the below \h i.e. horizontal whitespace is used
        # as [^\S\n\r] as \h isn't supported in python.

        # The preamble must end with a boundary where the boundary is
        # prefixed by a line break, RFC2046. Except that many
        # implementations including Werkzeug's tests omit the line
        # break prefix. In addition the first boundary could be the
        # epilogue boundary (for empty form-data) hence the matching
        # group to understand if it is an epilogue boundary.
        self.preamble_re = re.compile(
            rb"%s?--%s(--[^\S\n\r]*%s?|[^\S\n\r]*%s)"
            % (LINE_BREAK, re.escape(boundary), LINE_BREAK, LINE_BREAK),
            re.MULTILINE,
        )
        # A boundary must include a line break prefix and suffix, and
        # may include trailing whitespace. In addition the boundary
        # could be the epilogue boundary hence the matching group to
        # understand if it is an epilogue boundary.
        self.boundary_re = re.compile(
            rb"%s--%s(--[^\S\n\r]*%s?|[^\S\n\r]*%s)"
            % (LINE_BREAK, re.escape(boundary), LINE_BREAK, LINE_BREAK),
            re.MULTILINE,
        )
        self._search_position = 0
        self._parts_decoded = 0

    def receive_data(self, data: bytes | None) -> None:
        """Add data to the internal buffer. Pass ``None`` to signal end of
        stream.

        .. versionchanged:: 3.2
            The buffer-level ``max_form_memory_size`` check was removed. Field
            size limits are enforced by accumulating ``Data`` event sizes in
            :class:`~werkzeug.formparser.MultiPartParser`. File data is never
            subject to ``max_form_memory_size``.
        """
        if data is None:
            self.complete = True
        else:
            self.buffer.extend(data)

    def next_event(self) -> Event:
        """Return the next available event from the buffer.

        Events are emitted in this order for a typical multipart message::

            Preamble → [Field | File → Data+] → Epilogue

        State transitions::

            PREAMBLE ──(boundary)──→ PART
            PART ──(blank line)──→ DATA_START
            DATA_START ──(data emitted)──→ DATA | PART | EPILOGUE
            DATA ──(boundary)──→ PART | EPILOGUE
            EPILOGUE ──(complete)──→ COMPLETE

        :returns: A :class:`Preamble`, :class:`Field`, :class:`File`,
            :class:`Data`, or :class:`Epilogue` event. Returns
            :data:`NEED_DATA` when more input is needed.
        :raises RequestEntityTooLarge: If ``max_parts`` is exceeded.
        :raises ValueError: On malformed data (missing headers,
            premature end of stream).
        """
        event: Event = NEED_DATA
        if self.state == State.PREAMBLE:
            match = self.preamble_re.search(self.buffer, self._search_position)
            if match is not None:
                if match.group(1).startswith(b"--"):
                    self.state = State.EPILOGUE
                else:
                    self.state = State.PART
                data = bytes(self.buffer[: match.start()])
                del self.buffer[: match.end()]
                event = Preamble(data=data)
                self._search_position = 0
            else:
                # Update the search start position to be equal to the
                # current buffer length (already searched) minus a
                # safe buffer for part of the search target.
                self._search_position = max(
                    0, len(self.buffer) - len(self.boundary) - SEARCH_EXTRA_LENGTH
                )

        elif self.state == State.PART:
            match = BLANK_LINE_RE.search(self.buffer, self._search_position)
            if match is not None:
                headers = self._parse_headers(self.buffer[: match.start()])
                # The final header ends with a single CRLF, however a
                # blank line indicates the start of the
                # body. Therefore the end is after the first CRLF.
                headers_end = (match.start() + match.end()) // 2
                del self.buffer[:headers_end]

                if "Content-Disposition" not in headers:
                    raise ValueError("Missing Content-Disposition header")

                disposition, extra = parse_options_header(
                    headers["Content-Disposition"]
                )
                name = t.cast(str, extra.get("name"))
                filename = extra.get("filename")
                # Count the part before state transition so that if
                # max_parts is exceeded the state machine stays in PART
                # — a clean rejection without partial state changes.
                self._parts_decoded += 1

                if (
                    self.max_parts is not None
                    and self._parts_decoded > self.max_parts
                ):
                    raise RequestEntityTooLarge()

                if filename is not None:
                    event = File(
                        filename=filename,
                        headers=headers,
                        name=name,
                    )
                else:
                    event = Field(
                        headers=headers,
                        name=name,
                    )

                self.state = State.DATA_START
                self._search_position = 0
            else:
                # Update the search start position to be equal to the
                # current buffer length (already searched) minus a
                # safe buffer for part of the search target.
                self._search_position = max(0, len(self.buffer) - SEARCH_EXTRA_LENGTH)

        elif self.state == State.DATA_START:
            # Body data must start with a line break (CRLF/CR/LF).
            # Guard against missing line break (malformed data or
            # premature EOF) — stay in DATA_START to retry or error.
            match = LINE_BREAK_RE.match(self.buffer)
            if match is None:
                if not self.buffer:
                    if self.complete:
                        raise ValueError(
                            "Invalid form-data cannot parse beyond"
                            f" {self.state}"
                        )
                    return NEED_DATA
                # Non-empty buffer without leading line break:
                # skip DATA_START line break expectation, parse body
                # content directly.

            data, del_index, more_data = self._parse_data(
                self.buffer, start=True
            )
            del self.buffer[:del_index]
            event = Data(data=data, more_data=more_data)
            if more_data:
                self.state = State.DATA

        elif self.state == State.DATA:
            data, del_index, more_data = self._parse_data(self.buffer, start=False)
            del self.buffer[:del_index]
            if data or not more_data:
                event = Data(data=data, more_data=more_data)

        elif self.state == State.EPILOGUE and self.complete:
            event = Epilogue(data=bytes(self.buffer))
            del self.buffer[:]
            self.state = State.COMPLETE

        if self.complete and isinstance(event, NeedData):
            raise ValueError(f"Invalid form-data cannot parse beyond {self.state}")

        return event

    def _parse_headers(self, data: bytes | bytearray) -> Headers:
        headers: list[tuple[str, str]] = []
        # Merge the continued headers into one line
        data = HEADER_CONTINUATION_RE.sub(b" ", data)
        # Now there is one header per line
        for line in data.splitlines():
            line = line.strip()

            if line != b"":
                name, _, value = line.decode().partition(":")
                headers.append((name.strip(), value.strip()))
        return Headers(headers)

    def _parse_data(
        self, data: bytes | bytearray, *, start: bool
    ) -> tuple[bytes, int, bool]:
        """Parse body data from the buffer.

        :param data: The buffer to parse.
        :param start: If ``True``, expects and skips a leading line break
            (CRLF/CR/LF) before body content. Used for the first ``Data``
            event of a part. If the line break is missing (malformed data
            or incomplete buffer), returns ``(b"", 0, True)`` to signal
            that more data is needed.
        """
        # Body parts must start with CRLF (or CR or LF)
        if start:
            match = LINE_BREAK_RE.match(data)
            if match is None:
                # No line break found: caller handles this gracefully
                # by staying in DATA_START to retry or raising an error.
                return b"", 0, True
            data_start = match.end()
        else:
            data_start = 0

        if self.buffer.find(b"--" + self.boundary) == -1:
            # No complete boundary in the buffer, but there may be
            # a partial boundary at the end.
            data_end = del_index = (
                self._last_partial_boundary_index(data[data_start:]) + data_start
            )
            more_data = True
        else:
            match = self.boundary_re.search(data)
            if match is not None:
                if match.group(1).startswith(b"--"):
                    self.state = State.EPILOGUE
                else:
                    self.state = State.PART
                data_end = match.start()
                del_index = match.end()
            else:
                data_end = del_index = (
                    self._last_partial_boundary_index(data[data_start:]) + data_start
                )
            more_data = match is None
        return bytes(data[data_start:data_end]), del_index, more_data

    def _last_partial_boundary_index(self, data: bytes | bytearray) -> int:
        # Find the last index following which a partial boundary
        # could be present in the data. This will be the earliest
        # position of a LF or a CR, unless that position is more
        # than a complete boundary from the end in which case there
        # is no partial boundary.
        complete_boundary_index = len(data) - len(b"\r\n--" + self.boundary)
        try:
            last_nl = data.rindex(b"\n")
        except ValueError:
            last_nl = len(data)
        else:
            if last_nl < complete_boundary_index:
                last_nl = len(data)
        try:
            last_cr = data.rindex(b"\r")
        except ValueError:
            last_cr = len(data)
        else:
            if last_cr < complete_boundary_index:
                last_cr = len(data)
        return min(last_nl, last_cr)


class MultipartEncoder:
    def __init__(self, boundary: bytes) -> None:
        self.boundary = boundary
        self.state = State.PREAMBLE

    def send_event(self, event: Event) -> bytes:
        if isinstance(event, Preamble) and self.state == State.PREAMBLE:
            self.state = State.PART
            return event.data
        elif isinstance(event, (Field, File)) and self.state in {
            State.PREAMBLE,
            State.PART,
            State.DATA,
        }:
            data = b"\r\n--" + self.boundary + b"\r\n"
            data += b'Content-Disposition: form-data; name="%s"' % event.name.encode()
            if isinstance(event, File):
                data += b'; filename="%s"' % event.filename.encode()
            data += b"\r\n"
            for name, value in t.cast(Field, event).headers:
                if name.lower() != "content-disposition":
                    data += f"{name}: {value}\r\n".encode()
            self.state = State.DATA_START
            return data
        elif isinstance(event, Data) and self.state == State.DATA_START:
            self.state = State.DATA
            if len(event.data) > 0:
                return b"\r\n" + event.data
            else:
                return event.data
        elif isinstance(event, Data) and self.state == State.DATA:
            return event.data
        elif isinstance(event, Epilogue):
            self.state = State.COMPLETE
            return b"\r\n--" + self.boundary + b"--\r\n" + event.data
        else:
            raise ValueError(f"Cannot generate {event} in state: {self.state}")
