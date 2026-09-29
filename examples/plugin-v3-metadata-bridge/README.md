# Metadata-aware site / processor plugin pair

This example complements the signed sources in `plugin-sources/`.  It is an
authoring example, so it deliberately does not include `manifest.json`: do not
commit a private signing key just to make a sample installable.  After reviewing
the source, sign each package with a user-controlled key as described below.

It demonstrates two related v3 contracts:

1. `site-plugin/metadata_bridge_gallery.py` puts non-secret `variant` and
   `orientation` values on each `ImageResource`.  It uses `variant` when it
   creates the actual image request, and uses `orientation` in the site's
   `transform_image()` hook to rotate the downloaded image.
2. The same request hook returns an `ImageFetchRequest` with small,
   non-secret `plugin_data`.  `processor-plugin/metadata_bridge_processor.py`
   reads that data from `TransformContext.transport_metadata` only after the
   user explicitly permits the site/processor pair.

The example API is intentionally fictitious.  `GET
https://metadata-bridge.example.test/api/works/{work_id}` returns a JSON object
with `id`, `title`, and an `images` array.  Each image has a non-secret `id`,
`variant` (`full` or `preview`), and EXIF-style `orientation` (`1`, `3`, `6`,
or `8`).  The actual image endpoint is
`/api/images/{image_id}/download?variant={variant}`.

## Sign and install

Install the project first so the signing script can import the local package.
Choose a protected location for your private Ed25519 key; it must not be placed
in this repository.

```powershell
python -m pip install -e ".[dev]"
$signingKey = "C:\secure\image-downloader-plugin.pem"

python tools/sign_local_site_plugin.py --key $signingKey --create-key `
  .\examples\plugin-v3-metadata-bridge\site-plugin
python tools/sign_local_site_plugin.py --key $signingKey `
  .\examples\plugin-v3-metadata-bridge\processor-plugin

image-downloader plugin install (Resolve-Path .\examples\plugin-v3-metadata-bridge\site-plugin)
image-downloader plugin install (Resolve-Path .\examples\plugin-v3-metadata-bridge\processor-plugin)
```

Use `--create-key` only once.  On later edits, omit it and re-sign both
packages before installing updated versions.

## Required application configuration

The bridge processor sees raw `plugin_data` only when both packages are enabled
and this exact pair is on the persistent allow-list:

```yaml
plugin_settings:
  local.image-downloader.metadata-bridge-gallery:
    enabled: true
  local.image-downloader.metadata-bridge-processor:
    enabled: true

image_processors:
  chain:
    - local.image-downloader.metadata-bridge-processor
  transport_metadata_access:
    local.image-downloader.metadata-bridge-processor:
      - local.image-downloader.metadata-bridge-gallery
```

Without the allow-list, the processor still runs but receives a redacted
snapshot and records that it did not apply the profile.  The sample never
copies `plugin_data` into an artifact, result, event, or log.  Treat both
`ImageResource.metadata` and `plugin_data` as non-secret values: never put
passwords, cookies, access tokens, signing URLs, or keys in either mapping.
