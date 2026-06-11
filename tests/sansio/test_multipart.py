import pytest

from werkzeug.datastructures import Headers
from werkzeug.exceptions import RequestEntityTooLarge
from werkzeug.sansio.multipart import Data
from werkzeug.sansio.multipart import Epilogue
from werkzeug.sansio.multipart import Field
from werkzeug.sansio.multipart import File
from werkzeug.sansio.multipart import MultipartDecoder
from werkzeug.sansio.multipart import MultipartEncoder
from werkzeug.sansio.multipart import NeedData
from werkzeug.sansio.multipart import Preamble
from werkzeug.sansio.multipart import State


def test_decoder_simple() -> None:
    boundary = b"---------------------------9704338192090380615194531385$"
    decoder = MultipartDecoder(boundary)
    data = """
-----------------------------9704338192090380615194531385$
Content-Disposition: form-data; name="fname"

ß∑œß∂ƒå∂
-----------------------------9704338192090380615194531385$
Content-Disposition: form-data; name="lname"; filename="bob"

asdasd
-----------------------------9704338192090380615194531385$--
    """.replace("\n", "\r\n").encode()
    decoder.receive_data(data)
    decoder.receive_data(None)
    events = [decoder.next_event()]
    while not isinstance(events[-1], Epilogue):
        events.append(decoder.next_event())
    assert events == [
        Preamble(data=b""),
        Field(
            name="fname",
            headers=Headers([("Content-Disposition", 'form-data; name="fname"')]),
        ),
        Data(data="ß∑œß∂ƒå∂".encode(), more_data=False),
        File(
            name="lname",
            filename="bob",
            headers=Headers(
                [("Content-Disposition", 'form-data; name="lname"; filename="bob"')]
            ),
        ),
        Data(data=b"asdasd", more_data=False),
        Epilogue(data=b"    "),
    ]
    encoder = MultipartEncoder(boundary)
    result = b""
    for event in events:
        result += encoder.send_event(event)
    assert data == result


@pytest.mark.parametrize(
    "data_start",
    [
        b"A",
        b"\n",
        b"\r",
        b"\r\n",
        b"\n\r",
        b"A\n",
        b"A\r",
        b"A\r\n",
        b"A\n\r",
    ],
)
@pytest.mark.parametrize("data_end", [b"", b"\r\n--foo"])
def test_decoder_data_start_with_different_newline_positions(
    data_start: bytes, data_end: bytes
) -> None:
    boundary = b"foo"
    data = (
        b"\r\n--foo\r\n"
        b'Content-Disposition: form-data; name="test"; filename="testfile"\r\n'
        b"Content-Type: application/octet-stream\r\n\r\n"
        b"" + data_start + b"\r\nBCDE" + data_end
    )
    decoder = MultipartDecoder(boundary)
    decoder.receive_data(data)
    events = [decoder.next_event()]
    # We want to check up to data start event
    while not isinstance(events[-1], Data):
        events.append(decoder.next_event())

    expected = data_start if data_end == b"" else data_start + b"\r\nBCDE"

    assert events == [
        Preamble(data=b""),
        File(
            name="test",
            filename="testfile",
            headers=Headers(
                [
                    (
                        "Content-Disposition",
                        'form-data; name="test"; filename="testfile"',
                    ),
                    ("Content-Type", "application/octet-stream"),
                ]
            ),
        ),
        Data(data=expected, more_data=True),
    ]


def test_chunked_boundaries() -> None:
    boundary = b"--boundary"
    decoder = MultipartDecoder(boundary)
    decoder.receive_data(b"--")
    assert isinstance(decoder.next_event(), NeedData)
    decoder.receive_data(b"--boundary\r\n")
    assert isinstance(decoder.next_event(), Preamble)
    decoder.receive_data(b"Content-Disposition: form-data;")
    assert isinstance(decoder.next_event(), NeedData)
    decoder.receive_data(b'name="fname"\r\n\r\n')
    assert isinstance(decoder.next_event(), Field)
    decoder.receive_data(b"longer than the boundary")
    assert isinstance(decoder.next_event(), Data)
    decoder.receive_data(b"also longer, but includes a linebreak\r\n--")
    assert isinstance(decoder.next_event(), Data)
    assert isinstance(decoder.next_event(), NeedData)
    decoder.receive_data(b"--boundary--\r\n")
    event = decoder.next_event()
    assert isinstance(event, Data)
    assert not event.more_data
    decoder.receive_data(None)
    assert isinstance(decoder.next_event(), Epilogue)


