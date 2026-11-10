"""Entry point of the built programs (PyInstaller): Packrat.exe (the window) and packrat-cli.exe (commands).

    Packrat.exe                      opens the window
    Packrat.exe gui --classic        the older Tk window
    packrat-cli.exe batch            a command, with output in the console
The window starts packrat-cli.exe for the download list and the site archive, as "-m packrat ..." (the same
words it uses with a normal Python), so a leading "-m packrat" is ignored here.
"""
import os
import sys


def main() -> int:
    args = sys.argv[1:]
    if args[:2] == ["-m", "packrat"]:
        args = args[2:]
    for name in ("stdout", "stderr"):          # a windowed program has no console to write to
        if getattr(sys, name) is None:
            setattr(sys, name, open(os.devnull, "w", encoding="utf-8"))  # noqa: SIM115
    if not args:
        from packrat.grabcli import open_window
        return open_window()
    from packrat.cli import main as cli_main
    return cli_main(args)


if __name__ == "__main__":
    raise SystemExit(main())
