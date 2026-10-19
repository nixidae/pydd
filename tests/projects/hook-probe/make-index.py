"""Write `index/`: a build backend, the package its hook asks for, and an archive it builds.

Run from this directory, then `uv lock`. `hookprobe_backend` declares no
dependency. Its `get_requires_for_build_wheel` asks for `hookprobe-extra`, and
its `build_wheel` imports that package, so the archive builds only when a
frontend calls the hook (PEP 517, nixidae/nixidae#847).
"""

from __future__ import annotations

import base64
import gzip
import hashlib
import io
import tarfile
import zipfile
from pathlib import Path

INDEX = Path(__file__).parent / "index"
EPOCH = (2020, 1, 1, 0, 0, 0)

BACKEND = '''\
"""A PEP 517 backend whose only build requirement arrives through the hook."""

import base64
import hashlib
import tomllib
import zipfile
from pathlib import Path


def get_requires_for_build_wheel(config_settings=None):
    return ["hookprobe-extra"]


def get_requires_for_build_sdist(config_settings=None):
    return []


def _hash(data):
    return "sha256=" + base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=").decode()


def build_wheel(wheel_directory, config_settings=None, metadata_directory=None):
    import hookprobe_extra

    project = tomllib.loads(Path("pyproject.toml").read_text())["project"]
    name, version = project["name"].replace("-", "_"), project["version"]
    dist_info = f"{name}-{version}.dist-info"
    files = {
        f"{name}.py": f"HOOK = {hookprobe_extra.SAID!r}\\n".encode(),
        f"{dist_info}/METADATA": f"Metadata-Version: 2.1\\nName: {project['name']}\\nVersion: {version}\\n".encode(),
        f"{dist_info}/WHEEL": b"Wheel-Version: 1.0\\nRoot-Is-Purelib: true\\nTag: py3-none-any\\n",
    }
    record = [f"{path},{_hash(data)},{len(data)}" for path, data in sorted(files.items())]
    files[f"{dist_info}/RECORD"] = ("\\n".join([*record, f"{dist_info}/RECORD,,"]) + "\\n").encode()
    filename = f"{name}-{version}-py3-none-any.whl"
    with zipfile.ZipFile(Path(wheel_directory) / filename, "w") as wheel:
        for path, data in sorted(files.items()):
            info = zipfile.ZipInfo(path, (2020, 1, 1, 0, 0, 0))
            info.external_attr = 0o100644 << 16
            wheel.writestr(info, data)
    return filename
'''


def record_hash(data: bytes) -> str:
    digest = base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=").decode()
    return f"sha256={digest}"


def write_wheel(name: str, files: dict[str, str]) -> None:
    dist_info = f"{name}-1.0.0.dist-info"
    staged = {path: text.encode() for path, text in files.items()}
    metadata = f"Metadata-Version: 2.1\nName: {name.replace('_', '-')}\nVersion: 1.0.0\n"
    staged[f"{dist_info}/METADATA"] = metadata.encode()
    staged[f"{dist_info}/WHEEL"] = (
        b"Wheel-Version: 1.0\nGenerator: pydd make-index.py\nRoot-Is-Purelib: true\nTag: py3-none-any\n"
    )
    record = [f"{path},{record_hash(data)},{len(data)}" for path, data in sorted(staged.items())]
    staged[f"{dist_info}/RECORD"] = ("\n".join([*record, f"{dist_info}/RECORD,,"]) + "\n").encode()
    with zipfile.ZipFile(INDEX / f"{name}-1.0.0-py3-none-any.whl", "w", zipfile.ZIP_DEFLATED) as wheel:
        for path, data in sorted(staged.items()):
            info = zipfile.ZipInfo(path, EPOCH)
            info.external_attr = 0o100644 << 16
            wheel.writestr(info, data)


def write_sdist(name: str, files: dict[str, str]) -> None:
    root = f"{name}-1.0.0"
    # `w:gz` writes the time of the run into the gzip header.
    with (
        gzip.GzipFile(INDEX / f"{root}.tar.gz", "wb", mtime=0) as compressed,
        tarfile.open(fileobj=compressed, mode="w", format=tarfile.PAX_FORMAT) as archive,
    ):
        for path, text in sorted(files.items()):
            data = text.encode()
            info = tarfile.TarInfo(f"{root}/{path}")
            info.size, info.mode, info.mtime = len(data), 0o644, 1577836800
            archive.addfile(info, io.BytesIO(data))


if __name__ == "__main__":
    INDEX.mkdir(exist_ok=True)
    write_wheel("hookprobe_backend", {"hookprobe_backend.py": BACKEND})
    write_wheel("hookprobe_extra", {"hookprobe_extra.py": 'SAID = "extra"\n'})
    write_sdist(
        "hookprobe_app",
        {
            "pyproject.toml": (
                '[build-system]\nrequires = ["hookprobe-backend"]\nbuild-backend = "hookprobe_backend"\n\n'
                '[project]\nname = "hookprobe-app"\nversion = "1.0.0"\n'
            ),
        },
    )
