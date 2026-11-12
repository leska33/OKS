from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Final, Iterable

from .csma_cd import JAM_SIGNAL, contains_jam
from .cyclic import FCS_BYTES, compute_fcs, correct_data

START_FLAG: Final[bytes] = b"450502-1"
HEADER_BYTES: Final[int] = 2
RESERVED_BYTES: Final[int] = 10 - (FCS_BYTES - 1)
MAX_FRAME_SIZE: Final[int] = 100
MAX_DATA_SIZE: Final[int] = (
    MAX_FRAME_SIZE - len(START_FLAG) - HEADER_BYTES - FCS_BYTES - RESERVED_BYTES
)
BODY_SIZE: Final[int] = MAX_FRAME_SIZE - len(START_FLAG)

                                                  
ERROR_PROBABILITY: Final[float] = 0.10

def _bits_of_byte(value: int) -> Iterable[int]:
    for shift in range(7, -1, -1):
        yield (value >> shift) & 1

def _flag_bits() -> tuple[int, ...]:
    return tuple(bit for byte in START_FLAG for bit in _bits_of_byte(byte))

FLAG_BITS: Final[tuple[int, ...]] = _flag_bits()
FLAG_LENGTH: Final[int] = len(FLAG_BITS)
STUFF_BIT: Final[int] = 1 - FLAG_BITS[-1]

def _build_failure_table(pattern: tuple[int, ...]) -> tuple[int, ...]:
    table = [0] * len(pattern)
    state = 0
    for position in range(1, len(pattern)):
        while state and pattern[position] != pattern[state]:
            state = table[state - 1]
        if pattern[position] == pattern[state]:
            state += 1
        table[position] = state
    return tuple(table)

FLAG_FAILURE: Final[tuple[int, ...]] = _build_failure_table(FLAG_BITS)

class _FlagTracker:

    __slots__ = ("state",)

    def __init__(self) -> None:
        self.state = 0

    def consume(self, bit: int) -> bool:
        while self.state and bit != FLAG_BITS[self.state]:
            self.state = FLAG_FAILURE[self.state - 1]
        if bit == FLAG_BITS[self.state]:
            self.state += 1
        return self.state == FLAG_LENGTH - 1

class _BitWriter:

    __slots__ = ("_bytes", "_value", "_count")

    def __init__(self) -> None:
        self._bytes = bytearray()
        self._value = 0
        self._count = 0

    def append(self, bit: int) -> None:
        self._value = (self._value << 1) | bit
        self._count += 1
        if self._count == 8:
            self._bytes.append(self._value)
            self._value = 0
            self._count = 0

    def finish(self) -> bytes:
        if self._count:
            self._bytes.append(self._value << (8 - self._count))
            self._value = 0
            self._count = 0
        return bytes(self._bytes)

def _binary_byte(value: int) -> str:
    return f"{value:08b}"

def _format_field(field: bytes) -> str:
    return ",".join(_binary_byte(value) for value in field)

def _format_field_with_diff(original: bytes, current: bytes) -> str:
                                                                            
    parts: list[str] = []
    for old_byte, new_byte in zip(original, current, strict=True):
        view: list[str] = []
        for shift in range(7, -1, -1):
            old_bit = (old_byte >> shift) & 1
            new_bit = (new_byte >> shift) & 1
            text = str(new_bit)
            if old_bit != new_bit:
                text = f"<u>{text}</u>"
            view.append(text)
        parts.append("".join(view))
    return ",".join(parts)

