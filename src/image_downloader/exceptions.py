class DownloaderError(Exception):
    """Base exception for expected downloader failures."""


class ConfigurationError(DownloaderError, ValueError):
    pass


class PluginError(DownloaderError):
    pass


class UnsupportedSiteFeature(PluginError):
    """A site requires a capability outside the v2 plugin contract."""


class AuthenticationError(DownloaderError):
    pass


class SecretNotFound(AuthenticationError):
    pass


class StorageSafetyError(DownloaderError):
    """A filesystem operation would leave its configured trusted root."""


class InterProcessLockError(DownloaderError):
    """An inter-process lock could not be acquired or released safely."""
