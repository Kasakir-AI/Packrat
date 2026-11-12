"""Saved logins: values longer than Windows allows per credential are split and put back together."""
from packrat.grab import secrets


class FakeKeyring:
    LIMIT = 1280          # characters Windows accepts per credential

    def __init__(self):
        self.d = {}

    def get_password(self, service, name):
        return self.d.get((service, name))

    def set_password(self, service, name, value):
        if len(value) > self.LIMIT:
            raise OSError("CredWrite failed: the stub received bad data")
        self.d[(service, name)] = value

    def delete_password(self, service, name):
        del self.d[(service, name)]


def test_long_values_are_split_and_restored(monkeypatch):
    kr = FakeKeyring()
    monkeypatch.setattr(secrets, "_keyring", lambda: kr)
    monkeypatch.setattr(secrets, "_mem", {})
    long = "c" * 4321
    assert secrets.set_secret("site.example", "session_cookies", long) is True
    assert len(kr.d) == 6                                    # marker + 5 pieces
    secrets._mem.clear()
    assert secrets.get_secret("site.example", "session_cookies") == long
    assert secrets.set_secret("site.example", "session_cookies", "short") is True      # shrinking removes the pieces
    assert list(kr.d.values()) == ["short"]
    secrets._mem.clear()
    assert secrets.get_secret("site.example", "session_cookies") == "short"
    secrets.forget_scope("site.example", ["session_cookies"])
    assert kr.d == {}


def test_short_values_are_stored_as_before(monkeypatch):
    kr = FakeKeyring()
    monkeypatch.setattr(secrets, "_keyring", lambda: kr)
    monkeypatch.setattr(secrets, "_mem", {})
    secrets.set_secret("s", "apiKey", "abc123")
    assert kr.d == {(secrets.SERVICE, "s|apiKey"): "abc123"}


def test_danbooru_gets_an_honest_user_agent():
    """Grabber's Danbooru defaults pose as Firefox; Danbooru's bot check refuses that (403). Packrat says what it is."""
    from types import SimpleNamespace

    from packrat.grab.sources import Site
    fake = SimpleNamespace(host="danbooru.donmai.us", source=SimpleNamespace(name="Danbooru (2.0)"),
                           is_e621_family=lambda: False,
                           setting=lambda k, d=None: ("Mozilla/5.0 (Windows NT 10.0; rv:153.0) Gecko/20100101 Firefox/153.0 "
                                                      "Grabber/%version%") if k == "Headers/User-Agent" else d)
    ua = Site.user_agent(fake)
    assert ua.startswith("Packrat/") and "mozilla" not in ua.lower()
    other = SimpleNamespace(**{**vars(fake), "source": SimpleNamespace(name="Gelbooru (0.2)")})
    assert "Mozilla" in Site.user_agent(other)        # other sites keep their own defaults
