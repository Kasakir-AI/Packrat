"""Small text helpers that need no display (so they can be tested anywhere)."""
from __future__ import annotations


def html_to_text(html: str) -> str:
    """Readable text from a site's wiki HTML (or DText): paragraphs and list items on their own lines."""
    import html as H
    import re as _re
    if not html.strip():
        return ""
    t = _re.sub(r"(?is)<(script|style).*?</\1>", "", html)
    t = _re.sub(r"(?i)<br\s*/?>", "\n", t)
    t = _re.sub(r"(?i)</(p|div|h\d|tr|ul|ol)>", "\n\n", t)
    t = _re.sub(r"(?i)<li[^>]*>", "\n  \u2022 ", t)
    t = _re.sub(r"<[^>]+>", "", t)
    t = H.unescape(t)
    t = _re.sub(r"\[/?(b|i|u|s|tn|spoiler|quote|section[^\]]*|expand[^\]]*)\]", "", t)   # DText markup
    t = _re.sub(r"\[\[([^\]|]+)\|([^\]]+)\]\]", r"\2", t)
    t = _re.sub(r"\[\[([^\]]+)\]\]", r"\1", t)
    t = _re.sub(r"[ \t]+\n", "\n", t)
    return _re.sub(r"\n{3,}", "\n\n", t).strip()
