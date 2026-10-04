"""Private character decoding policy for the core HTML fallback only."""

from __future__ import annotations

from collections.abc import Mapping
from email.message import Message
from html.parser import HTMLParser
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from webencodings import Encoding

_ASCII_SPACE = "\t\n\f\r "
_RAW_TEXT = frozenset({"script", "style", "title", "textarea"})


def _charset(content_type: str) -> str | None:
    message = Message()
    message["content-type"] = content_type
    value = message.get_param("charset")
    return value if isinstance(value, str) else None


def _encoding(label: str | None) -> Encoding | None:
    # Import only when the core fallback actually performs HTML decoding.
    import webencodings

    encoding = webencodings.lookup(label.strip(_ASCII_SPACE)) if label else None
    return encoding if encoding is None or encoding.name != "replacement" else None


class _MetaEncodingParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.encoding: Encoding | None = None
        self._raw_text: str | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if self.encoding is not None or self._raw_text is not None:
            return
        if tag in _RAW_TEXT:
            self._raw_text = tag
            return
        if tag != "meta":
            return
        values: dict[str, str] = {}
        for name, value in attrs:
            values.setdefault(name, value or "")
        label = values.get("charset")
        if "charset" not in values and values.get("http-equiv", "").strip(_ASCII_SPACE).lower() == "content-type":
            label = _charset(values.get("content", ""))
        encoding = _encoding(label)
        if encoding is not None:
            if encoding.name in {"utf-16le", "utf-16be"}:
                encoding = _encoding("utf-8")
            elif encoding.name == "x-user-defined":
                encoding = _encoding("windows-1252")
            self.encoding = encoding

    def handle_endtag(self, tag: str) -> None:
        if tag == self._raw_text:
            self._raw_text = None


def _decode_html(body: bytes, headers: Mapping[str, str]) -> str:
    for bom, codec in ((b"\xef\xbb\xbf", "utf-8"), (b"\xff\xfe", "utf-16le"), (b"\xfe\xff", "utf-16be")):
        if body.startswith(bom):
            return body[len(bom) :].decode(codec, errors="replace")
    if not body.isascii():
        try:
            return body.decode("utf-8", errors="strict")
        except UnicodeDecodeError:
            pass
    content_type = next((value for name, value in headers.items() if name.lower() == "content-type"), "")
    encoding = _encoding(_charset(content_type))
    if encoding is None:
        parser = _MetaEncodingParser()
        parser.feed(body[:1024].decode("latin-1"))
        # Do not close: incomplete markup crossing the byte limit is not a declaration.
        encoding = parser.encoding
    if encoding is None:
        return body.decode("utf-8", errors="replace")
    return cast(str, encoding.codec_info.decode(body, "replace")[0])
