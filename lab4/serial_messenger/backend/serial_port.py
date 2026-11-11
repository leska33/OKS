from __future__ import annotations

import sys
import time
from collections.abc import Generator
from dataclasses import replace
from types import TracebackType
from typing import Final, Self, override

from PyQt6 import QtCore
from serial import Serial, SerialException, SerialTimeoutException
from serial.tools import list_ports

from .csma_cd import (
    backoff_delay_ms,
    is_channel_busy,
    listen_until_idle,
    transmit_frame_csma_cd,
    wait_ms,
)
from .frame import MAX_DATA_SIZE, FrameDecoder, FrameTransmission, encode_frame

__all__ = ("BAUD_RATES", "SerialConnection")

BYTE_SIZE: Final[int] = 8
PARITY: Final[str] = "N"
STOP_BITS: Final[int] = 1

TIMEOUT_SECONDS: Final[float] = 0.05

WRITE_TIMEOUT_SECONDS: Final[float] = 2.0

RECEIVE_POLL_INTERVAL_MS: Final[int] = 20

OPEN_RETRY_COUNT: Final[int] = 6
OPEN_RETRY_DELAY_SECONDS: Final[float] = 0.12

def _is_port_busy_error(error: BaseException) -> bool:
    text = str(error).lower()
    return any(
        marker in text
        for marker in (
            "permission",
            "access",
            "отказано",
            "занят",
            "busy",
            "in use",
        )
    )

BAUD_RATES: list[int] = [
    110,
    300,
    600,
    1200,
    2400,
    4800,
    9600,
    19200,
    38400,
    57600,
    115200,
]

class ReceiverThread(QtCore.QThread):

    data_received = QtCore.pyqtSignal(object)
    error_occurred = QtCore.pyqtSignal(str)

    def __init__(self, port: Serial, owner: SerialConnection) -> None:
        super().__init__()

        self.port = port
        self._owner = owner
        self._active = True

    @override
    def run(self) -> None:
        decoder = FrameDecoder()

        while self._active:
            try:
                if not self.port.is_open:
                    return

                                                                
                                                                     
                if self._owner.emulate_multiple_access and is_channel_busy():
                    listen_until_idle()
                    if not self._active:
                        return

                waiting = self.port.in_waiting

                if waiting:
                    raw_data = self.port.read(waiting)
                    jam_before = decoder.jam_events
                    frames = decoder.feed(raw_data)
                    jam_count = decoder.jam_events - jam_before

                                                                                     
                    if jam_count and self._owner.emulate_multiple_access:
                        for attempt in range(1, jam_count + 1):
                            wait_ms(backoff_delay_ms(attempt))
                            if not self._active:
                                return

                    for frame in frames:
                        self.data_received.emit(frame)

                else:
                    self.msleep(RECEIVE_POLL_INTERVAL_MS)

            except (OSError, SerialException) as error:
                if self._active:
                    self.error_occurred.emit(f"Receive error: {error}")

                return

            except Exception as error:
                if self._active:
                    self.error_occurred.emit(f"Unexpected receive error: {error}")

                return

    def stop(self) -> None:
        self._active = False
        self.wait(1000)

