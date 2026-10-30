<a id="tutorial-embedded-download"></a>

# チュートリアル：ライブラリへの組込み

stable import は package root から行う。全引数・例外・resource lifetime は [ライブラリAPIリファレンス](../reference/library-api.md) を正本とする。

```python

from image_downloader import DownloadRequest
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
        result = await service.download(DownloadRequest(url="https://example.test/gallery"))
        for chapter in result.chapters:
            for outcome in chapter.outcomes:
                if outcome.failure is not None:
                    print(outcome.image.image_id, outcome.failure.code)
```

`plugin_root(config)` は `plugins.root: null` の OS標準のルート を含めて安全に解決し、未作成の最終 directory でも plugin root を作成しない。`RuntimeComposer.compose()` は log、state、HTTP/worker resources を作成して service が所有するため、必ず `async with` または `await close()` で終了する。`DownloadService` は一 instance 内で operation を直列化する。並行 operation が必要なら service を別に compose する。

<a id="other-embedded-operations"></a>

## その他の組み込み操作

以降の例では、最初の例と同様に`config`、`config_root`、`plugin_root(config)`を準備済みとします。例示URLは、インストール済みプラグインが扱えるURLへ置き換えてください。ブラウザーは不要で、インポート時に暗黙の通信は行いません。

<a id="check-a-complete-update-snapshot"></a>

### 完全な更新スナップショットの確認

```python

from image_downloader import UpdateCheckRequest
from pathlib import Path

from image_downloader import AppConfig, RuntimeComposer
from image_downloader.config import plugin_root


async def check_for_updates(config: AppConfig, config_root: Path) -> list[str]:
    async with RuntimeComposer(config, config_root=config_root, plugin_root=plugin_root(config)).compose() as service:
        update = await service.check_updates(UpdateCheckRequest(url="https://example.test/gallery"))
        return [item.url for item in update.changes if item.kind.value != "removed"]
```

`check_updates()`には、選択したプラグインが`UpdateProvider`を実装している必要があります。完全な`UpdateResult`を返すか例外を送出し、部分的な一覧は返しません。CLIでは`download URL --list-updated-urls`が対応します。

<a id="use-a-one-operation-plugin-override"></a>

### 1回の操作だけプラグイン設定を上書きする

```python

from image_downloader import DownloadRequest
from pathlib import Path

from image_downloader import AppConfig, RuntimeComposer
from image_downloader.config import plugin_root


async def download_with_override(config: AppConfig, config_root: Path) -> None:
    overrides = {"com.example.gallery": {"page_size": 50}}
    async with RuntimeComposer(config, config_root=config_root, plugin_root=plugin_root(config)).compose() as service:
        await service.download(DownloadRequest(url="https://example.test/gallery", plugin_overrides=overrides))
```

`PluginConfigOverrides`は、プラグインIDから設定オブジェクトのマッピングへの対応です。選択された候補に対して検証し、YAMLレイヤーには書き込みません。対応するCLI形式は`--plugin-config`と`--plugin-config-file`です。

<a id="registry-only-inspection"></a>

### レジストリだけを使った検査

```python
from pathlib import Path

from image_downloader import AppConfig, RuntimeComposer
from image_downloader.config import plugin_root


def inspect_registry(config: AppConfig, config_root: Path) -> None:
    composer = RuntimeComposer(config, config_root=config_root, plugin_root=plugin_root(config))
    registry = composer.compose_registry()
    try:
        registry.doctor_validate()
        registry.resolve("https://example.test/gallery", fallback_enabled=True)
    finally:
        registry.close()
```

`compose_registry()`は、`DownloadService`、HTTPゲートウェイ、ワーカー、ログ処理を作成しません。返されたレジストリは分離されたプラグインモジュールの名前空間を所有するため、検証や選択に失敗した場合も、呼び出し側で閉じてください。

<a id="handle-result-failures-and-operation-failures-separately"></a>

### 結果内の失敗と操作全体の失敗を分けて扱う

```python

from image_downloader import DownloadRequest
from pathlib import Path

from image_downloader import AppConfig, AuthenticationError, ImageDownloaderError, PluginError, RuntimeComposer
from image_downloader.config import plugin_root


async def download_with_handling(config: AppConfig, config_root: Path) -> None:
    try:
        async with RuntimeComposer(config, config_root=config_root, plugin_root=plugin_root(config)).compose() as service:
            result = await service.download(DownloadRequest(url="https://example.test/gallery"))
    except AuthenticationError:
        # 再ログイン・セッション再作成を要求する。DownloadResultは返されていない。
        raise
    except PluginError:
        # プラグインのパッケージ・設定を確認する。操作全体の失敗。
        raise
    except ImageDownloaderError:
        # 自動処理の安定した判定には、生の例外文ではなくcode・reasonを使う。
        raise
    else:
        failures = [outcome.failure for chapter in result.chapters for outcome in chapter.outcomes]
        failures = [failure for failure in failures if failure is not None]
```

通常の画像失敗は`ImageOutcome.failure`として返します。設定、認証、プラグイン、安全性・排他、キャンセルによる失敗では部分結果を返しません。例外の対応表は[ライブラリAPI](../reference/library-api.md#api-errors)を参照してください。

<a id="advanced-dependency-injection"></a>

### 高度な依存性の注入

```python
from image_downloader import AppConfig, DownloadService
from image_downloader.runtime import _RuntimeDependencies
from image_downloader.security import PluginRuntime


def build_service(
    config: AppConfig, registry: PluginRuntime, dependencies: _RuntimeDependencies
) -> DownloadService:
    # 完全で互換性のある依存一式の構築は、呼出し元が担当する。
    return DownloadService(config, registry, dependencies)
```

これは独自の依存構築を統合するための境界です。`_RuntimeDependencies`には、利用可能で互換性のあるファイルシステム、状態、ログ、Cookie、資格情報、ゲートウェイ、画像処理、イベント、通知、lockの全依存を含めます。必須の`credentials`には、対象データルート・プロファイルの`ProfileCredentialStore`を渡し、`cookie_store`にも同じストアを使います。受け取った`DownloadService`が、それらの非同期の終了処理を所有します。使用前に[高度な依存構築の契約](../reference/library-api.md#api-call-outcomes)と[直接利用する保管庫API](../reference/credential-vault.md#直接利用するapi)を確認してください。