def test_empty_field() -> None:
    boundary = b"foo"
    decoder = MultipartDecoder(boundary)
    data = """
--foo
Content-Disposition: form-data; name="text"
Content-Type: text/plain; charset="UTF-8"

Some Text
--foo
Content-Disposition: form-data; name="empty"
Content-Type: text/plain; charset="UTF-8"

--foo--
    """.replace("\n", "\r\n").encode()
    decoder.receive_data(data)
    decoder.receive_data(None)
    events = [decoder.next_event()]
    while not isinstance(events[-1], Epilogue):
        events.append(decoder.next_event())
    assert events == [
        Preamble(data=b""),
        Field(
            name="text",
            headers=Headers(
                [
                    ("Content-Disposition", 'form-data; name="text"'),
                    ("Content-Type", 'text/plain; charset="UTF-8"'),
                ]
            ),
        ),
        Data(data=b"Some Text", more_data=False),
        Field(
            name="empty",
            headers=Headers(
                [
                    ("Content-Disposition", 'form-data; name="empty"'),
                    ("Content-Type", 'text/plain; charset="UTF-8"'),
                ]
            ),
        ),
        Data(data=b"", more_data=False),
        Epilogue(data=b"    "),
    ]
    encoder = MultipartEncoder(boundary)
    result = b""
    for event in events:
        result += encoder.send_event(event)
    assert data == result


def test_max_parts_state_not_modified_on_rejection() -> None:
    """When max_parts is exceeded, the state stays in PART (clean rejection).

    Previously the state machine transitioned to DATA_START before checking
    max_parts, leaving it in a half-transitioned state.
    """
    boundary = b"foo"
    decoder = MultipartDecoder(boundary, max_parts=1)
    data = (
        b"--foo\r\n"
        b'Content-Disposition: form-data; name="a"\r\n\r\n'
        b"val_a\r\n"
        b"--foo\r\n"
        b'Content-Disposition: form-data; name="b"\r\n\r\n'
        b"val_b\r\n"
        b"--foo--"
    )
    decoder.receive_data(data)
    decoder.receive_data(None)

    # First part succeeds
    events = []
    events.append(decoder.next_event())  # Preamble
    assert isinstance(events[0], Preamble)
    events.append(decoder.next_event())  # Field
    assert isinstance(events[1], Field)
    events.append(decoder.next_event())  # Data
    assert isinstance(events[2], Data)
    assert not events[2].more_data

    # Second part: headers parsed, max_parts exceeded BEFORE state transition.
    # State should still be PART, not DATA_START.
    with pytest.raises(RequestEntityTooLarge):
        decoder.next_event()

    assert decoder.state == State.PART
    assert decoder._parts_decoded == 2


def test_decoder_file_data_not_limited_by_max_form_memory_size() -> None:
    """The decoder's buffer accepts file data of any size.

    Previously, receive_data() checked the total buffer size against
    max_form_memory_size, which incorrectly rejected large file chunks.
    Field size limits are enforced at the MultiPartParser level.
    """
    boundary = b"foo"
    # Set a very small max_form_memory_size
    decoder = MultipartDecoder(boundary, max_form_memory_size=10)

    # File data much larger than max_form_memory_size
    file_content = b"x" * 500
    data = (
        b"--foo\r\n"
        b'Content-Disposition: form-data; name="f"; filename="big.bin"\r\n'
        b"Content-Type: application/octet-stream\r\n\r\n"
        + file_content
        + b"\r\n--foo--"
    )

    # This should NOT raise — the old buffer-level check would reject
    # this because len(buffer) + len(data) > max_form_memory_size.
    decoder.receive_data(data)
    decoder.receive_data(None)

    # Verify the file part parses correctly
    events = [decoder.next_event()]
    while not isinstance(events[-1], Epilogue):
        events.append(decoder.next_event())

    assert isinstance(events[1], File)
    assert events[1].filename == "big.bin"
    # Collect all data events
    file_data = b""
    for ev in events[2:]:
        if isinstance(ev, Data):
            file_data += ev.data
    assert file_data == file_content


