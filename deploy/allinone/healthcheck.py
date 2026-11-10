"""Healthcheck van de alles-in-één image (contract 1.3 §2.2, C1.3-ASF-01).

Docker start ``HEALTHCHECK``- en ``docker exec``-processen als de gebruiker van de image, buiten de procesboom van
PID 1: zonder "Custom User" dus als root, met de standaard-capabilities en zonder no_new_privs. Daarom is de
**eerste** handeling van dit programma de wissel — bij root eerst de bounding set legen (``PR_CAPBSET_DROP``), dan
``setgroups([])``, ``setgid(568)``, ``setuid(568)`` en ``prctl(PR_SET_NO_NEW_PRIVS, 1)`` — en pas daarna de controle
van ``/readyz`` (API antwoordt, database en schema in orde). Mislukt de wissel, dan exit 1 (rood).

Aangeroepen in exec-vorm met ``python -I -S`` (geen shell, geen omgevingsvariabelen van Python, geen site-packages):
het korte venster vóór de wissel (opstarten van de interpreter) is een gemeld restrisico.

``--controle`` (rooktest): na de wissel ``Uid``, ``CapEff``, ``CapPrm``, ``CapAmb``, ``CapBnd`` en ``NoNewPrivs`` als
JSON afdrukken en zonder netwerkcontrole stoppen (exit 0 alleen als de wissel klopt).
"""

import ctypes
import json
import os
import sys
import urllib.request

UID = GID = 568
PR_SET_NO_NEW_PRIVS = 38
PR_CAPBSET_DROP = 24
ZERO = "0000000000000000"


def wissel() -> bool:
    """Bij root: bounding set leeg, volledig naar 568, no_new_privs. Als 568 (Custom User): alleen no_new_privs."""
    try:
        libc = ctypes.CDLL(None, use_errno=True)
        if os.getuid() == 0 or os.geteuid() == 0:
            for cap in range(64):
                if libc.prctl(PR_CAPBSET_DROP, cap, 0, 0, 0) != 0:
                    if cap == 0:
                        return False  # zelfs CAP_CHOWN niet: dan is de bounding set niet te legen
                    break  # einde van de bekende capabilities
            os.setgroups([])
            os.setgid(GID)
            os.setuid(UID)
        if libc.prctl(PR_SET_NO_NEW_PRIVS, 1, 0, 0, 0) != 0:
            return False
    except OSError:
        return False
    return os.getuid() == UID and os.geteuid() == UID and os.getgid() == GID and os.getegid() == GID


def status() -> dict[str, str]:
    with open("/proc/self/status", encoding="ascii") as fh:
        fields = dict(line.split(":", 1) for line in fh.read().splitlines() if ":" in line)
    keys = ("Uid", "Gid", "CapEff", "CapPrm", "CapAmb", "CapBnd", "NoNewPrivs")
    return {k: fields.get(k, "?").split()[0] if k in ("Uid", "Gid") else fields.get(k, "?").strip() for k in keys}


def main() -> int:
    ok = wissel()
    if "--controle" in sys.argv[1:]:
        st = status()
        print(json.dumps({"wissel": ok, **st}))
        clean = st["CapEff"] == st["CapPrm"] == st["CapAmb"] == ZERO and st["NoNewPrivs"] == "1"
        return 0 if ok and st["Uid"] == str(UID) and clean else 1
    if not ok:
        return 1
    port = os.environ.get("APPSTORE_PORT", "30430")
    if not port.isdigit():
        return 1
    # nooit via een proxy uit de omgeving (HTTP_PROXY in het formulier van de Custom App, contract §5): urlopen volgt
    # die anders ook voor 127.0.0.1 en de container wordt onterecht 'unhealthy' (review contract 1.3)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(f"http://127.0.0.1:{port}/readyz", timeout=6) as resp:
            return 0 if resp.status == 200 else 1
    except Exception:
        return 1


if __name__ == "__main__":
    sys.exit(main())
