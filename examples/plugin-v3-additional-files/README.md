# Additional-file callbacks

`plugin.py` is an annotated hook example based on the built-in generic HTML site.
Integrate its methods into a packaged site plugin; this directory is not a signed installable unit.

Configure the site's private `metadata_url` to an HTTP(S) JSON endpoint returning a `key` string.
Before each image request, core retrieves the JSON and invokes the receive callback. The plugin stores
the key by operation-local image instance, then uses it in `create_image_request()` and `ImageFetchRequest.plugin_data`.
Core saves the exact JSON bytes to the chapter's `metadata/<image-index>.json`.
The saved callback receives the actual saved (or existing skipped) data. After normal download,
each chapter gets a `metadata/images.json` summary.

This example intentionally persists the supplied metadata JSON. Choose only content intended for local
storage; use ordinary request/auth hooks for credentials that should never be persisted.
See the [hook contract](../../docs/v3/reference/plugin-hooks.md#optional-additional-files).
