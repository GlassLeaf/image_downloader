# Release preflight

Release candidate では次を実行し、失敗したら公開を止める。

```powershell
python -m pytest -q --cov=image_downloader
python -m ruff check .
python -m mypy src/image_downloader
python -m pip check
python -m build --sdist --wheel
python tools/release_smoke.py
```

offline environment で依存が既にある場合は `python tools/release_smoke.py --offline` を使えるが、依存解決は検証しない。smoke は wheel/sdist の content、non-editable wheel install、`doctor --json`、bundled signed plugin の strict load を検証する。

- release artifact は検証した wheel/sdist と一致し、`.local`、`.runtime`、profile data、IDE file、history archive を含まない。
- `pyproject.toml` の supported Python と Windows/Linux/macOS matrix の quality/package smoke を確認する。
- project license、third-party/bundled source attribution、private vulnerability reporting の運用を公開前に決める。
- release note に beta/stability、v1/v2 plugin incompatibility、migration、config rewrite/backup、必要なら旧 version より降格する install 手順を記載する。
- strict install/trust/revoke/uninstall/doctor と documentation contract/link check を release candidate で再実行する。