@dataclass(frozen=True)
class Frame:

    data: bytes
    frame_type: int = 0
    frame_number: int = 0
    fcs: bytes = bytes(FCS_BYTES)
    reserved: bytes = bytes(RESERVED_BYTES)

    def __post_init__(self) -> None:
        if not self.data or len(self.data) > MAX_DATA_SIZE:
            raise ValueError("Data field does not fit into a frame.")
        if self.frame_type or self.frame_number:
            raise ValueError("Type and number fields must be zero in this laboratory work.")
        if len(self.fcs) != FCS_BYTES:
            raise ValueError("FCS field must have fixed length FCS_BYTES.")
        if self.reserved != bytes(RESERVED_BYTES):
            raise ValueError("Reserved bytes must be zero and have length 9.")

    @property
    def flag(self) -> bytes:
        return START_FLAG

    def padded_data(self) -> bytes:
        return self.data.ljust(MAX_DATA_SIZE, b"\x00")

    @property
    def body(self) -> bytes:
        return b"".join(
            (
                bytes((self.frame_type, self.frame_number)),
                self.padded_data(),
                self.fcs,
                self.reserved,
            )
        )

    def to_bytes(self) -> bytes:
        return START_FLAG + self.body

    def wire_fields(self) -> tuple[bytes, ...]:
        return (
            bytes((self.frame_type,)),
            bytes((self.frame_number,)),
            self.padded_data(),
            self.fcs,
            self.reserved,
        )

@dataclass(frozen=True)
class FrameTransmission:

    wire: bytes
    before: str
    after_html: str
    after_error_html: str
    error_emulated: bool = False
                                                                          
    collisions_display: str = ""

def _stuff_body(fields: tuple[bytes, ...]) -> tuple[bytes, list[str]]:
    tracker = _FlagTracker()
    writer = _BitWriter()
    displayed: list[str] = []

    for field in fields:
        byte_views: list[str] = []
        for value in field:
            view: list[str] = []
            for bit in _bits_of_byte(value):
                writer.append(bit)
                view.append(str(bit))
                if tracker.consume(bit):
                    writer.append(STUFF_BIT)
                    view.append(f"<u>{STUFF_BIT}</u>")
                    tracker.consume(STUFF_BIT)
            byte_views.append("".join(view))
        displayed.append(",".join(byte_views))

    return writer.finish(), displayed

def _visible_status(flag: bytes, fields: tuple[bytes, ...], displayed: list[str] | None = None) -> str:
    if displayed is None:
        visible = fields[:-1]
        return " ".join((_format_field(flag), *map(_format_field, visible)))
    return " ".join((_format_field(flag), *displayed[:-1]))

def _corrupt_data_field(data: bytes) -> tuple[bytes, int, int]:
    if not data:
        return data, -1, -1
    mutable = bytearray(data)
    byte_index = random.randrange(len(mutable))
    bit_index = random.randrange(8)
    mutable[byte_index] ^= 1 << bit_index
    return bytes(mutable), byte_index, bit_index

def encode_frame(
    text: str | bytes,
    *,
    emulate_errors: bool = False,
) -> FrameTransmission:
\
\
\
\
\
\
       
    data = text if isinstance(text, bytes) else text.encode("utf-8")
    padded = data.ljust(MAX_DATA_SIZE, b"\x00") if len(data) < MAX_DATA_SIZE else data
    if len(padded) > MAX_DATA_SIZE:
        raise ValueError("Data field does not fit into a frame.")

    fcs = compute_fcs(padded)
    reserved = bytes(RESERVED_BYTES)
    clean_fields = (
        bytes((0,)),
        bytes((0,)),
        padded,
        fcs,
        reserved,
    )

    before = _visible_status(START_FLAG, clean_fields)
                                                                               
    _clean_stuffed, clean_displayed = _stuff_body(clean_fields)
    after = _visible_status(START_FLAG, clean_fields, clean_displayed)

    error_emulated = False
    send_fields = clean_fields
                                                                                        
    if emulate_errors and random.random() < ERROR_PROBABILITY:
        corrupted_data, _byte_index, _bit_index = _corrupt_data_field(padded)
        send_fields = (
            clean_fields[0],
            clean_fields[1],
            corrupted_data,
            fcs,
            reserved,
        )
        error_emulated = True
                                                                                                
        after_error = " ".join(
            (
                _format_field(START_FLAG),
                _format_field(send_fields[0]),
                _format_field(send_fields[1]),
                _format_field_with_diff(padded, corrupted_data),
                _format_field(fcs),
            )
        )
        stuffed_body, _ = _stuff_body(send_fields)
    else:
                                                                        
        after_error = before
        stuffed_body = _clean_stuffed

    return FrameTransmission(
        wire=START_FLAG + stuffed_body,
        before=before,
        after_html=after,
        after_error_html=after_error,
        error_emulated=error_emulated,
    )

