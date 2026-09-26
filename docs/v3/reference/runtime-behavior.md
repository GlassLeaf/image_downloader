# Runtime behavior reference

この文書は public API の型ではなく、transport、persistence、output safety の実行契約を定義する。callable signature は [library API reference](library-api.md) を参照する。

## HTTP transport

<a id="runtime-transport"></a>
<a id="http-transport"></a>

plugin は raw HTTP client を作らず `await context.requests.execute(RequestSpec(...))` を使う。core が timeout、pool、cookie、redirect、authentication、origin/domain concurrency、rate limit を管理する。`RequestSpec`、`RequestResponse`、`ImageResource` は immutable DTO であり、header/query を変える場合は新しい value を返す。

GET、HEAD、OPTIONS、TRACE は retry 対象である。429、500、502、503、504 は `Retry-After` を尊重し、`network.retry_max_delay_seconds` 以下の jitter を加える。POST 等の non-idempotent request は `retry_non_idempotent=True` を plugin が安全性を保証するときだけ retry する。

redirect は hop ごとに policy を検査する。anonymous cross-origin redirect には Cookie、Referer、plugin-specific header、application header を引き継がない。response body は streaming で読み、宣言済みサイズと受信済みサイズのいずれも `network.max_response_bytes` を超えると `ResponseSizeLimitError` で止める。

## Cookies

<a id="runtime-cookies"></a>

<!-- claim: TAX-RUNTIME-COOKIES -->
profile cookie は `<storage.data_root>/profiles/<profile>/cookie/cookies.enc` に AES-256-GCM で保存する。暗号鍵は keyring service `image-downloader.cookie-key` に保持し、cookie 本体と鍵を app configuration に書かない。

cookie export/import は passphrase container を使い、passphrase を対話入力する。browser import は `browser-cookie3` を含む `.[browser-cookies]` extra が必要で、既存 jar を差分 merge する。破損した encrypted file は上書きせず error にし、明示 import を復旧手段にする。

同一端末上の本 application process は `cookie/cookies.lock` で read/merge/write を直列化する。異なる cookie は保持し、同一 `(domain, path, name)` は後に確定した更新、同値なら長い expiry、session cookie は persistent cookie より短期として扱う。lock file は解放後も残る。NAS と外部 application の同時変更は保証対象外である。`CookieStore.save()` は jar 全体を置換する library API であり、runtime の delta merge とは異なる。

## Output allocation and locks

output path は trusted root の配下だけに作る。managed directory/file/lock に symlink または Windows reparse point があれば `StorageSafetyError` を送出する。`OutputAllocator.allocate()` が reservation を返した `should_write=True` allocation は `commit()` または `abort()` の一度だけで完了する。`existing_file=skip` による `should_write=False` allocation は reservation を持たず、`commit()`/`abort()` は何度呼んでも no-op である。`existing_file=error` は controlled `ExistingFileConflictError`、output lock timeout は operation failure であり image retry ではない。

## Update state

<a id="runtime-update-state"></a>

<!-- claim: TAX-RUNTIME-UPDATE-STATE -->
profile state の `state/updates.json` は schema version 2 である。snapshot は plugin ID と source URL の SHA-256 key ごとに保持し、raw source URL を namespace key として保存しない。URL 表記が変われば別 feed である。

v1（version field のない旧形を含む）は次の update check で `legacy_records` に保存して移行する。旧 record と candidate の URL と content ID が完全一致するときだけ初回比較に使い、legacy record だけを根拠に `REMOVED` を出さない。新 snapshot を保存した次回から通常の removal 判定を行う。未来 schema または破損 state は上書きせず `UpdateStateError` を送出する。

同 profile の本 application process は `state/updates.lock` を共有し、read/compare/atomic write 全体を直列化する。default lock timeout は 30 秒で、timeout は update operation 全体を失敗させる。異なる feed の変更は両方保持する。lock file は残り、NAS と外部 application の変更は保証しない。

## Observability and notification

chapter log は image URL、response URL、status、stage、transport、reason code、safe exception detail を記録する。`completed`、`response_received`、`response_limit_exceeded`、`redirect_rejected`、`failed` は取得段階を区別する。image-level fetch/process/save failure は対応する notification category を一度だけ送る。operation-level authentication/configuration/plugin/update/storage/unknown failure は対応する category に送る。

observer、notification sender、sink、Python log capture の cleanup failure は safe diagnostic warning に留め、主 operation の result/exception を変更しない。互換性のため定義される event でも、core に明確な判定点がなければ自動発火しない。設計上の理由は [observability explanation](../explanation/observability.md) を参照する。
