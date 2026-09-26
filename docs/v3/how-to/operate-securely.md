# How to operate cookies, secrets, and plugins safely

- browser session は `image-downloader cookie browser-import DOMAIN` で profile cookie store に import する。browser support は optional dependency である。
- export/import は passphrase を対話で扱う。cookie body、passphrase、browser record、secret reference を JSON や log に出力してはならない。
- plugin install/trust/revoke/uninstall の前に plugin root を backup し、manifest ID、publisher、kind、version、fingerprint、tree digest を確認する。
- automation で mutation を確認する場合だけ `--yes` を使う。

暗号化・merge・lock・保証範囲は [runtime behavior reference](../reference/runtime-behavior.md#runtime-cookies)、catalog policy は [plugin package reference](../reference/plugin-package.md#plugin-catalog)、CLI output は [CLI reference](../reference/cli.md#cli-json) が正本である。
