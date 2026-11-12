"""Values that come from a site (tags, file names, registry entries) must never become commands, SQL, a login
sent to a stranger, or a folder outside Packrat's own."""
import io
import os
import re
import zipfile
from pathlib import Path
from types import SimpleNamespace

from packrat import paths
from packrat.grab import postsave, registry
from packrat.grab.sources import Site
from packrat.grab.tags import Tag

EVIL = 'x" & calc & echo "'


class St(dict):
    def get(self, k, d=None):
        return super().get(k, d)


def _captured(monkeypatch):
    calls = []
    monkeypatch.setattr(postsave.subprocess, "run",
                        lambda cmd, **kw: calls.append(cmd) or SimpleNamespace(returncode=0, stderr=b""))
    return calls


def _one_argument(cmd, value_start):
    """Windows runs commands through cmd.exe: nothing from the value may end the command or start another.
    Elsewhere there is no shell (shlex.split + a list): the value can't unbalance the quotes."""
    if os.name == "nt":
        outside_quotes = re.sub(r'"[^"]*"', "", cmd)     # inside "..." cmd.exe takes & | < > literally
        assert '"x' not in cmd and 'x"' not in cmd                # no quote from the site's value survives
        assert cmd.count('"') % 2 == 0 and not re.search(r"[&|<>^]", outside_quotes)
    else:
        assert isinstance(cmd, list) and any(a.startswith(value_start) for a in cmd)
        assert not any('"' in a for a in cmd)


def test_tag_command_cannot_chain_commands(monkeypatch):
    calls = _captured(monkeypatch)
    ps = postsave.PostSave(St({"Exec/tag_after": "echo %tag%"}))
    ps.commands(SimpleNamespace(tags=[Tag(EVIL, "general")]), None, "/tmp/a.jpg", {}, 1)
    assert len(calls) == 1
    _one_argument(calls[0], "x")


def test_image_command_tokens_are_made_safe(monkeypatch):
    calls = _captured(monkeypatch)
    ps = postsave.PostSave(St({"Exec/image": "tool %md5% %path%"}))
    ps.run_command("tool %md5% %path%", {"md5": EVIL}, "image", "/tmp/black & white.jpg", 1)
    assert len(calls) == 1
    _one_argument(calls[0], "x")
    # the path keeps its real name, quoted, so a file name with '&' or spaces stays one argument
    p = str(Path("/tmp/black & white.jpg"))
    assert (f'"{p}"' in calls[0]) if os.name == "nt" else (p in calls[0])


def test_path_quoting_respects_quotes_already_there():
    p = str(Path("/tmp/a b.jpg"))
    assert postsave.path_tokens('x "%path%"', "/tmp/a b.jpg", "command") == f'x "{p}"'
    assert postsave.path_tokens("x %path%", "/tmp/a b.jpg", "command") == f'x "{p}"'
    assert postsave.path_tokens("x %path%", "/tmp/a b.jpg") == f"x {p}"


def test_sql_values_are_escaped(monkeypatch):
    ran = []
    ps = postsave.PostSave(St({"Exec/SQL/tag_after": "INSERT INTO t VALUES(%tag%, '%original%', '%path%')",
                               "Exec/SQL/driver": "QSQLITE"}))
    monkeypatch.setattr(ps, "_sql_exec", ran.append)
    ps.commands(SimpleNamespace(tags=[Tag("o'neil\\x", "artist")]), None, "/tmp/it's.jpg", {}, 1)
    p = str(Path("/tmp/it's.jpg")).replace("'", "''")
    # SQLite keeps backslashes as they are (only MySQL treats them as escapes)
    assert ran == [f"INSERT INTO t VALUES('o''neil\\x', 'o''neil\\x', '{p}')"]


def test_login_headers_stay_on_the_site():
    own = lambda host, url: Site.owns_url(SimpleNamespace(host=host), url)  # noqa: E731
    assert own("danbooru.donmai.us", "https://cdn.donmai.us/original/a.jpg")
    assert own("e621.net", "https://static1.e621.net/data/a.png")
    assert own("chan.sankakucomplex.com", "https://capi-v2.sankakucomplex.com/posts")
    assert own("e621.net", "/posts.json")
    assert not own("e621.net", "https://evil.example/a.png")
    assert not own("e621.net", "https://e621.net.evil.example/a.png")
    assert not own("booru.example.co.uk", "https://other.co.uk/a.png")
    assert own("booru.example.co.uk", "https://img.example.co.uk/a.png")


def test_page_url_never_carries_the_login():
    from packrat.grab.image import Image
    site = SimpleNamespace(fix_url=lambda u: u, fix_login_url=lambda u: u + "?login=me&api_key=SECRET",
                           tag_db_types=lambda names: {}, key="e621.net", host="e621.net")
    img = Image.from_model(site, {"id": 1, "page_url": "https://e621.net/posts/1", "file_url":
                                  "https://static1.e621.net/data/a.png"}, type_ids={})
    assert img is not None and "SECRET" not in img.page_url


def test_registry_name_cannot_leave_the_sources_folder(monkeypatch, tmp_path):
    monkeypatch.setenv("PACKRAT_HOME", str(tmp_path))
    keep = paths.data_dir() / "settings.json"
    keep.write_text("{}", "utf-8")
    model = next(paths.bundled_sources_dir().glob("*/model.js"))
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("model.js", model.read_text("utf-8"))
    dest = registry.install_zip(buf.getvalue(), "..")
    assert dest.parent == paths.user_sources_dir() and keep.exists()
    assert registry.uninstall("..") is False and keep.exists()
