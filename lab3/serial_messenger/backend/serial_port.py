"""COM-port access and framed background receiving for laboratory work No. 2."""

from __future__ import annotations

import sys
import time
from collections.abc import Generator
from types import TracebackType
from typing import Final, Self, override

from PyQt6 import QtCore
from serial import Serial, SerialException, SerialTimeoutException
from serial.tools import list_ports

from .frame import MAX_DATA_SIZE, FrameDecoder, FrameTransmission, encode_frame

__all__ = ("BAUD_RATES", "SerialConnection")


BYTE_SIZE: Final[int] = 8
PARITY: Final[str] = "N"
STOP_BITS: Final[int] = 1

TIMEOUT_SECONDS: Final[float] = 0.05

# Нижняя граница write-timeout. Фактическое значение считается от скорости:
# иначе на 110 бод или при незакрытой паре com0com запись срывается слишком рано.
WRITE_TIMEOUT_SECONDS: Final[float] = 2.0

RECEIVE_POLL_INTERVAL_MS: Final[int] = 20

# Windows иногда отдаёт порт не сразу после close() — особенно com0com.
OPEN_RETRY_COUNT: Final[int] = 6
OPEN_RETRY_DELAY_SECONDS: Final[float] = 0.12


def _is_port_busy_error(error: BaseException) -> bool:
    """Permission/access-denied while the previous handle is still releasing."""
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
    """Read a connected serial port in a background Qt thread."""

    data_received = QtCore.pyqtSignal(object)
    error_occurred = QtCore.pyqtSignal(str)

    def __init__(self, port: Serial) -> None:
        """Create a receiver for an already-open serial port."""
        super().__init__()

        self.port = port
        self._active = True

    @override
    def run(self) -> None:
        """Read available bytes until the thread is stopped."""
        # Один декодер обслуживает весь срок жизни потока: его внутренний
        # буфер хранит кадр, если COM-порт отдал его не целиком.
        decoder = FrameDecoder()

        while self._active:
            try:
                if not self.port.is_open:
                    return

                waiting = self.port.in_waiting

                if waiting:
                    # read() возвращает только доступные сейчас байты. Граница
                    # этого чтения не обязана совпадать с границей кадра.
                    raw_data = self.port.read(waiting)

                    for frame in decoder.feed(raw_data):
                        # В интерфейс передаётся только полностью собранный и
                        # проверенный кадр, а не необработанный поток байтов.
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
        """Request thread termination."""
        self._active = False
        self.wait(1000)


