"""Where Packrat keeps its files: next to the program on Windows, in the user's Library for the Mac app."""
import sys
from pathlib import Path

from packrat import paths


def test_mac_app_keeps_files_in_the_users_library(monkeypatch, tmp_path):
    monkeypatch.delenv("PACKRAT_HOME", raising=False)
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    assert paths.install_root() == tmp_path / "Library" / "Application Support" / "Packrat"
    assert paths.install_root().is_dir()
    assert paths.default_downloads() == tmp_path / "Pictures" / "Packrat"


def test_windows_exe_keeps_files_next_to_it(monkeypatch, tmp_path):
    monkeypatch.delenv("PACKRAT_HOME", raising=False)
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(sys, "executable", str(tmp_path / "Packrat.exe"))
    assert paths.install_root() == tmp_path.resolve()
    assert paths.default_downloads() == tmp_path.resolve() / "downloads"
