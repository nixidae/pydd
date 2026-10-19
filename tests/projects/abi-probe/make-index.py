"""Write the two wheels of `index/`: one per ABI of CPython 3.13.

Run from this directory, then `uv lock`. Each wheel says which ABI it was
chosen for, so the check reads the choice from inside the environment.
"""

from __future__ import annotations

import base64
import hashlib
import zipfile
from pathlib import Path

INDEX = Path(__file__).parent / "index"
EPOCH = (2020, 1, 1, 0, 0, 0)


def record_hash(data: bytes) -> str:
    digest = base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=").decode()
    return f"sha256={digest}"


def write_wheel(abi: str, says: str) -> None:
    tag = f"cp313-{abi}-linux_x86_64"
    dist_info = "abiprobe-1.0.0.dist-info"
    files = {
        "abiprobe.py": f"ABI = {says!r}\n",
        f"{dist_info}/METADATA": "Metadata-Version: 2.1\nName: abiprobe\nVersion: 1.0.0\n",
        f"{dist_info}/WHEEL": f"Wheel-Version: 1.0\nGenerator: pydd make-index.py\nRoot-Is-Purelib: false\nTag: {tag}\n",
    }
    record = [f"{path},{record_hash(text.encode())},{len(text.encode())}" for path, text in sorted(files.items())]
    files[f"{dist_info}/RECORD"] = "\n".join([*record, f"{dist_info}/RECORD,,"]) + "\n"

    INDEX.mkdir(exist_ok=True)
    with zipfile.ZipFile(INDEX / f"abiprobe-1.0.0-{tag}.whl", "w", zipfile.ZIP_DEFLATED) as wheel:
        for path, text in sorted(files.items()):
            info = zipfile.ZipInfo(path, EPOCH)
            info.external_attr = 0o100644 << 16
            wheel.writestr(info, text)


if __name__ == "__main__":
    write_wheel("cp313", "gil")
    write_wheel("cp313t", "free-threaded")
