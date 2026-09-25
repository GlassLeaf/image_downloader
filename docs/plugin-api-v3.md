# Local plugin API v3

v3 is a breaking local-directory plugin API.  It does not read v1/v2 entry
points, wheels, sidecars, or the former configuration tree.  Move those files
manually before enabling v3.

> 詳細な現行文書は [v3 ドキュメント索引](v3/README.md) に分割している。この file は
> 互換性境界と構成を示す短い概要であり、操作手順・公開 API・受入条件の正本ではない。

## Configuration tree

The configuration root contains `app.yaml`, `sites/global.yaml`,
`sites/<host>.yaml`, `profiles/<name>/app.yaml`, and
`profiles/<name>/sites/...`.  Merge order is model defaults, bundled baseline,
main app, profile app, site global, profile site global, then base-domain
through full-host base and profile files, followed by CLI bootstrap and
library-operation overrides. The bundled baseline is applied by both the CLI
and library loader. The profile is selected only by main `app.yaml` or
`--profile`; overlays may not contain `profile`.

DNS names are lowercase IDNA A-labels.  They inherit from the PSL registrable
domain; IP addresses use only their complete filename.  IPv6 files are named
`ipv6-<32-lowercase-hex>.yaml`.  Profile configuration remains under
`profiles/<name>` and profile data is
`<storage.data_root>/profiles/<name>`.

`security.plugin_verification` is allowed only in app and profile-app layers.
Its catalog is always `<plugin-root>/catalog.json`; `plugin_catalog` no longer
exists.  User plugin settings use `plugin_settings.<id>` with `enabled`,
`config`, and (site plugins only) `secrets`.

Site layers provide candidate plugin configuration; they do not bind a hostname
to a plugin ID. A site plugin can optionally implement
`matches_with_config(url, config, app_settings) -> bool` to use its final
private configuration while choosing a host. Core gives this hook only that
plugin's config and redacted safe application settings. Existing plugins keep
using `matches(url)`. The `doctor --host URL` report records both match results,
the winner, and redacted effective configuration.

## Directory unit

The configured `plugins.root` is an absolute local directory; `config init`
offers a platformdirs-based default. The console command and
`python -m image_downloader` use the same configured root. Use an absolute
`--plugin-root` for a one-invocation override.

`output.isolate_by_plugin` defaults to `false` for layout compatibility. With
it enabled, download artifacts go under
`<profile>/downloads/<canonical-host>/<plugin-id>/...`; DNS uses lowercase
IDNA, IPv4 its canonical address, and IPv6 `ipv6-<32hex>`. Cookies, state, and
debug logs remain profile-shared. Existing output leaves must be unlinked
regular files; links, reparse points, and hard links are rejected. `overwrite`
uses an atomic replacement only for such a safe leaf.

```text
plugins/
  catalog.json
  site_plugins/example-gallery/
    manifest.json
    sample_plugin.py
    sample_plugin.yaml
  image_processor_plugins/example-scaler/
    manifest.json
    scaler.source
    scaler.yaml
```

Each direct child is one plugin.  The directory name is arbitrary.  Its author
YAML is mandatory and contains exactly `config: { ... }`.  The private config
seen by a plugin is author defaults, persistent `plugin_settings`, CLI config
files in argument order, inline CLI values, then a library operation override.
Mappings merge deeply; no delete marker exists.

## Manifest and trust

`manifest.json` contains exactly `{"manifest": {...}, "signature": "..."}`.
The inner manifest has schema version 1 and API version `"3"`, and is signed
as canonical JSON with Ed25519.  It declares a reverse-DNS ID/publisher, PEP
440 version, kind, integer `match_priority`, entry file/class, YAML filename,
public key/key ID, opaque `capabilities`, and exact file-tree hashes.  Unknown
fields fail verification.  Links/reparse points fail verification.  Every
regular plugin file other than `manifest.json` and `__pycache__/*.pyc` is in the
tree hash.

`capabilities` is signed opaque metadata in v3.  It is neither interpreted nor
pinned.  Adding active capability semantics requires a future API/schema.

For a consolidated guide to site-plugin HTTP integration, credentials, Cookie
import, AuthFlow, request recovery, concurrency, and unsupported features, see
[サイト plugin 統合・認証ガイド](v3/site-plugin-integration-guide.md).

Use `plugin install <absolute-directory>`, `plugin trust <absolute-directory>`,
`plugin revoke <id>`, `plugin uninstall <id>`, and `plugin list`.  Install stages the copy, validates
hash/signature/class contract, then atomically replaces the directory and
catalog.  Trust/update requires confirmation (or `--yes`).  Same-version hash
changes and publisher/kind changes are rejected; key rotations, downgrades,
revocation recovery, and priority changes require confirmation.

`--plugin-verification-override` is available on every CLI command.  Its
`bypass-all`, `bypass-catalog`, and `bypass-signature` modes respectively skip
all plugin verification, only catalog verification, or only Ed25519 signature
verification while retaining catalog content pins.  See the trust and
operations guide for the minimum-manifest and content-digest rules.

## Runtime API

Both site and processor classes implement synchronous, side-effect-free
`validate_config(config, app_settings) -> None`.  Site lifecycle methods remain
`matches`, `inspect`, request/recovery, `auth_flow`, `transform_image`, and
optional `check_updates`; descriptors are gone.  IDs and priorities belong to
the manifest.

`PluginExecutionContext` exposes immutable `config`, safe final
`app_settings`, `manifest`, optional `catalog`, `secrets`, and `requests`.
Safe app settings include output/media/execution and safe network values, but
exclude headers, proxy, security, notifications, and credentials.  Processor
contexts have their own config/metadata plus selected site manifest/catalog,
never the site's config, secrets, request port, or filesystem.

The core `core.generic-html` fallback has static manifest metadata and no user
plugin settings.  External matches always win.  Use
`fallback.generic_html.enabled` or `--fallback-generic auto|enabled|disabled`.

## Migration

There is no automatic migration or compatibility read.  Recreate each v2
plugin as a signed local v3 directory, move configuration into the tree above,
and copy profile data manually only when wanted.  v2 sidecars and entry points
are deliberately ignored.
