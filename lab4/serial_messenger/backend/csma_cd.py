\
\
\
\
\
\
\
\
\
\
\
\
\
\
\
\
\
\
   

from __future__ import annotations

import random
import time
from dataclasses import dataclass, field
from typing import Callable, Final

from PyQt6 import QtCore, QtWidgets

CHANNEL_BUSY_PROBABILITY: Final[float] = 0.10
                                            
COLLISION_PROBABILITY: Final[float] = 0.40

                                               
JAM_BYTE: Final[int] = 0xEE
JAM_LENGTH: Final[int] = 4
JAM_SIGNAL: Final[bytes] = bytes([JAM_BYTE] * JAM_LENGTH)

SLOT_TIME_MS: Final[float] = 10.0
MAX_ATTEMPTS: Final[int] = 16
BACKOFF_CAP: Final[int] = 10

def is_channel_busy() -> bool:
    return random.random() < CHANNEL_BUSY_PROBABILITY

def backoff_delay_ms(attempt: int) -> float:
    if attempt < 1:
        attempt = 1
    k = min(attempt, BACKOFF_CAP)
    slots = random.randint(0, (1 << k) - 1)
    return slots * SLOT_TIME_MS

def wait_ms(milliseconds: float) -> None:
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
    while is_channel_busy():
        wait_ms(SLOT_TIME_MS)

@dataclass
class CsmaCdResult:
    success: bool
                                                        
    collision_marks: list[str] = field(default_factory=list)
    attempts: int = 0

    @property
    def collisions_display(self) -> str:
        return " ".join(self.collision_marks)

WriteCallback = Callable[[bytes], int]

def transmit_frame_csma_cd(write: WriteCallback, frame_bytes: bytes) -> CsmaCdResult:
                                                                                
    marks: list[str] = []
    attempt = 0

    while attempt < MAX_ATTEMPTS:
                                  
        listen_until_idle()

                                                                               
                                                                                   
        collision_byte_index: int | None = None
        if frame_bytes and random.random() < COLLISION_PROBABILITY:
            collision_byte_index = random.randrange(len(frame_bytes))

        collided = False

                                                                              
        for byte_index, value in enumerate(frame_bytes):
            written = write(bytes((value,)))
            if written != 1:
                return CsmaCdResult(success=False, collision_marks=marks, attempts=attempt)

                                                           
            if collision_byte_index is not None and byte_index == collision_byte_index:
                marks.append("*")
                                                  
                jam_written = write(JAM_SIGNAL)
                if jam_written != len(JAM_SIGNAL):
                    return CsmaCdResult(
                        success=False,
                        collision_marks=marks,
                        attempts=attempt + 1,
                    )
                attempt += 1
                                                                           
                wait_ms(backoff_delay_ms(attempt))
                collided = True
                break

        if not collided:
            return CsmaCdResult(success=True, collision_marks=marks, attempts=attempt)

    return CsmaCdResult(success=False, collision_marks=marks, attempts=attempt)

def contains_jam(buffer: bytes | bytearray | memoryview) -> int:
    return bytes(buffer).find(JAM_SIGNAL)
