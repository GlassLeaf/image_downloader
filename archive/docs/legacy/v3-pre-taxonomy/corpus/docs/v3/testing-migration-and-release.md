# 検証・移行・リリース

## 互換性の検証

API v3 の contract test は facade の <code>__all__</code>、参照文書 inventory、代表 signature、DTO field、error catalog、logging の明示 export を照合する。公開 API を追加、削除、改名する変更では、実装、[完全 API 参照](api-reference.md)、契約テストを同じ変更に含める。

Markdown link test は現行正本、ルート README、plugin template、plugin source README を検証する。archive snapshot は履歴資料であり、現行 link 完全性・実行可能性の対象外である。

`legacy-v3-coverage.md` は archive の各有効な仕様 claim を新正本へ対応付ける ledger である。テストは全 ID、destination file/anchor、`superseded` の理由と代替 anchor を検査する。構造を変える変更は同じ commit で ledger を更新する。

<a id="testing-config-cli"></a>

## 設定と CLI

設定 test は core → main app → profile app → global → profile global → parent host → full host → CLI override の順、main profile と `--profile`、overlay の禁止 section、profile data path を検査する。IDNA/PSL/IPv4/IPv6 host naming、unknown/legacy key reject、mapping merge と scalar/list/null replacement、plugin setting/secret/disabled processor も受入対象である。

CLI test は canonical/bare-URL form、command ごとの option reject、human/JSON success、JSON error、redaction、全 exit status、cookie の secret 非露出を検査する。`config init` と `config profile init` は JSON object と通常出力の両方を保つ。

<a id="testing-package-trust"></a>

## plugin package と信頼

plugin test は wrapper/inner exact schema、API/kind/ID/version/path/base64/key ID、Ed25519 signature、canonical digest、file tree、extra file/hash mismatch、link/reparse point/non-regular file と cache exception を検査する。catalog duplicate/unknown/stale/revoked entry、strict/warn/off/bypass、staged install rollback、atomic catalog replacement、key rotation/downgrade/revocation/priority confirmation も対象である。

<a id="testing-runtime-library"></a>

## 実行ライフサイクルと library

runtime test は enabled candidate の priority → catalog priority → tie failure、fallback/disabled behavior、synchronous `validate_config`、immutable/reduced contexts、override target validation、isolated module namespace、service snapshot と close を検査する。`inspect` pagination、auth refresh、create/recover image request、processor order/cleanup、media output validation、cancellation、per-image vs operation failure を fake request gateway で検査する。

library contract test は stable facade `__all__`、inventory、representative constructor/method signature、DTO field、error catalog、logging export を照合する。Pydantic inherited API/private name は inventory 対象外である。

<a id="testing-migration"></a>

## 移行

v2 descriptor、旧 config tree、旧 wheel sidecar は API v3 では読み込まれない。移行時は manifest、author config、catalog、signature、site plugin protocol、output/config layer を v3 仕様に作り直し、template ではなく現行の参照 plugin を比較対象にする。

Update state schema の移行、plugin root/catalog backup、secret reference の再解決も確認する。main/profile/site config に旧 key/path を残さず、`doctor --json` が healthy で意図しない warning がなく、fallback と disabled plugin/processor の意図をレビューしてから切替える。

<a id="testing-release"></a>

## リリース

公開前に次を確認する。

1. unit、integration、distribution contract test が通る。
2. package root と各 facade の stable export が inventory と一致する。
3. command ごとの human output と JSON output、終了コードを検証する。
4. strict verification で署名済み plugin の install、trust、revoke、uninstall、doctor を検証する。
5. config migration を行うリリースでは backup と <code>rewrite_user_layers</code> の副作用を release note に記載する。
6. API v3 正本へのリンクに切れがなく、legacy snapshot を現行仕様として案内していない。
