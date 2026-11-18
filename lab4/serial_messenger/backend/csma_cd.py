"""Упрощённый CSMA/CD для ЛР4, вариант 1.

Параметры варианта 1 (методичка):
- вероятность занятости канала: 10 %;
- вероятность коллизии: 40 %;
- коллизия в отношении отдельного байта (не кадра целиком);
- довесок к алгоритму: Jam-сигнал.

Выбранные параметры алгоритма (обязательные пояснения):
- SLOT_TIME_MS = 10 мс - укороченный слот (на COM задержка заметна, без зависания UI);
- случайная задержка по стандартной формуле BEB:
  k = min(n, 10), r из 0..2^k-1, delay = r * SLOT_TIME_MS;
- MAX_ATTEMPTS = 16 - прерывание после 16 коллизий (как abort в CSMA/CD);
- JAM_SIGNAL = 0xEE x 4 - значение Jam выбрано самостоятельно.

Три шага строго по порядку, без распараллеливания:
1) прослушивание канала;
2) обнаружение коллизии;
3) розыгрыш случайной задержки.
"""

from __future__ import annotations

import random
import time
from dataclasses import dataclass, field
from typing import Callable, Final

from PyQt6 import QtCore, QtWidgets

# Вариант 1: 10 % занятость канала, 40 % коллизия.
CHANNEL_BUSY_PROBABILITY: Final[float] = 0.10
COLLISION_PROBABILITY: Final[float] = 0.40

# Jam-сигнал (значение по своему усмотрению).
JAM_BYTE: Final[int] = 0xEE
JAM_LENGTH: Final[int] = 4
JAM_SIGNAL: Final[bytes] = bytes([JAM_BYTE] * JAM_LENGTH)

SLOT_TIME_MS: Final[float] = 10.0
MAX_ATTEMPTS: Final[int] = 16
BACKOFF_CAP: Final[int] = 10


def is_channel_busy() -> bool:
    """Шаг 1: канал занят с вероятностью 10 %."""
    return random.random() < CHANNEL_BUSY_PROBABILITY


def backoff_delay_ms(collision_number: int) -> float:
    """Шаг 3: стандартная формула binary exponential backoff."""
    n = max(1, collision_number)
    k = min(n, BACKOFF_CAP)
    r = random.randint(0, (1 << k) - 1)
    return r * SLOT_TIME_MS


def wait_ms(milliseconds: float) -> None:
    """Ждать delay мс последовательно (без распараллеливания алгоритма)."""
    if milliseconds <= 0:
        return
    deadline = time.perf_counter() + milliseconds / 1000.0
    app = QtWidgets.QApplication.instance()
    flags = QtCore.QEventLoop.ProcessEventsFlag.ExcludeUserInputEvents
    while time.perf_counter() < deadline:
        remaining_ms = max(0.0, (deadline - time.perf_counter()) * 1000.0)
        slice_ms = min(10.0, remaining_ms)
        if app is not None:
            app.processEvents(flags, int(max(1.0, slice_ms)))
            time.sleep(min(0.005, slice_ms / 1000.0))
        else:
            time.sleep(slice_ms / 1000.0)


def listen_until_idle() -> None:
    """Шаг 1: слушать канал, пока не свободен."""
    while is_channel_busy():
        wait_ms(SLOT_TIME_MS)


@dataclass
class CsmaCdResult:
    success: bool
    # По одному '*' на каждую коллизию последнего кадра (через пробел в UI).
    collision_marks: list[str] = field(default_factory=list)
    attempts: int = 0

    @property
    def collisions_display(self) -> str:
        return " ".join(self.collision_marks)


WriteCallback = Callable[[bytes], int]


def transmit_frame_csma_cd(write: WriteCallback, frame_bytes: bytes) -> CsmaCdResult:
    """CSMA/CD на передатчике (вариант 1: коллизия по отдельному байту).

    Алгоритм (рассказывать по шагам):
    ----------------------------------------------------------
    attempt = 0
    пока кадр не ушёл и attempt < 16:
      1) ПРОСЛУШИВАНИЕ: пока канал занят (10 %) - ждать слот
      2) ОБНАРУЖЕНИЕ КОЛЛИЗИИ (по байту):
         - с вероятностью 40 % выбирается ОДИН байт кадра - место коллизии
           (не весь кадр сразу: так отличаются варианты 3-4);
         - байты до него уходят в линию;
         - на этом байте: коллизия -> Jam-сигнал -> '*'
      3) РОЗЫГРЫШ ЗАДЕРЖКИ: BEB, затем повтор всего кадра с шага 1
      если все байты ушли без коллизии - успех
    ----------------------------------------------------------
    """
    marks: list[str] = []
    attempt = 0

    while attempt < MAX_ATTEMPTS:
        # --- Шаг 1. Прослушивание канала ---
        listen_until_idle()

        # Вариант 1: коллизия в отношении отдельного байта.
        # Вероятность коллизии 40 %: если выпала - фиксируем индекс байта.
        collision_at: int | None = None
        if frame_bytes and random.random() < COLLISION_PROBABILITY:
            collision_at = random.randrange(len(frame_bytes))

        collided = False

        # Передача побайтно (не «кадр целиком»).
        for byte_index, value in enumerate(frame_bytes):
            written = write(bytes((value,)))
            if written != 1:
                return CsmaCdResult(success=False, collision_marks=marks, attempts=attempt)

            # --- Шаг 2. Обнаружение коллизии на этом байте ---
            if collision_at is not None and byte_index == collision_at:
                marks.append("*")
                # Довесок варианта 1: Jam-сигнал.
                jam_written = write(JAM_SIGNAL)
                if jam_written != len(JAM_SIGNAL):
                    return CsmaCdResult(
                        success=False,
                        collision_marks=marks,
                        attempts=attempt + 1,
                    )
                attempt += 1
                # --- Шаг 3. Розыгрыш случайной задержки ---
                wait_ms(backoff_delay_ms(attempt))
                collided = True
                break

        if not collided:
            return CsmaCdResult(success=True, collision_marks=marks, attempts=attempt)

    return CsmaCdResult(success=False, collision_marks=marks, attempts=attempt)


def contains_jam(buffer: bytes | bytearray | memoryview) -> int:
    """Индекс Jam-сигнала или -1 (для обнаружения коллизии на приёме)."""
    return bytes(buffer).find(JAM_SIGNAL)
