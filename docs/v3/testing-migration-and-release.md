# 検証・移行・リリース

## 互換性の検証

API v3 の contract test は facade の <code>__all__</code>、参照文書 inventory、代表 signature、DTO field、error catalog、logging の明示 export を照合する。公開 API を追加、削除、改名する変更では、実装、[完全 API 参照](api-reference.md)、契約テストを同じ変更に含める。

Markdown link test は現行正本、ルート README、plugin template、plugin source README を検証する。archive snapshot は履歴資料であり、現行 link 完全性・実行可能性の対象外である。

## v2 以前からの移行

v2 descriptor、旧 config tree、旧 wheel sidecar は API v3 では読み込まれない。移行時は manifest、author config、catalog、signature、site plugin protocol、output/config layer を v3 仕様に作り直し、template ではなく現行の参照 plugin を比較対象にする。

## リリース判定

公開前に次を確認する。

1. unit、integration、distribution contract test が通る。
2. package root と各 facade の stable export が inventory と一致する。
3. command ごとの human output と JSON output、終了コードを検証する。
4. strict verification で署名済み plugin の install、trust、revoke、uninstall、doctor を検証する。
5. config migration を行うリリースでは backup と <code>rewrite_user_layers</code> の副作用を release note に記載する。
6. API v3 正本へのリンクに切れがなく、legacy snapshot を現行仕様として案内していない。
