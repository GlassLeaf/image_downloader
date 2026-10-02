# CLI引数とエラー文面の調査（2026-10-02）

**8,794ケースを実行し、引数の組み合わせ・順序、想定される説明、実際の文面を一覧化した。** 主要因は、未定義の単語をdownloadへ補完する処理、例外の具体的な理由を定型文へ置換する処理、トップレベルとサブパーサーで分かれたオプション検証である。アプリケーションの実装変更は行っていない。

- [全件比較CSV](argument-comparison.csv)：引数、改善後に想定する文面、実際の文面、終了コード、内部理由、stdout/stderr全文。UTF-8 BOM付き。
- [全件比較Markdown](all-cases.md)：全行の比較表。
- [生データJSON](raw-results.json)：引数配列、解析結果、内部理由、stdout/stderrの全文。
- [別プロセスのモジュール実行結果](module-results.json)／[環境・件数・パーサー一覧](metadata.json)。

## 調査範囲と実行条件

ソースはコミット `21decb6cbac7eb61329c5cf0e0f19661ffe466bd`。Windows / Python 3.11.4。リポジトリの `.runtime/quality-venv/Scripts/python.exe` を使用し、`PYTHONPATH=src` で現在のソースを読み込んだ。通常の `py` では当初このリポジトリのパッケージが見つからなかったため、一覧の `py -m image_downloader` はユーザーの入力に合わせた同等コマンドの表記であり、実測のPython実行ファイルはmetadataに記録している。

| 対象 | 範囲 |
| --- | --- |
| コマンド | download / workflow / inspect / doctor / config / plugin / cookie / state の8種、21の代表的な操作形式 |
| オプション | ヘルプ以外のトップレベル35オプション全て。--help / -h、省略指定も追加 |
| 順序 | コマンドの前、コマンドと引数の間、引数の後。代表的な多段サブコマンドの途中も追加 |
| 組み合わせ | --json以外の34オプションの全561組を両順序で指定。JSON有無を追加。代表的な排他指定はパーサー階層をまたぐ配置も検証 |
| 不正入力 | 未知のオプション・コマンド、必須値不足、余剰引数、選択値違反、数値形式・範囲、相対パス、存在しないファイル、不正YAML/JSON、空値、=形式、重複指定、--区切り |
| 結果 | 8,614件はCLI mainを実行。180件はCookie操作直前までの検証に限定 |
| 照合 | 代表28件を別プロセスの `python -m image_downloader` で実行し、stdout・stderr・終了コードが28/28件一致 |
| 内部理由 | 固定文面へ置換される前の具体的な理由を448種類記録（パス・値が違う理由を含む） |

実際のユーザー設定・保存データ・プラグインには触れず、platformdirsの `WIN_PD_OVERRIDE_LOCAL_APPDATA` / `WIN_PD_OVERRIDE_APPDATA` を作業用ディレクトリへ向けた。正常な最小設定と空のプラグイン領域を用意し、外部サイトの取得が発生しない `probe-invalid-url` を通常のURL引数に使った。正しいHTTP(S) URLを使う追加試験ではgeneric fallbackを無効化した。したがって対象プラグインが無い実行時エラーは、引数の解析失敗と区別する必要がある。

Cookieはオプション検証と設定解決を通過した時点で調査用の停止を入れた。Cookie読み書き・ブラウザCookieの取得・パスフレーズ入力は実行していない。これらの行の終了コードは未測定で、成功したと断定しない。設定の作成・プラグイン管理の試験は作業用領域に限定した。

引数値・オプション数・重複回数・未知の単語は無限に存在するため、完全な全順列の列挙ではない。全オプションとペアを基礎に、意味の異なる検証経路・配置を広く調べた。外部通信、実プラグインのインストール成功、Cookie操作の成功、ダウンロード成功などの後続処理の文面は今回の対象外である。

## 比較表の読み方

「本来想定される文面」は、原因の特定を助ける**改善案**であり、決定済みの新仕様ではない。コマンド適用範囲・排他指定は [CLI reference](../../v3/reference/cli.md) を基礎にし、既存の具体的な検証理由から復元した説明には、CSVの「期待文面の根拠」列でその旨を示した。内部例外を使った説明の復元は独立した仕様テストではない。複数の誤りがある場合の優先順は今後の修正時に決める必要がある。

