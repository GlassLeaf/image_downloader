"""Compatibility entry point for the shared API v3 plugin signing implementation."""
from image_downloader.devtools.signing import (
    canonical_json as canonical_json,
)
from image_downloader.devtools.signing import (
    file_tree as file_tree,
)
from image_downloader.devtools.signing import (
    load_key as load_key,
)
from image_downloader.devtools.signing import (
    main as main,
)
from image_downloader.devtools.signing import (
    sha256 as sha256,
)
from image_downloader.devtools.signing import (
    sign as sign,
)

if __name__ == "__main__":
    main()
