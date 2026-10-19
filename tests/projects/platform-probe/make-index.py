"""Write the wheels of `index/`: one per Linux platform tag, each naming itself.

Run from this directory, then `uv lock`. On glibc 2.44 the closest fit is
`manylinux_2_40`: `2_99` needs a newer glibc, `2_17` fits worse, and a
`musllinux` wheel does not run on glibc at all.
"""

from __future__ import annotations

import base64
import hashlib
import zipfile
from pathlib import Path

INDEX = Path(__file__).parent / "index"
EPOCH = (2020, 1, 1, 0, 0, 0)
PLATFORMS = ("manylinux_2_17", "manylinux_2_40", "manylinux_2_99", "musllinux_1_2")


def record_hash(data: bytes) -> str:
    digest = base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=").decode()
    return f"sha256={digest}"


def write_wheel(platform: str) -> None:
    tag = f"py3-none-{platform}_x86_64"
    dist_info = "platformprobe-1.0.0.dist-info"
    files = {
        "platformprobe.py": f"PLATFORM = {platform!r}\n",
        f"{dist_info}/METADATA": "Metadata-Version: 2.1\nName: platformprobe\nVersion: 1.0.0\n",
        f"{dist_info}/WHEEL": (
            f"Wheel-Version: 1.0\nGenerator: pydd make-index.py\nRoot-Is-Purelib: false\nTag: {tag}\n"
        ),
    }
    record = [f"{path},{record_hash(text.encode())},{len(text.encode())}" for path, text in sorted(files.items())]
    files[f"{dist_info}/RECORD"] = "\n".join([*record, f"{dist_info}/RECORD,,"]) + "\n"

    INDEX.mkdir(exist_ok=True)
    with zipfile.ZipFile(INDEX / f"platformprobe-1.0.0-{tag}.whl", "w", zipfile.ZIP_DEFLATED) as wheel:
        for path, text in sorted(files.items()):
            info = zipfile.ZipInfo(path, EPOCH)
            info.external_attr = 0o100644 << 16
            wheel.writestr(info, text)


if __name__ == "__main__":
    for platform in PLATFORMS:
        write_wheel(platform)
