# How to configure profiles and inspect effective values

1. `image-downloader config path` で fixed user config と default root を確認する。
2. `config init` で main config を作り、`config profile init NAME` で profile layer を作る。
3. `config explain --host HOST --json` で有効値、layer、origin を確認する。
4. state-changing operation の前に、backup と `doctor --host HOST --json` を実行する。

profile/site layer に書けない bootstrap fields、merge 順、`rewrite_user_layers` の書換え副作用、link/reparse point の扱いは [configuration reference](../reference/configuration.md#config-layers) を参照する。
