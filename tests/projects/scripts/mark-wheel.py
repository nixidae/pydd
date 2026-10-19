"""Copy a wheel and add one module to it, so a check can tell the copy from the original.

mark-wheel.py <wheel> <package dir> <out>
"""

import base64
import hashlib
import sys
import zipfile

source, package, out = sys.argv[1:]
marker = f"{package}/_pydd_replaced.py"
body = b"REPLACED = True\n"
digest = base64.urlsafe_b64encode(hashlib.sha256(body).digest()).rstrip(b"=").decode()

with zipfile.ZipFile(source) as old, zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as new:
    for item in old.infolist():
        data = old.read(item)
        if item.filename.endswith(".dist-info/RECORD"):
            data += f"{marker},sha256={digest},{len(body)}\n".encode()
        new.writestr(item, data)
    new.writestr(marker, body)
