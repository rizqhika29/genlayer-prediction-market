"""Write .env for the wallet network.

The client role is the wallet in ACCOUNT_PRIVATE_KEY_1. The freelancer and the
outsider roles are deterministic child keys derived from it, so a single secret
is enough to run the three-party integration and adversarial tests, and the
same addresses come back on every run.
"""

import hashlib
import os
import pathlib

base = os.environ["KEY"].strip()
if not base.startswith("0x"):
    base = "0x" + base

lines = ["ACCOUNT_PRIVATE_KEY_1=" + base]
for index in (2, 3):
    digest = hashlib.sha256((base + "|party|" + str(index)).encode()).hexdigest()
    lines.append("ACCOUNT_PRIVATE_KEY_%d=0x%s" % (index, digest))

pathlib.Path(".env").write_text("\n".join(lines) + "\n")
