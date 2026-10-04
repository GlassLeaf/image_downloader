"""Assert the core HTML decoder is unreachable from offline command handlers."""

import pytest

from image_downloader.commands.config import ConfigCommandHandler
from image_downloader.commands.cookie import CookieCommandHandler
from image_downloader.commands.doctor import DoctorCommandHandler
from image_downloader.commands.plugin import PluginCommandHandler
from image_downloader.commands.state import StateCommandHandler
from image_downloader.commands.verify import VerifyCommandHandler
from image_downloader.plugins import builtin, html_encoding


@pytest.fixture(autouse=True)
def forbid_html_decoder_in_offline_commands(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("offline commands must not use the core HTML decoder")

    for handler in (
        ConfigCommandHandler,
        CookieCommandHandler,
        DoctorCommandHandler,
        PluginCommandHandler,
        StateCommandHandler,
        VerifyCommandHandler,
    ):
        original = handler.handle

        async def guarded(self, args, original=original):
            with pytest.MonkeyPatch.context() as guard:
                guard.setattr(builtin, "_decode_html", forbidden)
                guard.setattr(html_encoding, "_decode_html", forbidden)
                return await original(self, args)

        monkeypatch.setattr(handler, "handle", guarded)
