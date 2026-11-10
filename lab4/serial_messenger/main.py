"""Точка входа графического приложения."""

import sys

from PyQt6 import QtWidgets

from frontend import SerialMessenger


def main() -> None:
    app = QtWidgets.QApplication(sys.argv)
    window = SerialMessenger()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
