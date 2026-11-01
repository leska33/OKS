"""Циклический код с исправлением одиночных ошибок (лабораторная работа №3).

Расчёт длины поля FCS (обязательный комментарий к параметрам кодирования):
------------------------------------------------------------
ЛР2, вариант 1: максимальная длина ВСЕГО кадра = 100 байт.
Состав кадра после ЛР3:
    флаг 8 + Type 1 + Number 1 + данные + FCS 2 + резерв 9 = 100,
    следовательно максимум поля данных k_bytes = 79 байт = 632 бит.
Код должен обнаруживать и исправлять одиночные ошибки, поэтому число
проверочных бит r подчиняется границе Хэмминга для SEC:
    2^r >= k + r + 1,  где k = 632.
Проверка:
    r = 9  →  2^9  =  512  >= 632 + 9 + 1 = 642  — нет;
    r = 10 →  2^10 = 1024  >= 632 + 10 + 1 = 643 — да.
Итого FCS_BITS = 10. Через COM-порт передаются только целые байты, поэтому
    FCS_BYTES = ceil(10 / 8) = 2.
Используется уже предусмотренное в кадре контрольное поле (Checksum) и
один байт из резерва: итого FCS занимает 2 байта (имя в UI — Checksum).
Порождающий многочлен (примитивный, степень 10):
    g(x) = x^10 + x^3 + 1.
Алгоритм деления «как на бумаге»: последовательное XOR при совпадении
старшего бита делимого со старшим битом делителя.
------------------------------------------------------------
"""

from __future__ import annotations

from typing import Final


# --- параметры кодирования (см. расчёт в docstring модуля) ---
FCS_BITS: Final[int] = 10
FCS_BYTES: Final[int] = (FCS_BITS + 7) // 8  # 2

# g(x) = x^10 + x^3 + 1  →  биты от старшей степени к младшей.
GENERATOR_BITS: Final[tuple[int, ...]] = (1, 0, 0, 0, 0, 0, 0, 1, 0, 0, 1)
GENERATOR_DEGREE: Final[int] = FCS_BITS


def bits_from_bytes(data: bytes) -> list[int]:
    """Развернуть байты в список бит (старший бит байта первым)."""
    bits: list[int] = []
    for value in data:
        for shift in range(7, -1, -1):
            bits.append((value >> shift) & 1)
    return bits


def bytes_from_bits(bits: list[int]) -> bytes:
    """Собрать байты из списка бит (дополняя нулями справа до кратности 8)."""
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
    """Остаток от деления многочленов над GF(2) — деление столбиком.

    ``dividend`` и ``divisor`` — коэффициенты от старшей степени к младшей.
    Возвращается ровно ``len(divisor) - 1`` бит остатка.
    """
    if len(divisor) < 2:
        raise ValueError("Generator polynomial must have degree >= 1.")

    work = list(dividend)
    degree = len(divisor) - 1

    # Ведущие нули делимого не мешают: цикл идёт по всем позициям,
    # откуда ещё можно «накрыть» делитель.
    for position in range(len(work) - degree):
        if work[position] == 0:
            continue
        for offset, coeff in enumerate(divisor):
            work[position + offset] ^= coeff

    return work[-degree:]


def _fcs_bits_from_field(fcs_field: bytes) -> list[int]:
    """Извлечь значимые FCS_BITS из двухбайтового поля (старшие биты)."""
    if len(fcs_field) != FCS_BYTES:
        raise ValueError("FCS field must be exactly FCS_BYTES long.")
    all_bits = bits_from_bytes(fcs_field)
    return all_bits[:FCS_BITS]


def _field_from_fcs_bits(remainder: list[int]) -> bytes:
    """Упаковать остаток длины FCS_BITS в FCS_BYTES байт (старшие биты)."""
    if len(remainder) != FCS_BITS:
        raise ValueError("Remainder length must equal FCS_BITS.")
    # Младшие биты двухбайтового поля дополняются нулями.
    padded = list(remainder) + [0] * (FCS_BYTES * 8 - FCS_BITS)
    return bytes_from_bits(padded)


def compute_fcs(data: bytes) -> bytes:
    """Систематическое кодирование: FCS = (data · x^r) mod g(x)."""
    data_bits = bits_from_bytes(data)
    # «Приписываем» r нулевых бит и делим на g(x) — как на бумаге.
    dividend = data_bits + [0] * FCS_BITS
    remainder = poly_mod(dividend, GENERATOR_BITS)
    return _field_from_fcs_bits(remainder)


def _syndrome(data_bits: list[int], fcs_bits: list[int]) -> list[int]:
    """Синдром кодового слова data || fcs."""
    return poly_mod(data_bits + fcs_bits, GENERATOR_BITS)


def _precompute_error_syndromes(codeword_length: int) -> dict[tuple[int, ...], int]:
    """Синдром каждой одиночной ошибки → позиция бита в кодовом слове."""
    mapping: dict[tuple[int, ...], int] = {}
    for position in range(codeword_length):
        error = [0] * codeword_length
        error[position] = 1
        syn = tuple(poly_mod(error, GENERATOR_BITS))
        # Для примитивного g(x) все ненулевые синдромы одиночных ошибок различны.
        mapping[syn] = position
    return mapping


# Длина кодового слова при максимальном (и единственном рабочем) поле данных.
# Фактическая длина данных в кадре всегда MAX_DATA_SIZE после padding.
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
    """Проверить и при необходимости исправить одиночную ошибку в data||FCS.

    Возвращает ``(data, corrected, ok)``:
    - ``corrected`` — была ли инвертирована ровно одна позиция;
    - ``ok`` — кадр принят (синдром нулевой либо ошибка исправлена).
    """
    data_bits = bits_from_bytes(data)
    fcs_bits = _fcs_bits_from_field(fcs_field)
    syn = _syndrome(data_bits, fcs_bits)
    if not any(syn):
        return data, False, True

    table = _table_for(len(data_bits))
    position = table.get(tuple(syn))
    if position is None:
        # Двойная/множественная ошибка — циклический код её не исправляет.
        return data, False, False

    codeword = data_bits + fcs_bits
    codeword[position] ^= 1
    corrected_data = bytes_from_bits(codeword[: len(data_bits)])
    # Обрезаем возможное дополнение bytes_from_bits до байтовой границы:
    # число бит данных всегда кратно 8 (поле данных — целые байты).
    corrected_data = corrected_data[: len(data)]
    return corrected_data, True, True
