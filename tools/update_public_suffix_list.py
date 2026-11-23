"""Refresh the packaged PSL from its official source during release preparation."""

from __future__ import annotations

from io import StringIO
from pathlib import Path
from urllib.request import Request, urlopen

from publicsuffix2 import PublicSuffixList

SOURCE = "https://publicsuffix.org/list/public_suffix_list.dat"
TARGET = Path(__file__).resolve().parents[1] / "src/image_downloader/configuration/public_suffix_list.dat"


def main() -> None:
    request = Request(SOURCE, headers={"User-Agent": "image-downloader-release"})
    with urlopen(request, timeout=30) as response:
        data = response.read(2_000_001)
    if len(data) > 2_000_000:
        raise ValueError("upstream PSL exceeds the expected snapshot size")
    content = data.decode("utf-8")
    required = (
        "// VERSION:",
        "// COMMIT:",
        "Mozilla Public",
        "===BEGIN ICANN DOMAINS===",
        "===END ICANN DOMAINS===",
        "===BEGIN PRIVATE DOMAINS===",
        "===END PRIVATE DOMAINS===",
    )
    if not all(marker in content for marker in required):
        raise ValueError("upstream PSL lacks its license, version or complete domain sections")
    psl = PublicSuffixList(StringIO(content))
    if psl.get_sld("www.example.co.jp", strict=True) != "example.co.jp" or psl.get_tld("github.io") != "github.io":
        raise ValueError("upstream PSL does not preserve expected public and private boundaries")
    temporary = TARGET.with_suffix(".dat.tmp")
    temporary.write_bytes(data)
    temporary.replace(TARGET)
    print(next(line for line in content.splitlines() if line.startswith("// VERSION:")))


if __name__ == "__main__":
    main()
