# Tutorial: embedded download

stable import は package root から行う。全引数・例外・resource lifetime は [library API reference](../reference/library-api.md) を正本とする。

```python
from pathlib import Path

from image_downloader import RuntimeComposer, load_application_config
from image_downloader.config import plugin_root

async def download() -> None:
    config_root = Path("config").resolve()
    config = load_application_config(config_root / "app.yaml", require_config=True)

    async with RuntimeComposer(
        config,
        config_root=config_root,
        plugin_root=plugin_root(config),
    ).compose() as service:
        result = await service.run("https://example.test/gallery")
        for chapter in result.chapters:
            for outcome in chapter.outcomes:
                if outcome.failure is not None:
                    print(outcome.image.image_id, outcome.failure.code)
```

`plugin_root(config)` は `plugins.root: null` の platform root を含めて安全に解決し、未作成の最終 directory でも plugin root を作成しない。`RuntimeComposer.compose()` は log、state、HTTP/worker resources を作成して service が所有するため、必ず `async with` または `await close()` で終了する。`DownloadService` は一 instance 内で operation を直列化する。並行 operation が必要なら service を別に compose する。

## Other embedded operations

All snippets assume that `config`, `config_root`, and `plugin_root(config)` were
loaded as in the first example. Replace the example URL with one handled by an
installed plugin; no snippet requires a browser or makes a hidden network call
on import.

### Check a complete update snapshot

```python
async def check_for_updates(config, config_root) -> list[str]:
    async with RuntimeComposer(config, config_root=config_root, plugin_root=plugin_root(config)).compose() as service:
        update = await service.check_updates("https://example.test/gallery")
        return [item.url for item in update.changes if item.kind.value != "removed"]
```

`check_updates()` requires the selected plugin to implement `UpdateProvider`.
It returns a complete `UpdateResult` or raises; it does not return a partial
list. For the CLI equivalent use `download URL --list-updated-urls`.

### Use a one-operation plugin override

```python
async def download_with_override(config, config_root) -> None:
    overrides = {"com.example.gallery": {"page_size": 50}}
    async with RuntimeComposer(config, config_root=config_root, plugin_root=plugin_root(config)).compose() as service:
        await service.run("https://example.test/gallery", plugin_overrides=overrides)
```

`PluginConfigOverrides` is a mapping of plugin ID to object mapping. It is
validated for the selected candidate and is not written to the YAML layer.
`--plugin-config` and `--plugin-config-file` are the corresponding CLI forms.

### Registry-only inspection

```python
def inspect_registry(config, config_root) -> None:
    composer = RuntimeComposer(config, config_root=config_root, plugin_root=plugin_root(config))
    registry = composer.compose_registry()
    try:
        registry.doctor_validate()
        registry.resolve("https://example.test/gallery", fallback_enabled=True)
    finally:
        registry.close()
```

`compose_registry()` intentionally does not create a `DownloadService`, HTTP
gateway, worker, or logger. Its returned registry owns isolated plugin module
namespaces, so the caller must close it even if validation or selection fails.

### Handle result failures and operation failures separately

```python
from image_downloader import AuthenticationError, ImageDownloaderError, PluginError

async def download_with_handling(config, config_root) -> None:
    try:
        async with RuntimeComposer(config, config_root=config_root, plugin_root=plugin_root(config)).compose() as service:
            result = await service.run("https://example.test/gallery")
    except AuthenticationError:
        # Request a new login/session; no DownloadResult was returned.
        raise
    except PluginError:
        # Inspect the plugin package/configuration; this is operation-level.
        raise
    except ImageDownloaderError:
        # Use code/reason for stable automation behavior, not raw exception text.
        raise
    else:
        failures = [outcome.failure for chapter in result.chapters for outcome in chapter.outcomes]
        failures = [failure for failure in failures if failure is not None]
```

Normal image failures are returned through `ImageOutcome.failure`; configuration,
authentication, plugin, safety/lock, and cancellation failures do not produce a
partial result. The complete exception matrix is in [library API](../reference/library-api.md#api-errors).

### Advanced dependency injection

```python
from image_downloader import DownloadService
from image_downloader.runtime import _RuntimeDependencies

def build_service(config, registry, dependencies: _RuntimeDependencies) -> DownloadService:
    # The caller owns construction of a complete, compatible dependency bundle.
    return DownloadService(config, registry, dependencies)
```

This is an integration boundary, not a shortcut around `RuntimeComposer`:
`_RuntimeDependencies` contains all open filesystem, state, logging, cookie,
gateway, processor, event, notification, and lock dependencies. Once accepted,
the returned `DownloadService` owns their asynchronous shutdown. See the
[advanced composition contract](../reference/library-api.md#api-call-outcomes)
before using it.