以下は代表例。共通の `py -m image_downloader` は省略。`@MISSING@` は作業用の存在しない絶対パス、`@bad-*.yaml@` は作業用の不正設定ファイル。実際に渡したパスはCSV/JSONに記録している。stderrにはエラー以外にusageや4行の設定診断が付く場合があり、表ではエラー部分を中心に示す。

### 提示された例と基本動作

| ID | 引数 | 本来想定される文面・動作（改善案） | 実際に返る文面・動作 | 終了コード |
| --- | --- | --- | --- | --- |
| C00001 | `(引数なし)` | 引数エラー: URLまたはコマンドが必要です。--help を参照してください | error [configuration_error]: configuration is invalid | 2 |
| C00002 | `--list` | 入力・設定エラー: URL or command is required | error [configuration_error]: configuration is invalid | 2 |
| C00003 | `--unknown-option` | 引数エラー: unrecognized arguments: --unknown-option | __main__.py: error: unrecognized arguments: --unknown-option | 2 |
| C00004 | `help` | 引数エラー: 未定義のコマンド／HTTP(S) URLではない値 'help'（help は --help の別名にする案も可） | error [plugin_error]: plugin operation failed | 4 |
| C00005 | `help plugin` | 引数エラー: 未定義のコマンド／HTTP(S) URLではない値 'help'（help は --help の別名にする案も可） | __main__.py: error: unrecognized arguments: plugin | 2 |
| C00010 | `--help` | ヘルプを表示して正常終了（0） | ヘルプをstdoutに表示（全文はstdout列／raw-results.json） | 0 |
| C04312 | `--json --unknown-option` | 引数エラー: unrecognized arguments: --unknown-option | {"error":{"code":"configuration_error","reason":"configuration is invalid","exception":"ConfigurationError","message":"configuration is invalid","operation":"download"}} | 2 |
| C04310 | `--json --list` | 入力・設定エラー: URL or command is required | {"error":{"code":"configuration_error","reason":"configuration is invalid","exception":"ConfigurationError","message":"configuration is invalid","operation":"download"}} | 2 |
| C04314 | `--json help` | 引数エラー: 未定義のコマンド／HTTP(S) URLではない値 'help'（help は --help の別名にする案も可） | {"error":{"code":"plugin_error","reason":"plugin operation failed","exception":"PluginError","message":"plugin operation failed","operation":"download"}} | 4 |

### 必須引数・不正値・サブコマンド

| ID | 引数 | 本来想定される文面・動作（改善案） | 実際に返る文面・動作 | 終了コード |
| --- | --- | --- | --- | --- |
| C00016 | `download` | 引数エラー: the following arguments are required: url | __main__.py download: error: the following arguments are required: url | 2 |
| C04338 | `--json download` | 引数エラー: the following arguments are required: url | {"error":{"code":"configuration_error","reason":"configuration is invalid","exception":"ConfigurationError","message":"configuration is invalid","operation":"download"}} | 2 |
| C02563 | `--config` | 引数エラー: argument --config: expected one argument | __main__.py: error: argument --config: expected one argument | 2 |
| C02824 | `download probe-invalid-url --image-format GIF` | 引数エラー: argument --image-format: invalid choice: 'GIF' (choose from 'ORIGINAL', 'JPEG', 'PNG', 'WEBP') | __main__.py download: error: argument --image-format: invalid choice: 'GIF' (choose from 'ORIGINAL', 'JPEG', 'PNG', 'WEBP') | 2 |
| C02856 | `workflow probe-invalid-url --workflow-retries -1` | 入力・設定エラー: workflow retries and delay must be non-negative and finite | error [configuration_error]: configuration is invalid | 2 |
| C00019 | `plugin` | 入力・設定エラー: plugin command is required: install, trust, revoke, uninstall, or list | error [configuration_error]: configuration is invalid | 2 |
| C04252 | `plugin unknown` | 入力・設定エラー: unknown plugin command | error [configuration_error]: configuration is invalid | 2 |
| C04254 | `plugin install` | 入力・設定エラー: plugin install requires one absolute plugin directory | error [configuration_error]: configuration is invalid | 2 |
| C04243 | `config unknown` | 入力・設定エラー: config command is 'config path', 'config explain [--host HOST]', 'config init [ABSOLUTE_PATH]', or 'config profile init NAME' | error [configuration_error]: configuration is invalid | 2 |
| C04260 | `cookie unknown` | 引数エラー: argument cookie_action: invalid choice: 'unknown' (choose from 'export', 'import', 'browser-import') | __main__.py cookie: error: argument cookie_action: invalid choice: 'unknown' (choose from 'export', 'import', 'browser-import') | 2 |
| C04266 | `state workflow unknown` | 入力・設定エラー: unknown workflow state action | error [configuration_error]: configuration is invalid | 2 |