class SerialConnection(QtCore.QObject):
    """Manage one selected serial port."""

    received = QtCore.pyqtSignal(str)
    frame_received = QtCore.pyqtSignal(object)
    # Отдельный сигнал нужен окну-отправителю: он возникает только после
    # успешной физической записи сформированного кадра в COM-порт.
    frame_sent = QtCore.pyqtSignal(object)
    error = QtCore.pyqtSignal(str)
    # Не фатальные проблемы отправки (например, парный порт ещё не открыт).
    warning = QtCore.pyqtSignal(str)

    def __init__(
        self,
        port_name: str,
        baud_rate: int,
    ) -> None:
        """Store the selected COM port and baud rate."""
        super().__init__()

        self.port_name = port_name
        self.baud_rate = baud_rate

        self.port: Serial | None = None
        self.receiver: ReceiverThread | None = None
        # Поле данных фиксированной длины (остаток в кадре ≤ 100 байт).
        # Символы копятся и уходят на линию только когда поле заполнено.
        self._tx_buffer = bytearray()
        self._tx_characters = 0
        # ЛР3: эмуляция одиночной ошибки в поле данных (вероятность — по варианту).
        self.emulate_errors = False


    @staticmethod
    def available_ports() -> Generator[str, None, None]:
        """Yield system COM-port names.

        pyserial иногда не видит порты com0com через SetupAPI, хотя они есть
        в ``HKLM\\HARDWARE\\DEVICEMAP\\SERIALCOMM`` и успешно открываются.
        """
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
        """Open the selected port and start receiving."""
        port_name = self.port_name
        # На Windows для COM10+ нужен префикс \\.\ ; для COM3/COM4 он тоже безопасен.
        if sys.platform == "win32" and port_name.upper().startswith("COM"):
            port_name = "\\\\.\\" + port_name

        last_error: BaseException | None = None
        for attempt in range(OPEN_RETRY_COUNT):
            try:
                # Кадр ~100+ байт; на низкой скорости нужно больше 0.25 с.
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

        self.receiver = ReceiverThread(self.port)

        # Приёмник работает в фоне; Qt безопасно передаст сигнал в поток GUI.
        self.receiver.data_received.connect(self._handle_frame)

        self.receiver.error_occurred.connect(self._handle_receiver_error)

        self.receiver.start()

        return self

    def _handle_receiver_error(
        self,
        message: str,
    ) -> None:
        """Forward a real serial-port failure."""
        self.error.emit(message)

    def _handle_frame(self, frame: object) -> None:
        """Forward a recognized frame and its decoded data to the UI."""
        # ReceiverThread only emits Frame instances; keeping the conversion
        # here makes the Qt boundary explicit and protects the UI from bytes.
        data = getattr(frame, "data", None)
        if not isinstance(data, bytes):
            return
        # frame.data уже не содержит padding-нули и stuffing-биты.
        self.received.emit(data.decode("utf-8"))
        self.frame_received.emit(frame)

    def send_character(
        self,
        character: str,
    ) -> FrameTransmission | None:
        """Add one character to the fixed packet currently being assembled."""
        if self.port is None or not self.port.is_open:
            self.error.emit("COM port is not open.")
            return None

        try:
            data = character.encode("utf-8")
            if len(data) > MAX_DATA_SIZE:
                raise ValueError("Character does not fit into the data field.")

            if len(self._tx_buffer) + len(data) > MAX_DATA_SIZE:
                # Многобайтный UTF-8-символ может не поместиться в остаток
                # поля. Сначала отправляем накопленный кадр, затем начнём новый.
                transmission = self._send_full_packet()
                if transmission is None:
                    return None

            self._tx_buffer.extend(data)
            self._tx_characters += 1
            if len(self._tx_buffer) == MAX_DATA_SIZE:
                # Поле данных заполнено → кадр в пределах лимита 100 байт (до stuffing).
                return self._send_full_packet()
            return None

        except SerialTimeoutException:
            # Типичный случай com0com: второй конец пары ещё не открыт.
            # Соединение не рвём — достаточно открыть парный порт во 2-м окне.
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
        """Разрешить или запретить эмуляцию ошибок перед передачей кадра."""
        self.emulate_errors = enabled

    def _send_full_packet(self) -> FrameTransmission | None:
        """Serialize and write the currently full (or overflowed) data field."""
        if self.port is None or not self._tx_buffer:
            return None
        transmission = encode_frame(
            bytes(self._tx_buffer),
            emulate_errors=self.emulate_errors,
        )
        # transmission.wire начинается с флага и содержит уже stuffed-тело
        # (FCS заполнен до stuffing; эмуляция ошибки — в поле данных).
        written = self.port.write(transmission.wire)
        if written != len(transmission.wire):
            self.error.emit("Send error: incomplete write.")
            return None
        # Отправитель показывает свой кадр по этому событию, не ожидая, пока
        # он пройдёт по кабелю и будет декодирован вторым экземпляром программы.
        self.frame_sent.emit(transmission)
        self._tx_buffer.clear()
        # Счётчик нужен только для диагностического сообщения; данные уже
        # отправлены, поэтому начинать следующий пакет нужно с нуля.
        self._tx_characters = 0
        return transmission

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """Stop receiving and close the serial port."""
        if self.receiver is not None:
            self.receiver.stop()
            self.receiver = None

        if self.port is not None and self.port.is_open:
            try:
                self.port.close()

            except (OSError, SerialException):
                pass

        self.port = None
        # Дать Windows/com0com время освободить имя порта перед повторным open.
        time.sleep(OPEN_RETRY_DELAY_SECONDS)
        # Неполный пакет намеренно не отправляется при закрытии: условие
        # исходной реализации требует передачи только заполненного буфера.
        self._tx_buffer.clear()
        self._tx_characters = 0
