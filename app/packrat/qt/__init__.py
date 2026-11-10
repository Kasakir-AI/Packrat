"""Packrat's window, built with Qt (PySide6). The classic Tk window is still there: `packrat gui --classic`."""


def main(argv=None) -> int:
    from .app import main as _main
    return _main(argv)