### オプションの位置による違い

| ID | 引数 | 本来想定される文面・動作（改善案） | 実際に返る文面・動作 | 終了コード |
| --- | --- | --- | --- | --- |
| C01039 | `--host example.test plugin list` | 引数エラー: --host はこのコマンドでは指定できません | error [configuration_error]: configuration is invalid | 2 |
| C01040 | `plugin list --host example.test` | 引数エラー: unrecognized arguments: --host example.test | __main__.py: error: unrecognized arguments: --host example.test | 2 |
| C01000 | `--inspect-only plugin list` | 引数エラー: --inspect-only はこのコマンドでは指定できません | 診断ログのみ（stderr全文を参照） | 0 |
| C01001 | `plugin list --inspect-only` | 引数エラー: unrecognized arguments: --inspect-only | __main__.py: error: unrecognized arguments: --inspect-only | 2 |
| C04306 | `--selection-priority 0 config path` | 引数エラー: --selection-priority はこのコマンドでは指定できません | user configuration: C:\Users\pc01\source\repos\image-downloader\.runtime\cli-argument-audit\run-1790949042285171100\local\image-downloader\conf\app.yaml<br>exists: True<br>package baseline: C:\Users\pc01\source\repos\image-downloader\src\image_downloader\app.yaml<br>automatic data root: C:\Users\pc01\source\repos\image-downloader\.runtime\cli-argument-audit\run-1790949042285171100\local\image-downloader<br>automatic plugin root: C:\Users\pc01\source\repos\image-downloader\.runtime\cli-argument-audit\run-1790949042285171100\local\image-downloader\plugins | 0 |
| C00546 | `--selection-priority 1 config path` | 引数エラー: --selection-priority はこのコマンドでは指定できません | error [configuration_error]: configuration is invalid | 2 |
| C08746 | `config profile --json init invalid/name` | 引数エラー: unrecognized arguments: init invalid/name | {"error":{"code":"configuration_error","reason":"configuration is invalid","exception":"ConfigurationError","message":"configuration is invalid","operation":"config"}} | 2 |
| C00856 | `config --json profile init invalid/name` | 入力・設定エラー: profile name must contain only letters, numbers, '_' or '-' | {"error":{"code":"configuration_error","reason":"configuration is invalid","exception":"ConfigurationError","message":"configuration is invalid","operation":"config"}} | 2 |
| C08748 | `plugin install --yes @MISSING@` | 引数エラー: unrecognized arguments: C:\Users\pc01\source\repos\image-downloader\.runtime\cli-argument-audit\run-1790949510554144900\missing | __main__.py: error: unrecognized arguments: C:\Users\pc01\source\repos\image-downloader\.runtime\cli-argument-audit\run-1790949510554144900\missing | 2 |

### 排他的オプションを同じ階層／別の階層へ置く

| ID | 引数 | 本来想定される文面・動作（改善案） | 実際に返る文面・動作 | 終了コード |
| --- | --- | --- | --- | --- |
| C04093 | `download probe-invalid-url --image-format PNG --force-image-format JPEG` | 引数エラー: argument --force-image-format: not allowed with argument --image-format | __main__.py download: error: argument --force-image-format: not allowed with argument --image-format | 2 |
| C04094 | `--image-format PNG download probe-invalid-url --force-image-format JPEG` | 引数エラー: --image-format と --force-image-format は同時に指定できません | error [plugin_error]: plugin operation failed | 4 |
| C04096 | `--force-image-format JPEG download probe-invalid-url --image-format PNG` | 引数エラー: --image-format と --force-image-format は同時に指定できません | error [plugin_error]: plugin operation failed | 4 |
| C04133 | `--image-format PNG workflow probe-invalid-url --force-image-format JPEG` | 引数エラー: --image-format と --force-image-format は同時に指定できません | error [configuration_error]: configuration is invalid | 2 |
| C04098 | `--plugin com.example.missing download probe-invalid-url --force-plugin com.example.missing` | 引数エラー: --plugin と --force-plugin は同時に指定できません | error [configuration_error]: configuration is invalid | 2 |
| C04102 | `--inspect-only download probe-invalid-url --list-updated-urls` | 引数エラー: --list-updated-urls はこのコマンドでは指定できません | error [configuration_error]: configuration is invalid | 2 |
| C04239 | `--export-cookies @MISSING@ --import-cookies @MISSING@` | 入力・設定エラー: select only one cookie import or export operation | error [configuration_error]: configuration is invalid | 2 |

