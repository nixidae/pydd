"""Write the wheels of `index/`, each one exercising a corner of the wheel format.

Run from this directory, then `uv lock`:

    python3 make-index.py

The wheels are written by hand, not by a backend, so each one holds exactly
the feature it tests. A fixed timestamp keeps the bytes the same on every run.

- `datafull`: `.data/data`, `.data/headers`, `.data/scripts` with `#!python`,
  one of them without a Unix mode as a Windows build writes it, a console
  script and a GUI script.
- `nsone` and `nstwo`: two halves of the PEP 420 namespace `nsjoint`.
- `pthful`: a `.pth` file with an `import` line, which `site` runs at start.
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


def write_wheel(name: str, version: str, files: dict[str, str], entry_points: str = "") -> None:
    dist_info = f"{name}-{version}.dist-info"
    files = dict(files)
    files[f"{dist_info}/METADATA"] = f"Metadata-Version: 2.1\nName: {name}\nVersion: {version}\n"
    files[f"{dist_info}/WHEEL"] = (
        "Wheel-Version: 1.0\nGenerator: pydd make-index.py\nRoot-Is-Purelib: true\nTag: py3-none-any\n"
    )
    if entry_points:
        files[f"{dist_info}/entry_points.txt"] = entry_points
    record = [f"{path},{record_hash(text.encode())},{len(text.encode())}" for path, text in sorted(files.items())]
    files[f"{dist_info}/RECORD"] = "\n".join([*record, f"{dist_info}/RECORD,,"]) + "\n"

    INDEX.mkdir(exist_ok=True)
    with zipfile.ZipFile(INDEX / f"{name}-{version}-py3-none-any.whl", "w", zipfile.ZIP_DEFLATED) as wheel:
        for path, text in sorted(files.items()):
            info = zipfile.ZipInfo(path, EPOCH)
            if path.endswith("-windows"):
                # A wheel made on Windows carries no Unix mode, so `installer`
                # writes the script without +x.
                info.create_system = 0
            else:
                # `installer` reads +x only with the regular-file type bits.
                info.external_attr = (0o100755 if "/scripts/" in path else 0o100644) << 16
            wheel.writestr(info, text)


def main() -> None:
    write_wheel(
        "datafull",
        "1.0.0",
        {
            "datafull/__init__.py": "GREETING = 'hello'\n\ndef main():\n    print(GREETING)\n",
            "datafull-1.0.0.data/data/share/datafull/greeting.txt": "hello from share\n",
            "datafull-1.0.0.data/headers/datafull.h": "#define DATAFULL 1\n",
            "datafull-1.0.0.data/scripts/datafull-raw": "#!python\nimport datafull\nprint(datafull.GREETING)\n",
            "datafull-1.0.0.data/scripts/datafull-windows": "#!python\nimport datafull\nprint(datafull.GREETING)\n",
        },
        entry_points="[console_scripts]\ndatafull-cli = datafull:main\n\n[gui_scripts]\ndatafull-gui = datafull:main\n",
    )
    write_wheel("nsone", "1.0.0", {"nsjoint/one.py": "NAME = 'one'\n"})
    write_wheel("nstwo", "1.0.0", {"nsjoint/two.py": "NAME = 'two'\n"})
    write_wheel(
        "pthful",
        "1.0.0",
        {
            "pthful_hook.py": "import builtins\nbuiltins.PTHFUL_RAN = True\n",
            "pthful.pth": "import pthful_hook\n",
        },
    )


if __name__ == "__main__":
    main()
