# 配布・信頼・運用

## 信頼境界

plugin は manifest、author defaults、catalog pin、署名と verification mode により検証される。<code>strict</code> は検証不能な plugin を拒否し、<code>warn</code> は診断 warning を伴う限定的な読み込み、<code>off</code> は検証を無効化する運用上の例外である。production では strict を使用する。

<code>image_downloader.security</code> の <code>read_manifest</code>、<code>verify_manifest</code>、<code>install_plugin</code>、<code>trust_plugin</code>、<code>revoke_plugin</code>、<code>uninstall_plugin</code> が programmatic 管理 API である。引数、filesystem mutation、戻り値、例外は [完全 API 参照](api-reference.md) に定義する。

## install と catalog

install/trust/revoke/uninstall は plugin root と catalog を変更する管理操作である。実行前に backup、署名の出所、plugin id、verification mode を確認する。mutation が失敗した場合は <code>PluginError</code> または <code>ConfigurationError</code> を扱い、途中の directory を有効 plugin と見なしてはならない。

## 運用ログ

<code>image_downloader.observability.logging</code> は stable API である。<code>DownloadLogger</code> と sink は message、URL、path、例外を安全に整形し、secret を raw で出力しない。<code>safe_log_text</code>、<code>mask_log_text</code>、<code>safe_url</code>、<code>safe_relative_path</code>、<code>safe_exception_name</code> を利用する。

sink の write/close failure は診断目的で抑制される場合がある。logging を download 成否の source of truth にせず、<code>DownloadResult</code>、例外、CLI exit status を使用する。logging API の全 method と concurrency contract は [完全 API 参照](api-reference.md) を参照する。

## doctor

<code>doctor</code> は config source、resolved paths、verification mode、loaded plugin、selection diagnostics を確認する読み取り中心の診断 command である。plugin root、config root、source host を指定して本番実行前に selection と override を検証する。
