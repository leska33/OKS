
from __future__ import annotations

from contextlib import ExitStack
from typing import override

from PyQt6 import QtCore, QtGui, QtWidgets

from backend import BAUD_RATES, SerialConnection
from backend.frame import FrameTransmission, encode_frame

__all__ = ("SerialMessenger",)

class ImmediateInput(QtWidgets.QPlainTextEdit):

    character_entered = QtCore.pyqtSignal(str)

    def __init__(self) -> None:
        super().__init__()

        self.setPlaceholderText("Type here — every character is sent immediately")
        self.setAcceptDrops(False)

    def _move_cursor_to_end(self) -> None:
        cursor = self.textCursor()
        cursor.movePosition(QtGui.QTextCursor.MoveOperation.End)
        self.setTextCursor(cursor)

    @override
    def keyPressEvent(
        self,
        event: QtGui.QKeyEvent | None,
    ) -> None:
        if event is None:
            return

        if event.key() in (
            QtCore.Qt.Key.Key_Backspace,
            QtCore.Qt.Key.Key_Delete,
        ) or event.matches(QtGui.QKeySequence.StandardKey.Cut) or event.matches(
            QtGui.QKeySequence.StandardKey.Undo
        ) or event.matches(QtGui.QKeySequence.StandardKey.Redo):
            return

        text = event.text()

        is_line_break = event.key() in (
            QtCore.Qt.Key.Key_Return,
            QtCore.Qt.Key.Key_Enter,
        )

        if (
            text
            or is_line_break
            or event.matches(QtGui.QKeySequence.StandardKey.Paste)
        ):
            self._move_cursor_to_end()

        if is_line_break:
            super().keyPressEvent(event)
            self.character_entered.emit("\n")
            return

        super().keyPressEvent(event)

        if text and text.isprintable():
            self.character_entered.emit(text)

    @override
    def mousePressEvent(
        self,
        event: QtGui.QMouseEvent | None,
    ) -> None:
        if event is None:
            return

        super().mousePressEvent(event)

        self._move_cursor_to_end()

    @override
    def contextMenuEvent(
        self,
        event: QtGui.QContextMenuEvent | None,
    ) -> None:
        if event is not None:
            event.accept()