class SerialConnection(QtCore.QObject):

    received = QtCore.pyqtSignal(str)
    frame_received = QtCore.pyqtSignal(object)
    frame_sent = QtCore.pyqtSignal(object)
    error = QtCore.pyqtSignal(str)
    warning = QtCore.pyqtSignal(str)

    def __init__(
        self,
        port_name: str,
        baud_rate: int,
    ) -> None:
        super().__init__()

        self.port_name = port_name
        self.baud_rate = baud_rate

        self.port: Serial | None = None
        self.receiver: ReceiverThread | None = None
        self._tx_buffer = bytearray()
        self._tx_characters = 0
                                                                                   
        self.emulate_errors = False
                                                                                 
        self.emulate_multiple_access = False
        self._sending = False

    @staticmethod
    def available_ports() -> Generator[str, None, None]:
        found: set[str] = {item.device for item in list_ports.comports()}

        if sys.platform == "win32":
            try:
                import winreg

                with winreg.OpenKey(
                    winreg.HKEY_LOCAL_MACHINE,
                    r"HARDWARE\DEVICEMAP\SERIALCOMM",
                ) as key:
                    index = 0
                    while True:
                        try:
                            _name, value, _typ = winreg.EnumValue(key, index)
                        except OSError:
                            break
                        if isinstance(value, str) and value.strip():
                            found.add(value.strip())
                        index += 1
            except OSError:
                pass

        def sort_key(name: str) -> tuple[int, str]:
            digits = "".join(character for character in name if character.isdigit())
            return (int(digits) if digits else 0, name)

        yield from sorted(found, key=sort_key)

    def __enter__(self) -> Self:
        port_name = self.port_name
        if sys.platform == "win32" and port_name.upper().startswith("COM"):
            port_name = "\\\\.\\" + port_name

        last_error: BaseException | None = None
        for attempt in range(OPEN_RETRY_COUNT):
            try:
                write_timeout = max(
                    WRITE_TIMEOUT_SECONDS,
                    (250 * 11) / max(int(self.baud_rate), 1) + 1.0,
                )
                self.port = Serial(
                    port=port_name,
                    baudrate=int(self.baud_rate),
                    bytesize=BYTE_SIZE,
                    parity=PARITY,
                    stopbits=STOP_BITS,
                    timeout=TIMEOUT_SECONDS,
                    write_timeout=write_timeout,
                    xonxoff=False,
                    rtscts=False,
                    dsrdtr=False,
                )
                last_error = None
                break
            except (
                SerialException,
                OSError,
                ValueError,
                TypeError,
            ) as error:
                self.port = None
                last_error = error
                if attempt + 1 < OPEN_RETRY_COUNT and _is_port_busy_error(error):
                    time.sleep(OPEN_RETRY_DELAY_SECONDS)
                    continue
                break

        if self.port is None:
            detail = last_error if last_error is not None else "unknown error"
            hint = ""
            if last_error is not None and _is_port_busy_error(last_error):
                hint = (
                    "\n\nПорт занят другим окном программы. "
                    "Закройте лишние экземпляры и выберите разные порты "
                    "пары (например COM3 и COM4)."
                )
            raise ConnectionError(
                f"Could not open {self.port_name}: {detail}{hint}"
            ) from last_error

        self.receiver = ReceiverThread(self.port, self)

        self.receiver.data_received.connect(self._handle_frame)

        self.receiver.error_occurred.connect(self._handle_receiver_error)

        self.receiver.start()

        return self

    def _handle_receiver_error(
        self,
        message: str,
    ) -> None:
        self.error.emit(message)

    def _handle_frame(self, frame: object) -> None:
        data = getattr(frame, "data", None)
        if not isinstance(data, bytes):
            return
        self.received.emit(data.decode("utf-8"))
        self.frame_received.emit(frame)

    def send_character(
        self,
        character: str,
    ) -> FrameTransmission | None:
        if self.port is None or not self.port.is_open:
            self.error.emit("COM port is not open.")
            return None

        if self._sending:
            return None

        try:
            data = character.encode("utf-8")
            if len(data) > MAX_DATA_SIZE:
                raise ValueError("Character does not fit into the data field.")

            if len(self._tx_buffer) + len(data) > MAX_DATA_SIZE:
                transmission = self._send_full_packet()
                if transmission is None:
                    return None

            self._tx_buffer.extend(data)
            self._tx_characters += 1
            if len(self._tx_buffer) == MAX_DATA_SIZE:
                return self._send_full_packet()
            return None

        except SerialTimeoutException:
            self.warning.emit(
                "Send error: write timeout.\n\n"
                "Парный COM-порт не принимает данные. Откройте второе окно "
                "программы на другом порту пары (COM3 ↔ COM4) с той же "
                "скоростью и повторите ввод."
            )
            return None

        except (
            OSError,
            SerialException,
            UnicodeEncodeError,
            ValueError,
        ) as error:
            self.error.emit(f"Send error: {error}")

            return None

    def set_emulate_errors(self, enabled: bool) -> None:
                                                                            
        self.emulate_errors = enabled

    def set_emulate_multiple_access(self, enabled: bool) -> None:
                                                                            
        self.emulate_multiple_access = enabled

    def _send_full_packet(self) -> FrameTransmission | None:
        if self.port is None or not self._tx_buffer:
            return None
        if self._sending:
            return None
        self._sending = True
        try:
            return self._send_full_packet_unlocked()
        finally:
            self._sending = False

    def _send_full_packet_unlocked(self) -> FrameTransmission | None:
        if self.port is None or not self._tx_buffer:
            return None
        transmission = encode_frame(
            bytes(self._tx_buffer),
            emulate_errors=self.emulate_errors,
        )
        if self.emulate_multiple_access:
                                                                                      
            port = self.port

            def _write(chunk: bytes) -> int:
                return int(port.write(chunk))

            result = transmit_frame_csma_cd(_write, transmission.wire)
            transmission = replace(
                transmission,
                collisions_display=result.collisions_display,
            )
            if not result.success:
                self.warning.emit(
                    "CSMA/CD: кадр не передан после повторных коллизий "
                    f"(попыток: {result.attempts})."
                )
                                                                              
                self.frame_sent.emit(transmission)
                self._tx_buffer.clear()
                self._tx_characters = 0
                return transmission
        else:
            written = self.port.write(transmission.wire)
            if written != len(transmission.wire):
                self.error.emit("Send error: incomplete write.")
                return None
        self.frame_sent.emit(transmission)
        self._tx_buffer.clear()
        self._tx_characters = 0
        return transmission

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        if self.receiver is not None:
            self.receiver.stop()
            self.receiver = None

        if self.port is not None and self.port.is_open:
            try:
                self.port.close()

            except (OSError, SerialException):
                pass

        self.port = None
        time.sleep(OPEN_RETRY_DELAY_SECONDS)
        self._tx_buffer.clear()
        self._tx_characters = 0