class FrameDecoder:

    def __init__(self) -> None:
        self._source = bytearray()
                                                                            
        self.jam_events = 0

    def feed(self, received: bytes) -> list[Frame]:
        self._source.extend(received)
        ready: list[Frame] = []

        while True:
            flag_offset = self._source.find(START_FLAG)
            if flag_offset < 0:
                                                                                    
                jam_at = contains_jam(self._source)
                if jam_at >= 0:
                                                                  
                    self.jam_events += 1
                    del self._source[: jam_at + len(JAM_SIGNAL)]
                    continue
                self._keep_possible_flag_prefix()
                return ready
            if flag_offset:
                                                                      
                del self._source[:flag_offset]

                                                                                
            jam_at = contains_jam(self._source)
            decoded = self._decode_one(memoryview(self._source)[len(START_FLAG) :])
            if jam_at >= len(START_FLAG):
                if decoded is not None:
                    frame, wire_bytes = decoded
                    frame_end = len(START_FLAG) + wire_bytes
                                                                          
                    if frame is not None and jam_at >= frame_end:
                        ready.append(frame)
                        del self._source[:frame_end]
                        continue
                                                                    
                self.jam_events += 1
                del self._source[: jam_at + len(JAM_SIGNAL)]
                continue

            if decoded is None:
                return ready
            frame, wire_bytes = decoded
            if frame is None:
                del self._source[0]
                continue
            ready.append(frame)
            del self._source[: len(START_FLAG) + wire_bytes]

    def _keep_possible_flag_prefix(self) -> None:
        keep = min(len(self._source), len(START_FLAG) - 1)
        if len(self._source) > keep:
            del self._source[:-keep]

    @staticmethod
    def _decode_one(raw: memoryview) -> tuple[Frame | None, int] | None:
        tracker = _FlagTracker()
        body = bytearray()
        byte_value = 0
        bits_in_byte = 0
        skip_stuff_bit = False
        consumed_bits = 0

        for value in raw:
            for bit in _bits_of_byte(value):
                consumed_bits += 1
                if skip_stuff_bit:
                    if bit != STUFF_BIT:
                        return None, 0
                    tracker.consume(bit)
                    skip_stuff_bit = False
                    if len(body) == BODY_SIZE:
                        return FrameDecoder._build_frame(body, consumed_bits)
                    continue

                byte_value = (byte_value << 1) | bit
                bits_in_byte += 1
                if bits_in_byte == 8:
                    body.append(byte_value)
                    byte_value = 0
                    bits_in_byte = 0

                if tracker.consume(bit):
                    skip_stuff_bit = True
                if len(body) == BODY_SIZE and not skip_stuff_bit:
                    return FrameDecoder._build_frame(body, consumed_bits)
        return None

    @staticmethod
    def _build_frame(body: bytearray, consumed_bits: int) -> tuple[Frame | None, int]:
        data_start = HEADER_BYTES
        data_end = data_start + MAX_DATA_SIZE
        fcs_end = data_end + FCS_BYTES
        padded = bytes(body[data_start:data_end])
        fcs_field = bytes(body[data_end:fcs_end])
        reserved = bytes(body[fcs_end:])

        corrected, _was_corrected, ok = correct_data(padded, fcs_field)
        if not ok:
            return None, 0

        payload = corrected.rstrip(b"\x00")
        if not payload:
            return None, 0

        try:
            payload.decode("utf-8")
            frame = Frame(
                data=payload,
                frame_type=body[0],
                frame_number=body[1],
                fcs=compute_fcs(corrected.ljust(MAX_DATA_SIZE, b"\x00")),
                reserved=reserved,
            )
        except (UnicodeDecodeError, ValueError):
            return None, 0
        return frame, (consumed_bits + 7) // 8