class SerialMessenger(QtWidgets.QWidget):

    def __init__(self) -> None:
        super().__init__()

        self.connection: SerialConnection | None = None
        self._connection_scope = ExitStack()

        self.sent_characters = 0
        self._handling_error = False

        self.setWindowTitle("COM Port Messenger")
        self.resize(1200, 900)
        self.setMinimumSize(640, 420)

        self._create_widgets()
        self._create_layout()
        self._apply_style()
        self._connect_signals()
        self._load_ports()
        self._refresh_state()

        self.input_area.setFocus()

    def _create_widgets(self) -> None:
        self.port_choice = QtWidgets.QComboBox()

        self.baud_rate_choice = QtWidgets.QComboBox()

        for value in BAUD_RATES:
            self.baud_rate_choice.addItem(
                str(value),
                value,
            )

        self.baud_rate_choice.setCurrentIndex(-1)
        self.baud_rate_choice.setEnabled(False)

        # ЛР3: разрешение/запрет эмуляции ошибок не затрагивает окна Input/Output.
        self.error_emulation_checkbox = QtWidgets.QCheckBox("Enable")
        self.error_emulation_checkbox.setChecked(False)

        # ЛР4: разрешение/запрет эмуляции множественного доступа (CSMA/CD).
        self.multiple_access_checkbox = QtWidgets.QCheckBox("Enable")
        self.multiple_access_checkbox.setChecked(False)

        self.input_area = ImmediateInput()
        self.input_area.setEnabled(False)
        self.input_area.setVerticalScrollBarPolicy(
            QtCore.Qt.ScrollBarPolicy.ScrollBarAsNeeded
        )

        self.output_area = QtWidgets.QPlainTextEdit()
        self.output_area.setReadOnly(True)
        self.output_area.setVerticalScrollBarPolicy(
            QtCore.Qt.ScrollBarPolicy.ScrollBarAsNeeded
        )

        self.field_names_label = QtWidgets.QLabel(
            "StartFlag FrameType FrameNumber Data Checksum"
        )
        self.field_names_label.setObjectName("fieldNamesLabel")
        self.field_names_label.setTextFormat(QtCore.Qt.TextFormat.PlainText)
        self.field_names_label.setWordWrap(True)
        self.status_area = QtWidgets.QTextBrowser()
        self.status_area.setReadOnly(True)
        self.status_area.setLineWrapMode(
            QtWidgets.QTextEdit.LineWrapMode.WidgetWidth
        )
        self.status_area.setHorizontalScrollBarPolicy(
            QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        text_option = self.status_area.document().defaultTextOption()
        text_option.setWrapMode(QtGui.QTextOption.WrapMode.WrapAnywhere)
        self.status_area.document().setDefaultTextOption(text_option)
        self.status_area.setOpenExternalLinks(False)
        self.status_area.setMinimumHeight(145)
        self.status_area.setVerticalScrollBarPolicy(
            QtCore.Qt.ScrollBarPolicy.ScrollBarAsNeeded
        )

        self.sent_characters_label = QtWidgets.QLabel()
        self.sent_characters_label.setObjectName("stateLabel")

        # ЛР4: динамика коллизий последнего кадра — «*», разделённые пробелами.
        self.collisions_label = QtWidgets.QLabel("")
        self.collisions_label.setObjectName("collisionsLabel")
        self.collisions_label.setWordWrap(True)

    def _create_layout(self) -> None:
        layout = QtWidgets.QGridLayout(self)
        layout.setContentsMargins(22, 18, 22, 12)
        layout.setHorizontalSpacing(12)
        layout.setVerticalSpacing(12)

        port_box, port_layout = self._section("COM port")
        port_layout.addWidget(self.port_choice)

        baud_rate_box, baud_rate_layout = self._section("Baud rate")
        baud_rate_layout.addWidget(self.baud_rate_choice)

        emulate_box, emulate_layout = self._section("Error emulation")
        emulate_layout.addWidget(self.error_emulation_checkbox)
        emulate_box.setMinimumWidth(150)

        # ЛР4: отдельный блок управления эмуляцией множественного доступа.
        access_box, access_layout = self._section("Multiple access")
        access_layout.addWidget(self.multiple_access_checkbox)
        access_box.setMinimumWidth(150)

        controls = QtWidgets.QHBoxLayout()
        controls.addWidget(port_box)
        controls.addWidget(baud_rate_box)
        controls.addWidget(emulate_box)
        controls.addWidget(access_box)
        controls.addStretch()
        layout.addLayout(controls, 0, 0, 1, 2)

        outgoing, outgoing_layout = self._section("Input")
        outgoing_layout.addWidget(self.input_area)

        incoming, incoming_layout = self._section("Output")
        incoming_layout.addWidget(self.output_area)

        layout.addWidget(outgoing, 1, 0)
        layout.addWidget(incoming, 1, 1)
        state, state_layout = self._section("Status")
        state_layout.addWidget(self.field_names_label)
        state_layout.addWidget(self.status_area)
        # Коллизии — отдельно от полей кадра (треб. 6 методички ЛР4).
        state_layout.addWidget(self.collisions_label)
        layout.addWidget(state, 2, 0, 1, 2)
        layout.addWidget(self.sent_characters_label, 3, 0, 1, 2)

        layout.setRowStretch(1, 1)
        layout.setColumnStretch(0, 1)
        layout.setColumnStretch(1, 1)

    @staticmethod
    def _section(
        title: str,
    ) -> tuple[QtWidgets.QGroupBox, QtWidgets.QVBoxLayout]:
        section = QtWidgets.QGroupBox(title)
        layout = QtWidgets.QVBoxLayout(section)
        layout.setContentsMargins(10, 18, 10, 10)
        return section, layout

    def _connect_signals(self) -> None:
        self.port_choice.currentIndexChanged.connect(self._port_selected)

        self.baud_rate_choice.currentIndexChanged.connect(self._change_baud_rate)

        self.input_area.character_entered.connect(self._send_character)

        self.error_emulation_checkbox.toggled.connect(self._toggle_error_emulation)
        self.multiple_access_checkbox.toggled.connect(self._toggle_multiple_access)

    def _load_ports(self) -> None:
        ports = list(SerialConnection.available_ports())

        self.port_choice.addItems(ports)

        self.port_choice.setCurrentIndex(-1)

    def _port_selected(self) -> None:
        if self._handling_error:
            return

        has_port = bool(self.port_choice.currentText().strip())
        self.baud_rate_choice.setEnabled(has_port)

        if not has_port:
            self.baud_rate_choice.setCurrentIndex(-1)
            return

        self._try_open()

    def _try_open(self) -> None:
        if self.connection is not None:
            return

        if self._handling_error:
            return

        port_name = self.port_choice.currentText().strip()

        baud_rate = self.baud_rate_choice.currentData()

        if not port_name or baud_rate is None:
            return

        try:
            baud_rate = int(baud_rate)
        except (TypeError, ValueError):
            return

        try:
            connection = self._connection_scope.enter_context(
                SerialConnection(
                    port_name,
                    baud_rate,
                )
            )

        except ConnectionError as error:
            self._show_open_error(str(error))

            return

        except Exception as error:
            self._show_open_error(f"Could not open {port_name}: {error}")

            return

        self.connection = connection

        connection.received.connect(self._append_received)

        connection.frame_received.connect(self._show_received_frame)

        connection.frame_sent.connect(self._show_frame)

        connection.error.connect(self._show_error)
        connection.warning.connect(self._show_warning)

        connection.set_emulate_errors(self.error_emulation_checkbox.isChecked())
        connection.set_emulate_multiple_access(
            self.multiple_access_checkbox.isChecked()
        )

        self._port_opened()

    def _change_baud_rate(self) -> None:
        if self._handling_error:
            return

        baud_rate = self.baud_rate_choice.currentData()
        if baud_rate is None:
            return

        if self.connection is not None:
            if baud_rate == self.connection.baud_rate:
                return
            self._close_connection()

        self._try_open()

    def _port_opened(self) -> None:
        self.port_choice.setEnabled(False)
        self.input_area.setEnabled(True)
        self.input_area.setFocus()

    def _toggle_error_emulation(self, enabled: bool) -> None:
        """Передать в соединение флаг эмуляции ошибок (вариант 1 — 10 %)."""
        if self.connection is not None:
            self.connection.set_emulate_errors(enabled)

    def _toggle_multiple_access(self, enabled: bool) -> None:
        """Разрешить/запретить эмуляцию множественного доступа (ЛР4, вариант 1)."""
        if self.connection is not None:
            self.connection.set_emulate_multiple_access(enabled)

    def _send_character(
        self,
        character: str,
    ) -> None:
        connection = self.connection

        if connection is None:
            return

        connection.send_character(character)

        self.sent_characters += 1
        self._refresh_state()

    def _append_received(
        self,
        text: str,
    ) -> None:
        cursor = self.output_area.textCursor()

        cursor.movePosition(QtGui.QTextCursor.MoveOperation.End)

        cursor.insertText(
            text.replace(
                "\r\n",
                "\n",
            ).replace(
                "\r",
                "\n",
            )
        )

        self.output_area.setTextCursor(cursor)

        self.output_area.ensureCursorVisible()

    def _refresh_state(self) -> None:
        self.sent_characters_label.setText(
            f"Sent characters: {self.sent_characters}"
        )

    def _show_frame(
        self,
        transmission: FrameTransmission,
        *,
        update_collisions: bool = True,
    ) -> None:
        """Показать последний кадр: до stuffing, после stuffing, после эмуляции ошибки."""
        # 1) до бит-стаффинга, 2) после него, 3) после эмуляции ошибки
        self.status_area.setHtml(
            "<div style='font-family: Consolas, monospace; font-size: 12px; "
            "line-height: 1.35;'>"
            f"{transmission.before}<br>"
            f"{transmission.after_html}<br>"
            f"{transmission.after_error_html}"
            "</div>"
        )
        # При эмуляции прокрутить к третьей строке — там подчёркнутый байт.
        bar = self.status_area.verticalScrollBar()
        bar.setValue(bar.maximum())
        # ЛР4: коллизии последнего кадра — последовательно, через пробел.
        if update_collisions:
            self.collisions_label.setText(transmission.collisions_display)

    def _show_received_frame(self, frame: object) -> None:
        data = getattr(frame, "data", None)
        if isinstance(data, bytes):
            self._show_frame(
                encode_frame(data.decode("utf-8")),
                update_collisions=False,
            )

    def _show_warning(
        self,
        message: str,
    ) -> None:
        if self._handling_error:
            return

        self._handling_error = True
        try:
            QtWidgets.QMessageBox.warning(
                self,
                "COM Port Messenger",
                message,
            )
        finally:
            self._handling_error = False

    def _show_open_error(
        self,
        message: str,
    ) -> None:
        if self._handling_error:
            return

        self._handling_error = True

        try:
            QtWidgets.QMessageBox.critical(
                self,
                "COM Port Messenger",
                message,
            )

        finally:
            self._handling_error = False

    def _show_error(
        self,
        message: str,
    ) -> None:
        if self.connection is None:
            return

        if self._handling_error:
            return

        self._handling_error = True

        try:
            self._reset_connection_controls()

            QtWidgets.QMessageBox.critical(
                self,
                "COM Port Messenger",
                message,
            )

        finally:
            self._handling_error = False

    def _close_connection(self) -> None:
        connection = self.connection

        self.connection = None

        if connection is not None:
            for signal, handler in (
                (connection.received, self._append_received),
                (connection.error, self._show_error),
                (connection.warning, self._show_warning),
                (connection.frame_received, self._show_received_frame),
                (connection.frame_sent, self._show_frame),
            ):
                try:
                    signal.disconnect(handler)
                except TypeError:
                    pass

        self._connection_scope.close()
        self._connection_scope = ExitStack()

    def _reset_connection_controls(self) -> None:
        self._close_connection()

        self.port_choice.setEnabled(True)

        self.port_choice.setCurrentIndex(-1)

        self.baud_rate_choice.setCurrentIndex(-1)
        self.baud_rate_choice.setEnabled(False)

        self.input_area.setEnabled(False)

    def _apply_style(self) -> None:
        self.setStyleSheet("""
            QWidget {
                background: #f0f0f0;
                color: #202020;
                font: 14px 'Segoe UI';
            }

            QGroupBox {
                border: 1px solid #c8c8c8;
                margin-top: 9px;
                padding-top: 7px;
            }

            QGroupBox::title {
                subcontrol-origin: margin;
                left: 8px;
                padding: 0 3px;
            }

            QComboBox {
                background: white;
                border: 1px solid #a8a8a8;
                padding: 5px 8px;
                min-height: 24px;
            }

            QPlainTextEdit {
                background: white;
                border: 1px solid #303030;
                padding: 4px;
                selection-background-color: #9bbcff;
            }

            QTextBrowser {
                background: white;
                border: 1px solid #303030;
                padding: 4px;
                selection-background-color: #9bbcff;
            }

            """)
        self.status_area.document().setDefaultStyleSheet(
            "u { text-decoration: underline; font-weight: bold; }"
        )

    @override
    def closeEvent(
        self,
        event: QtGui.QCloseEvent | None,
    ) -> None:
        self._close_connection()

        if event is not None:
            event.accept()
