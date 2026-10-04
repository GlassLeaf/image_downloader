"""Help returns before any HTML decoding or runtime operation."""

import pytest

from image_downloader.cli import main
from image_downloader.plugins import builtin, html_encoding


@pytest.mark.parametrize("command", [[], ["help"], ["help", "download"], ["help", "workflow"], ["help", "inspect"]])
def test_help_never_decodes_html(command, monkeypatch, capsys):
    def forbidden(*args, **kwargs):
        pytest.fail("help must not decode HTML")

    monkeypatch.setattr(builtin, "_decode_html", forbidden)
    monkeypatch.setattr(html_encoding, "_decode_html", forbidden)
    words = command or ["--help"]
    with pytest.raises(SystemExit) as result:
        main(words)
    assert result.value.code == 0
    assert "usage:" in capsys.readouterr().out