def test_data_start_with_missing_linebreak() -> None:
    """Graceful handling when _parse_data(start=True) has no line break.

    Previously, _parse_data(start=True) called
    ``LINE_BREAK_RE.match(data).end()`` without a None check, which
    would crash with AttributeError when the buffer didn't start with
    CRLF/CR/LF.
    """
    boundary = b"foo"
    decoder = MultipartDecoder(boundary)

    # Test 1: _parse_data with start=True returns safe default when
    # buffer doesn't start with a line break (instead of crashing).
    data, del_index, more_data = decoder._parse_data(b"no line break", start=True)
    assert data == b""
    assert del_index == 0
    assert more_data is True

    # Test 2: Normal case with leading line break still works.
    # _parse_data checks self.buffer for boundary presence, so we
    # must set the buffer to include the boundary.
    test_data = b"\r\nsome data\r\n--foo--"
    decoder.buffer = bytearray(test_data)
    data, del_index, more_data = decoder._parse_data(test_data, start=True)
    assert data == b"some data"
    assert not more_data

    # Test 3: Full integration — parser handles headers-only input
    # without AttributeError. The PART handler leaves the mandatory
    # body line break (\r\n) in the buffer, which DATA_START processes.
    decoder2 = MultipartDecoder(boundary)
    decoder2.receive_data(
        b"--foo\r\n"
        b'Content-Disposition: form-data; name="test"\r\n\r\n'
    )
    # Preamble + Field parse fine, then buffer has mandatory \r\n
    e = decoder2.next_event()  # Preamble
    assert isinstance(e, Preamble)
    e = decoder2.next_event()  # Field
    assert isinstance(e, Field)
    e = decoder2.next_event()  # Data (from mandatory \r\n)
    assert isinstance(e, Data)

    # Signal end of stream
    decoder2.receive_data(None)
    # Should get ValueError (not AttributeError) for incomplete data
    with pytest.raises(ValueError, match="Invalid form-data"):
        decoder2.next_event()


def test_max_parts_with_file_and_field() -> None:
    """max_parts counts both Field and File parts."""
    boundary = b"foo"
    decoder = MultipartDecoder(boundary, max_parts=2)
    data = (
        b"--foo\r\n"
        b'Content-Disposition: form-data; name="a"\r\n\r\n'
        b"val\r\n"
        b"--foo\r\n"
        b'Content-Disposition: form-data; name="f"; filename="a.txt"\r\n'
        b"Content-Type: text/plain\r\n\r\n"
        b"content\r\n"
        b"--foo\r\n"
        b'Content-Disposition: form-data; name="b"\r\n\r\n'
        b"val2\r\n"
        b"--foo--"
    )
    decoder.receive_data(data)
    decoder.receive_data(None)

    events = []
    events.append(decoder.next_event())  # Preamble
    assert isinstance(events[-1], Preamble)
    events.append(decoder.next_event())  # Field "a"
    assert isinstance(events[-1], Field)
    events.append(decoder.next_event())  # Data for "a"
    assert isinstance(events[-1], Data)
    events.append(decoder.next_event())  # File "f"
    assert isinstance(events[-1], File)
    events.append(decoder.next_event())  # Data for "f"
    assert isinstance(events[-1], Data)

    # Third part exceeds max_parts=2
    with pytest.raises(RequestEntityTooLarge):
        decoder.next_event()
    assert decoder.state == State.PART
