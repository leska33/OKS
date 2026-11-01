"""Packet serialization and stream decoding for laboratory works No. 2–3."""

from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Final, Iterable

from .cyclic import FCS_BYTES, compute_fcs, correct_data


# Флаг: «45050» + цифра группы + «-» + номер варианта → 450502-1.
START_FLAG: Final[bytes] = b"450502-1"
# FrameType + FrameNumber (контрольное поле — отдельное FCS/Checksum).
HEADER_BYTES: Final[int] = 2
# ЛР2: 10 резервных байт. ЛР3: FCS = 2 байта = бывший Checksum (1) + 1 из резерва.
RESERVED_BYTES: Final[int] = 10 - (FCS_BYTES - 1)
# Вариант 1 ЛР2: максимальная длина ВСЕГО кадра — 100 байт (не поля данных).
MAX_FRAME_SIZE: Final[int] = 100
# Поле данных = что остаётся в лимите 100: 100−8−2−2−9 = 79 байт.
MAX_DATA_SIZE: Final[int] = (
    MAX_FRAME_SIZE - len(START_FLAG) - HEADER_BYTES - FCS_BYTES - RESERVED_BYTES
)
BODY_SIZE: Final[int] = MAX_FRAME_SIZE - len(START_FLAG)

# Вариант 1 ЛР3: вероятность эмуляции ошибки 10 %.
ERROR_PROBABILITY: Final[float] = 0.10


def _bits_of_byte(value: int) -> Iterable[int]:
    """Yield one byte from the most significant bit to the least."""
    for shift in range(7, -1, -1):
        yield (value >> shift) & 1


def _flag_bits() -> tuple[int, ...]:
    return tuple(bit for byte in START_FLAG for bit in _bits_of_byte(byte))


FLAG_BITS: Final[tuple[int, ...]] = _flag_bits()
FLAG_LENGTH: Final[int] = len(FLAG_BITS)
STUFF_BIT: Final[int] = 1 - FLAG_BITS[-1]


def _build_failure_table(pattern: tuple[int, ...]) -> tuple[int, ...]:
    """Return the KMP fallback state for every prefix of ``pattern``."""
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
    """KMP state machine used identically while stuffing and de-stuffing."""

    __slots__ = ("state",)

    def __init__(self) -> None:
        self.state = 0

    def consume(self, bit: int) -> bool:
        """Consume a bit and report whether 63 flag bits now match."""
        while self.state and bit != FLAG_BITS[self.state]:
            self.state = FLAG_FAILURE[self.state - 1]
        if bit == FLAG_BITS[self.state]:
            self.state += 1
        return self.state == FLAG_LENGTH - 1


class _BitWriter:
    """Append individual MSB-first bits to a compact byte buffer."""

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
    """Двоичное поле; изменённые биты подчёркнуты (эмуляция ошибки)."""
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
    """One fixed-size frame before bit stuffing."""

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
        """Поля тела кадра в порядке передачи (включая резерв)."""
        return (
            bytes((self.frame_type,)),
            bytes((self.frame_number,)),
            self.padded_data(),
            self.fcs,
            self.reserved,
        )


@dataclass(frozen=True)
class FrameTransmission:
    """Bytes written to the port and values displayed in the status area."""

    wire: bytes
    before: str
    after_html: str
    after_error_html: str
    error_emulated: bool = False


def _stuff_body(fields: tuple[bytes, ...]) -> tuple[bytes, list[str]]:
    """Serialize body fields and interrupt every 63-bit flag prefix."""
    tracker = _FlagTracker()
    writer = _BitWriter()
    displayed: list[str] = []

    for field in fields:
        # Каждый исходный байт остаётся отдельным значением в представлении:
        # это позволяет отделять значения запятыми и при этом подчёркивать
        # вставленный бит ровно в том месте, где он появился.
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
    """Строка статуса без резервных байтов (как в лабораторной №2)."""
    # fields: type, number, data, fcs, reserved — резерв не показываем.
    if displayed is None:
        visible = fields[:-1]
        return " ".join((_format_field(flag), *map(_format_field, visible)))
    return " ".join((_format_field(flag), *displayed[:-1]))


def _corrupt_data_field(data: bytes) -> bytes:
    """Инвертировать один случайный бит в одном случайном байте поля данных."""
    if not data:
        return data
    mutable = bytearray(data)
    byte_index = random.randrange(len(mutable))
    bit_index = random.randrange(8)
    mutable[byte_index] ^= 1 << bit_index
    return bytes(mutable)


def encode_frame(
    text: str | bytes,
    *,
    emulate_errors: bool = False,
) -> FrameTransmission:
    """Собрать кадр с FCS, при необходимости исказить данные, затем сделать stuffing.

    Порядок по заданию ЛР3:
    1) заполнить FCS по полю данных;
    2) бит-стаффинг применять к кадру с уже заполненным FCS;
    3) эмуляция ошибки — один бит одного байта поля данных перед передачей.
    """
    data = text if isinstance(text, bytes) else text.encode("utf-8")
    padded = data.ljust(MAX_DATA_SIZE, b"\x00") if len(data) < MAX_DATA_SIZE else data
    if len(padded) > MAX_DATA_SIZE:
        raise ValueError("Data field does not fit into a frame.")

    # FCS считается только по полю данных (после padding до MAX_DATA_SIZE).
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
    # Строка «после stuffing» всегда показывает кадр с корректным FCS
    # (стаффинг применяется к кадру с уже заполненным полем контроля).
    _clean_stuffed, clean_displayed = _stuff_body(clean_fields)
    after = _visible_status(START_FLAG, clean_fields, clean_displayed)

    error_emulated = False
    send_fields = clean_fields
    if emulate_errors and random.random() < ERROR_PROBABILITY:
        corrupted_data = _corrupt_data_field(padded)
        # FCS не пересчитывается: порча уже после канального кодирования.
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
        # Без эмуляции третья строка совпадает с содержимым до stuffing.
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
    """Accumulate serial bytes and yield only complete valid frames."""

    def __init__(self) -> None:
        self._source = bytearray()

    def feed(self, received: bytes) -> list[Frame]:
        self._source.extend(received)
        ready: list[Frame] = []

        while True:
            flag_offset = self._source.find(START_FLAG)
            if flag_offset < 0:
                self._keep_possible_flag_prefix()
                return ready
            if flag_offset:
                del self._source[:flag_offset]

            decoded = self._decode_one(memoryview(self._source)[len(START_FLAG) :])
            if decoded is None:
                return ready
            frame, wire_bytes = decoded
            if frame is None:
                # Discard one byte and scan again: a real flag may start in
                # the remainder of a malformed candidate.
                del self._source[0]
                continue
            ready.append(frame)
            del self._source[: len(START_FLAG) + wire_bytes]

    def _keep_possible_flag_prefix(self) -> None:
        """Retain only bytes that could still start a split start flag."""
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
            # Кадр из одних нулей после padding не несёт пользовательских данных.
            return None, 0

        try:
            payload.decode("utf-8")
            # FCS в объекте Frame — исходный принятый; данные уже исправлены.
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