### configuration_errorの具体的な原因

| ID | 引数 | 本来想定される文面・動作（改善案） | 実際に返る文面・動作 | 終了コード |
| --- | --- | --- | --- | --- |
| C08742 | `--config relative.yaml doctor` | 入力・設定エラー: --config must be an absolute path | error [configuration_error]: configuration is invalid | 2 |
| C02891 | `--config @MISSING@ download probe-invalid-url` | 入力・設定エラー: configuration file not found: C:\Users\pc01\source\repos\image-downloader\.runtime\cli-argument-audit\run-1790949042388841900\missing; create it with 'config init "C:\Users\pc01\source\repos\image-downloader\.runtime\cli-argument-audit\run-1790949042388841900\missing"' | error [configuration_error]: configuration is invalid | 2 |
| C02893 | `--config @bad-yaml.yaml@ download probe-invalid-url` | 入力・設定エラー: could not read configuration: C:\Users\pc01\source\repos\image-downloader\.runtime\cli-argument-audit\run-1790949042388841900\bad-yaml.yaml: while parsing a flow sequence<br>  in "<unicode string>", line 1, column 1:<br>    [invalid<br>    ^<br>expected ',' or ']', but got '<stream end>'<br>  in "<unicode string>", line 1, column 9:<br>    [invalid<br>            ^ | error [configuration_error]: configuration is invalid | 2 |
| C02897 | `--config @bad-field.yaml@ download probe-invalid-url` | 入力・設定エラー: invalid configuration in app layer C:\Users\pc01\source\repos\image-downloader\.runtime\cli-argument-audit\run-1790949042388841900\bad-field.yaml: network.request_concurrency: Input should be greater than or equal to 1 | error [configuration_error]: configuration is invalid | 2 |
| C02933 | `--plugin-config missing-equals download probe-invalid-url` | 入力・設定エラー: --plugin-config must have ID=<JSON-object> form | error [configuration_error]: configuration is invalid | 2 |
| C02935 | `--plugin-config id={ download probe-invalid-url` | 入力・設定エラー: --plugin-config JSON is invalid | error [configuration_error]: configuration is invalid | 2 |
| C02911 | `--output-dir relative download probe-invalid-url` | 入力・設定エラー: --output-dir must be an absolute path | error [configuration_error]: configuration is invalid | 2 |

### 省略記法・ヘルプ優先度・正しいURLでの後続エラー

| ID | 引数 | 本来想定される文面・動作（改善案） | 実際に返る文面・動作 | 終了コード |
| --- | --- | --- | --- | --- |
| C04273 | `--lis` | 入力・設定エラー: URL or command is required | error [configuration_error]: configuration is invalid | 2 |
| C04279 | `--plugin-c` | 引数エラー: ambiguous option: --plugin-c could match --plugin-config, --plugin-config-file | __main__.py: error: ambiguous option: --plugin-c could match --plugin-config, --plugin-config-file | 2 |
| C04276 | `--conf dummy doctor` | 引数エラー: argument command: invalid choice: 'dummy' (choose from 'download', 'workflow', 'inspect', 'doctor', 'config', 'plugin', 'cookie', 'state') | __main__.py: error: argument command: invalid choice: 'dummy' (choose from 'download', 'workflow', 'inspect', 'doctor', 'config', 'plugin', 'cookie', 'state') | 2 |
| C08744 | `--j --unknown-option` | 引数エラー: unrecognized arguments: --unknown-option | __main__.py: error: unrecognized arguments: --unknown-option | 2 |
| C08745 | `--json --j --unknown-option` | 引数エラー: unrecognized arguments: --unknown-option | {"error":{"code":"configuration_error","reason":"configuration is invalid","exception":"ConfigurationError","message":"configuration is invalid","operation":"download"}} | 2 |
| C04294 | `--unknown-option --help` | ヘルプを表示して正常終了（0） | ヘルプをstdoutに表示（全文はstdout列／raw-results.json） | 0 |
| C02568 | `--config --help` | 引数エラー: argument --config: expected one argument | __main__.py: error: argument --config: expected one argument | 2 |
| C08733 | `--json --profile plugin` | 入力・設定エラー: URL or command is required | {"error":{"code":"configuration_error","reason":"configuration is invalid","exception":"ConfigurationError","message":"configuration is invalid","operation":"plugin"}} | 2 |
| C08783 | `download https://example.test/gallery --fallback-generic disabled` | 実行時エラー: no enabled site plugin matched and generic fallback is disabled | error [plugin_error]: plugin operation failed | 4 |

## 原因と修正時に考慮すべき点

1. **`--list` は未知のオプションとして拒否されていない。** argparseの省略記法によって `--list-updated-urls` へ解釈される。URL/commandが無いため `ValueError('URL or command is required')` が発生し、`configuration_error` の定型文へ置換される。省略記法を維持するか、`allow_abbrev=False` にするかを判断する必要がある。

2. **`help` はpluginコマンドへ振り分けられているわけではない。** [_normalize_cli_arguments](../../../src/image_downloader/commands/parser.py) は最初の未定義の非オプション単語の前へ `download` を挿入する。`help` はURLとして渡され、対応プラグインが見つからず `PluginError` になる。`help plugin` は補完後の `download help plugin` の余剰引数 `plugin` として解析エラーになる。未知コマンドを拒否するか、`help` を正式な別名として扱い、裸URL補完をHTTP(S) URLへ限定すると原因が明確になる。

3. **内部には具体的な理由があるが、表示時に消えている。** [error_info_for](../../../src/image_downloader/exceptions.py) は `str(error)` を公開せず、`message` を固定の `reason` と同じにする。さらに [_cli_error_info](../../../src/image_downloader/commands/dispatch.py) は通常のValueErrorをメッセージ無しのConfigurationErrorへ変換する。引数・設定検証の安全な情報を構造化して渡す仕組みが必要で、任意の例外文字列を無条件に表示する変更は既存の機密情報保護の契約と衝突する。

4. **JSONの構文エラー経路だけが定型文へ入る。** [_CliArgumentParser.error](../../../src/image_downloader/commands/parser.py) は `--json` の完全一致があると ConfigurationErrorを投げる。そのため未知オプション・不足値・不正な選択値まで説明を失う。`--j` という省略表記は解析上JSONとして受理されても、構文エラー時のJSON判定には使われず、通常の解析エラーになる。また `_operation_name` は解析前に値として出現した `plugin` などもcommandとして推測しうる。

5. **パーサー階層をまたぐ排他指定は一貫しない。** トップレベルと各サブパーサーが別々にmutually_exclusive_groupを持つため、コマンド前後へ分けると 両値が残る。workflowは後段でも競合を検証するが、downloadやinspectには同等の検証が無い組み合わせがある。配置に依存しない共通の検証が必要である。

6. **非対応オプションの拒否には位置・値による穴がある。** [_reject_command_options](../../../src/image_downloader/commands/validation.py) は明示指定を見ず、既定値との差だけを見る。例えば `--selection-priority 0 config path` は通過し、1なら拒否される。`--inspect-only plugin list` のように、一部ハンドラーではそもそも拒否一覧に無いフラグが無視される。stateは [_explicit_options](../../../src/image_downloader/commands/state.py) を使った別の検証を持つ。多段の自由な位置引数 `nargs='*'` の途中へオプションを入れると残りが余剰引数になるケースもある。

## 再実行

```powershell
.runtime/quality-venv/Scripts/python.exe tools/audit_cli_arguments.py
.runtime/quality-venv/Scripts/python.exe tools/render_cli_argument_audit.py
```

調査スクリプトは実行ごとに新しい作業用領域を作る。初回調査の保存不備を修正した後、全件を2つの独立したプロセスへ分割して再実行し、追加の配置・URL試験を統合した。`metadata.json` の実行時間は最も長い分割実行の秒数であり、調査全体の所要時間ではない。
