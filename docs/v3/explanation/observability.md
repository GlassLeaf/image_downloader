# Observability explanation

ログ、event、notification は primary result channel ではない。image processing の成功/失敗は `DownloadResult` と `ImageOutcome`、operation failure は exception/CLI exit status で判断する。

image-level failure は fetch/process/save の stage に対応する notification を一度だけ送る。operation-level failure は authentication/configuration/plugin/update/storage/runtime category に分かれる。`NotificationCategory` に含まれても `auth_login_success` や cookie/credential store access のように core に明確な判定点がない event は自動発火しない。これは plugin が authentication request を出しただけで login 成功を偽装しないためである。

observer、notification sender、sink、Python log capture の failure は主処理を隠さない。safe diagnostic warning を残して cleanup を続行する。通常診断・observer のURL、path、exception は redaction boundary を通り、secret を raw のまま送らない。

workflow専用記録には別の保存規則がある。対象ページの原文URLを`logs/workflow`の取得予定・逐次ログに残すため、
上記の通常診断ログの伏字規則とは区別する。予定保存失敗はその周回の取得を止めるが、逐次ログの失敗は
警告して取得を継続する。保存先・中断時の記録と制限は[Logging reference](../reference/logging.md#workflow-execution-files)を参照する。
