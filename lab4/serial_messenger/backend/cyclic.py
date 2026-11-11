from __future__ import annotations

from typing import Final

FCS_BITS: Final[int] = 10
FCS_BYTES: Final[int] = (FCS_BITS + 7) // 8

GENERATOR_BITS: Final[tuple[int, ...]] = (1, 0, 0, 0, 0, 0, 0, 1, 0, 0, 1)
GENERATOR_DEGREE: Final[int] = FCS_BITS

def bits_from_bytes(data: bytes) -> list[int]:
    bits: list[int] = []
    for value in data:
        for shift in range(7, -1, -1):
            bits.append((value >> shift) & 1)
    return bits

def bytes_from_bits(bits: list[int]) -> bytes:
    padded = list(bits)
    while len(padded) % 8:
        padded.append(0)
    out = bytearray()
    for index in range(0, len(padded), 8):
        value = 0
        for bit in padded[index : index + 8]:
            value = (value << 1) | bit
        out.append(value)
    return bytes(out)

def poly_mod(dividend: list[int], divisor: tuple[int, ...] | list[int]) -> list[int]:
    if len(divisor) < 2:
        raise ValueError("Generator polynomial must have degree >= 1.")

    work = list(dividend)
    degree = len(divisor) - 1

    for position in range(len(work) - degree):
        if work[position] == 0:
            continue
        for offset, coeff in enumerate(divisor):
            work[position + offset] ^= coeff

    return work[-degree:]

def _fcs_bits_from_field(fcs_field: bytes) -> list[int]:
    if len(fcs_field) != FCS_BYTES:
        raise ValueError("FCS field must be exactly FCS_BYTES long.")
    all_bits = bits_from_bytes(fcs_field)
    return all_bits[:FCS_BITS]

def _field_from_fcs_bits(remainder: list[int]) -> bytes:
    if len(remainder) != FCS_BITS:
        raise ValueError("Remainder length must equal FCS_BITS.")
    padded = list(remainder) + [0] * (FCS_BYTES * 8 - FCS_BITS)
    return bytes_from_bits(padded)

def compute_fcs(data: bytes) -> bytes:
    data_bits = bits_from_bytes(data)
    dividend = data_bits + [0] * FCS_BITS
    remainder = poly_mod(dividend, GENERATOR_BITS)
    return _field_from_fcs_bits(remainder)

def _syndrome(data_bits: list[int], fcs_bits: list[int]) -> list[int]:
    return poly_mod(data_bits + fcs_bits, GENERATOR_BITS)

def _precompute_error_syndromes(codeword_length: int) -> dict[tuple[int, ...], int]:
    mapping: dict[tuple[int, ...], int] = {}
    for position in range(codeword_length):
        error = [0] * codeword_length
        error[position] = 1
        syn = tuple(poly_mod(error, GENERATOR_BITS))
        mapping[syn] = position
    return mapping

def _build_syndrome_table(data_bit_count: int) -> dict[tuple[int, ...], int]:
    return _precompute_error_syndromes(data_bit_count + FCS_BITS)

_SYNDROME_TABLES: dict[int, dict[tuple[int, ...], int]] = {}

def _table_for(data_bit_count: int) -> dict[tuple[int, ...], int]:
    table = _SYNDROME_TABLES.get(data_bit_count)
    if table is None:
        table = _build_syndrome_table(data_bit_count)
        _SYNDROME_TABLES[data_bit_count] = table
    return table

def correct_data(data: bytes, fcs_field: bytes) -> tuple[bytes, bool, bool]:
    data_bits = bits_from_bytes(data)
    fcs_bits = _fcs_bits_from_field(fcs_field)
    syn = _syndrome(data_bits, fcs_bits)
    if not any(syn):
        return data, False, True

    table = _table_for(len(data_bits))
    position = table.get(tuple(syn))
    if position is None:
        return data, False, False

    codeword = data_bits + fcs_bits
    codeword[position] ^= 1
    corrected_data = bytes_from_bits(codeword[: len(data_bits)])
    corrected_data = corrected_data[: len(data)]
    return corrected_data, True, True
