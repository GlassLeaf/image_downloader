# Observability explanation

ログ、event、notification は primary result channel ではない。image processing の成功/失敗は `DownloadResult` と `ImageOutcome`、operation failure は exception/CLI exit status で判断する。

image-level failure は fetch/process/save の stage に対応する notification を一度だけ送る。operation-level failure は authentication/configuration/plugin/update/storage/runtime category に分かれる。`NotificationCategory` に含まれても `auth_login_success` や cookie/credential store access のように core に明確な判定点がない event は自動発火しない。これは plugin が authentication request を出しただけで login 成功を偽装しないためである。

observer、notification sender、sink、Python log capture の failure は主処理を隠さない。safe diagnostic warning を残して cleanup を続行する。URL、path、exception は redaction boundary を通り、secret を raw のまま observer/log に送らない。
