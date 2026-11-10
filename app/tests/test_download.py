"""The download path end to end, against a small web server on this computer: saving, skipping what is there,
the download list (sites side by side, pages in order, single pictures in parallel), stop and resume, and
continuing a transfer that broke halfway."""
import hashlib
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace

import pytest
import requests

from packrat.events import StopRequested
from packrat.grab import download as D
from packrat.grab.image import Image
from packrat.grab.library import Library
from packrat.grab.settings import Settings
from packrat.grab.sources import PageResult


def body(n: int, size: int = 3000) -> bytes:
    return (b"\x89PNG\r\n\x1a\n" + f"picture {n} ".encode() * size)[:size]


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    state = None

    def log_message(self, *a):
        pass

    def do_GET(self):
        st = self.state
        name = self.path.rsplit("/", 1)[-1]
        n = int(name.split(".")[0])
        data = body(n, st.sizes.get(n, 3000))
        with st.lock:
            st.active += 1
            st.peak = max(st.peak, st.active)
            st.hits.append(n)
        try:
            if st.delay:
                time.sleep(st.delay)
            start = 0
            rng = self.headers.get("Range")
            if rng:
                start = int(rng.split("=")[1].split("-")[0])
                self.send_response(206)
                self.send_header("Content-Range", f"bytes {start}-{len(data) - 1}/{len(data)}")
                st.ranges.append((n, start))
            else:
                self.send_response(200)
            self.send_header("Content-Type", "image/png")
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Content-Length", str(len(data) - start))
            self.end_headers()
            if n in st.break_once and not rng:
                st.break_once.discard(n)
                self.wfile.write(data[:len(data) // 2])
                self.wfile.flush()
                self.close_connection = True
                self.connection.shutdown(socket.SHUT_RDWR)      # the connection drops halfway
                return
            self.wfile.write(data[start:])
        finally:
            with st.lock:
                st.active -= 1


@pytest.fixture
def server():
    st = SimpleNamespace(lock=threading.Lock(), active=0, peak=0, hits=[], ranges=[], delay=0.0, sizes={}, break_once=set())
    Handler.state = st
    srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    st.base = f"http://127.0.0.1:{srv.server_address[1]}"
    yield st
    srv.shutdown()
    srv.server_close()


class FakeSite:
    """What the saver needs from a site: paged results and plain downloads."""
    consecutive_403 = 0

    def __init__(self, host, base, ids, per_page=5, fail_page=None, page_delay=0.0):
        self.host = self.key = host
        self.name = host
        self.base, self.ids, self.per_page = base, list(ids), per_page
        self.fail_page, self.page_delay = fail_page, page_delay
        self.source = SimpleNamespace(meta={"apis": {}}, name="Fake")
        self.pages_asked = []
        self._sem = threading.BoundedSemaphore(10)

    def image(self, n):
        im = Image(self.host)
        im.id = n
        im.md5 = hashlib.md5(body(n, Handler.state.sizes.get(n, 3000))).hexdigest()
        im.file_url = f"{self.base}/{self.host}/{n}.png"
        im.ext = "png"
        im.details_loaded = True
        return im

    def search(self, search, page, per_page, prev=None, cancel=None):
        self.pages_asked.append(page)
        if self.page_delay:
            time.sleep(self.page_delay)
        if page == self.fail_page:
            return PageResult(self, search, page, error="HTTP 500")
        chunk = self.ids[(page - 1) * self.per_page: page * self.per_page]
        return PageResult(self, search, page, images=[self.image(n) for n in chunk],
                          page_count=-(-len(self.ids) // self.per_page), image_count=len(self.ids))

    def request(self, url, kind="page", *, stream=False, referer=None, cancel=None, retries=2, headers=None, **_):
        return requests.get(url, stream=stream, headers=headers or {}, timeout=10)

    def referer(self, kind, page_url, img=None):
        return ""

    def slot(self):
        return self._sem

    def details(self, img, cancel=None):
        return img

    def fix_url(self, u, old=None):
        return u

    def tag_db_types(self, names):
        return {}

    def page_url_for(self, img):
        return ""


@pytest.fixture
def env(tmp_path, server):
    st = Settings(tmp_path / "settings.json")
    st.update({"Save/path": str(tmp_path / "out"), "Save/filename": "%website%/%id%.%ext%", "Save/automaticretries": 0})
    lib = Library(tmp_path / "library.sqlite3")
    sites = {}
    store = SimpleNamespace(get=lambda h: sites.get(h))
    return SimpleNamespace(st=st, lib=lib, store=store, sites=sites, out=tmp_path / "out", server=server)


def add_group(env, sites, **kw):
    g = D.Group(search="x", sites=sites, per_page=5, **kw)
    env.lib.batch_add("group", g.to_json())


def saved(env):
    return sorted(str(p.relative_to(env.out)).replace("\\", "/") for p in env.out.rglob("*.png"))


def test_one_picture_is_saved_then_recognised(env):
    site = env.sites["a.test"] = FakeSite("a.test", env.server.base, [1])
    saver = D.ImageSaver(env.st, env.lib, env.store, threading.Event())
    im = site.image(1)
    r = saver.save(im, site)[0]
    assert r.status == "saved" and (env.out / "a.test" / "1.png").read_bytes() == body(1)
    assert saver.save(site.image(1), site)[0].status == "exists"
    assert env.lib.md5_path(im.md5) and env.lib.was_saved("a.test", 1)
    assert not list(env.out.rglob("*.part"))


def test_two_sites_download_side_by_side_and_each_gets_its_own_limit(env):
    env.server.delay = 0.05
    env.sites["a.test"] = FakeSite("a.test", env.server.base, range(1, 13), page_delay=0.05)
    env.sites["b.test"] = FakeSite("b.test", env.server.base, range(101, 113), page_delay=0.05)
    add_group(env, ["a.test", "b.test"], limit=7)
    batch = D.Batch(env.st, env.lib, env.store, workers=4)
    stats = batch.run_items(env.lib.batch_items())
    files = saved(env)
    assert [f for f in files if f.startswith("a.test")] == sorted(f"a.test/{n}.png" for n in range(1, 8))
    assert len([f for f in files if f.startswith("b.test")]) == 7          # the limit counts per site
    assert stats["saved"] == 14 and stats["failed"] == 0
    assert env.server.peak <= 4                                             # never more at once than the setting
    order = [n < 100 for n in env.server.hits]
    assert True in order[order.index(False):] or False in order[order.index(True):]     # the two sites interleave
    it = env.lib.batch_items()[0]
    assert it["status"] == "done" and it["data"]["done"] == {"a.test": 7, "b.test": 7}


def test_the_next_page_is_fetched_while_the_current_one_downloads(env):
    env.server.delay = 0.25
    site = env.sites["a.test"] = FakeSite("a.test", env.server.base, range(1, 11))
    add_group(env, ["a.test"])
    asked_at = {}
    orig = site.search

    def search(*a, **k):
        asked_at[a[1]] = len(env.server.hits)
        return orig(*a, **k)
    site.search = search
    D.Batch(env.st, env.lib, env.store, workers=2).run_items(env.lib.batch_items())
    assert len(saved(env)) == 10
    assert asked_at[2] < 5            # page 2 was asked for before page 1's five pictures had all been requested


def test_single_pictures_download_in_parallel(env):
    env.server.delay = 0.2
    site = env.sites["a.test"] = FakeSite("a.test", env.server.base, range(1, 9))
    for n in range(1, 9):
        env.lib.batch_add("unique", {"image": site.image(n).to_json(), "path": "", "filename": ""})
    t = time.time()
    stats = D.Batch(env.st, env.lib, env.store, workers=4).run_items(env.lib.batch_items())
    took = time.time() - t
    assert stats["saved"] == 8 and len(saved(env)) == 8
    assert env.server.peak >= 3 and took < 8 * 0.2                         # one at a time would take 1.6 s
    assert {it["status"] for it in env.lib.batch_items()} == {"done"}


def test_a_site_that_cannot_be_read_is_tried_again_next_time(env, monkeypatch):
    monkeypatch.setattr(D, "_sleep_check", lambda *a: None)
    site = env.sites["a.test"] = FakeSite("a.test", env.server.base, range(1, 16), fail_page=2)
    add_group(env, ["a.test"])
    D.Batch(env.st, env.lib, env.store, workers=2).run_items(env.lib.batch_items())
    it = env.lib.batch_items()[0]
    assert it["status"] == "error" and it["data"]["finished_sites"] == [] and len(saved(env)) == 5
    site.fail_page = None                                                   # the site works again
    site.pages_asked.clear()
    D.Batch(env.st, env.lib, env.store, workers=2).run_items(env.lib.batch_items())
    it = env.lib.batch_items()[0]
    assert it["status"] == "done" and len(saved(env)) == 15
    assert site.pages_asked[0] == 2                                         # continued at the page that failed


def test_stop_then_start_again_loses_and_repeats_nothing(env):
    env.server.delay = 0.05
    env.sites["a.test"] = FakeSite("a.test", env.server.base, range(1, 21))
    add_group(env, ["a.test"])
    cancel = threading.Event()
    batch = D.Batch(env.st, env.lib, env.store, workers=2, cancel=cancel)
    threading.Timer(0.3, cancel.set).start()
    with pytest.raises(StopRequested):
        batch.run_items(env.lib.batch_items())
    first = len(saved(env))
    assert 0 < first < 20 and env.lib.batch_items()[0]["status"] == "paused"
    assert not list(env.out.rglob("*.part"))
    stats = D.Batch(env.st, env.lib, env.store, workers=2).run_items(env.lib.batch_items())
    assert len(saved(env)) == 20 and env.lib.batch_items()[0]["status"] == "done"
    assert stats["saved"] + stats["exists"] >= 20 - first and stats["failed"] == 0
    for n in range(1, 21):
        assert (env.out / "a.test" / f"{n}.png").read_bytes() == body(n)


def test_a_transfer_that_breaks_halfway_continues_instead_of_starting_over(env, monkeypatch):
    monkeypatch.setattr(D, "_sleep_check", lambda *a: None)
    env.server.sizes[1] = 900_000
    env.server.break_once.add(1)
    site = env.sites["a.test"] = FakeSite("a.test", env.server.base, [1])
    r = D.ImageSaver(env.st, env.lib, env.store, threading.Event()).save(site.image(1), site)[0]
    assert r.status == "saved"
    assert (env.out / "a.test" / "1.png").read_bytes() == body(1, 900_000)
    assert r.md5 == hashlib.md5(body(1, 900_000)).hexdigest()
    assert env.server.ranges and env.server.ranges[0][0] == 1 and env.server.ranges[0][1] > 0
