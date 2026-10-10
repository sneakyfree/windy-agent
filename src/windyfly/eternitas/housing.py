"""Housing-history report to Eternitas (windy-contracts schema/eternitas/housing.v1.json).

At start the agent tells Eternitas which machine it lives on: a stable id for the machine
(hashed here AND again by Eternitas, never a hostname or IP), a class (laptop, desktop, ...)
and a snapshot of facts about it (OS, CPU, GPU, disk, connection, virtual or not, serial).
Eternitas decides what to keep: at most one snapshot per host per 30 days unless a field
changed, nothing at all while its collection setting is off (it still answers 201
``snapshot: "not_collected"``). So there is no timer, no diffing and no retry here.

ON by default (Boss "flip housing", 10-07; Eternitas collection ON + Hub privacy v20 live since
10-07). The owner switch is ``WINDY_HOUSING_REPORT`` (``0`` off, ``1`` on). FAIL-OPEN: one report
per start on a daemon thread, so a slow, hanging or dead Eternitas never blocks or slows the agent
(the caller returns at once; both POSTs together give up after ``_TIMEOUT`` = 3 s). Every fact is best effort and
every field is optional; a failure of any kind is silent (debug log, never the serial). Nothing
here raises into its caller.
"""

from __future__ import annotations

import hashlib
import logging
import os
import platform
import re
import shutil
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path
from typing import Any

import httpx

logger = logging.getLogger(__name__)

DEFAULT_ON = True
AUDIENCE = "windy-eternitas"
_TIMEOUT = 3.0  # Boss: one attempt, <= 3 s in all (both POSTs share this budget)
_PROBE_TIMEOUT = 5.0
# Serial strings that firmware fills in when it has no real number.
_JUNK_SERIALS = frozenset({
    "", "0", "none", "unknown", "default string", "to be filled by o.e.m.", "not specified",
    "system serial number", "not applicable", "n/a", "invalid",
})


def enabled() -> bool:
    raw = os.environ.get("WINDY_HOUSING_REPORT", "").strip().lower()
    if raw in ("0", "false", "off", "no"):
        return False
    if raw in ("1", "true", "on", "yes"):
        return True
    return DEFAULT_ON


def _run(*cmd: str) -> str:
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=_PROBE_TIMEOUT,
                             check=False, stdin=subprocess.DEVNULL)
        return out.stdout.strip() if out.returncode == 0 else ""
    except (OSError, subprocess.SubprocessError):
        return ""


def _read(path: str) -> str:
    try:
        return Path(path).read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return ""


def _os_name() -> str:
    return platform.system().lower() or "other"


# ── the machine's stable id ──────────────────────────────────────────

def _machine_id() -> str:
    system = _os_name()
    if system == "linux":
        return _read("/etc/machine-id") or _read("/var/lib/dbus/machine-id")
    if system == "darwin":
        m = re.search(r'"IOPlatformUUID" = "([^"]+)"', _run("ioreg", "-rd1", "-c", "IOPlatformExpertDevice"))
        return m.group(1) if m else ""
    if sys.platform == "win32":
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Cryptography") as k:
                return str(winreg.QueryValueEx(k, "MachineGuid")[0])
        except Exception:  # noqa: BLE001
            return ""
    return ""


def host_id() -> str:
    """A stable id for THIS machine. Derived from the OS machine id (hashed, so the raw id never
    leaves); with none readable, a random id kept in the state dir."""
    mid = _machine_id()
    if mid:
        return hashlib.sha256(f"windyfly-housing-v1:{mid}".encode()).hexdigest()[:40]
    state = Path(os.environ.get("WINDY_STATE_DIR") or Path.home() / ".windy")
    path = state / "housing_host_id"
    saved = _read(str(path))
    if saved:
        return saved
    fresh = uuid.uuid4().hex
    try:
        state.mkdir(parents=True, exist_ok=True)
        path.write_text(fresh, encoding="utf-8")
        path.chmod(0o600)
    except OSError:
        pass
    return fresh


# ── facts about the machine (each best effort; None = unknown, field omitted) ──

def _cpu_model() -> str | None:
    system = _os_name()
    if system == "linux":
        fields: dict[str, str] = {}
        for line in _read("/proc/cpuinfo").splitlines():
            key, _, val = line.partition(":")
            fields.setdefault(key.strip().lower(), val.strip())
        # x86 has "model name"; ARM boards have "hardware" or "model" (x86's "model" is a number)
        for key in ("model name", "hardware", "model"):
            if fields.get(key) and not fields[key].isdigit():
                return fields[key]
        return None
    elif system == "darwin":
        return _run("sysctl", "-n", "machdep.cpu.brand_string") or None
    return platform.processor() or None


def _gpu_model() -> str | None:
    system = _os_name()
    if system == "linux" and shutil.which("lspci"):
        for line in _run("lspci").splitlines():
            if re.search(r"\b(VGA|3D|Display)\b", line):
                return line.split(": ", 1)[-1].strip()
    elif system == "darwin":
        m = re.search(r'"sppci_model"\s*:\s*"([^"]+)"', _run("system_profiler", "SPDisplaysDataType", "-json"))
        return m.group(1) if m else None
    return None


def _linux_disk_name() -> str:
    """The block device under /, as the kernel names it (nvme0n1, sda, dm-0); "" when unknown."""
    source = ""
    for line in _read("/proc/self/mountinfo").splitlines():
        left, _, right = line.partition(" - ")
        if len(left.split()) > 4 and left.split()[4] == "/":
            fields = right.split()
            source = fields[1] if len(fields) > 1 else ""
    if not source.startswith("/dev/"):
        return ""
    name = os.path.basename(os.path.realpath(source))
    node = os.path.realpath(f"/sys/class/block/{name}")
    parent = os.path.basename(os.path.dirname(node))
    return parent if os.path.isdir(f"/sys/block/{parent}") and not os.path.isdir(f"/sys/block/{name}") else name


def _storage() -> tuple[str | None, int | None]:
    try:
        total = shutil.disk_usage(os.path.abspath(os.sep)).total
        gb: int | None = int(total // 10**9)
    except OSError:
        gb = None
    if _os_name() != "linux":
        return None, gb
    name = _linux_disk_name()
    if name.startswith("nvme"):
        return "nvme", gb
    if name.startswith("mmcblk"):
        return "emmc", gb
    rot = _read(f"/sys/block/{name}/queue/rotational") if name else ""
    return ({"1": "hdd", "0": "ssd"}.get(rot)), gb


def _connection_type() -> str | None:
    system = _os_name()
    iface = ""
    if system == "linux":
        best = None
        for line in _read("/proc/net/route").splitlines()[1:]:
            f = line.split()
            if len(f) > 6 and f[1] == "00000000" and (best is None or int(f[6]) < best[0]):
                best = (int(f[6]), f[0])
        iface = best[1] if best else ""
        if not iface:
            return None
        if os.path.isdir(f"/sys/class/net/{iface}/wireless"):
            return "wifi"
        if iface.startswith(("wwan", "ppp", "rmnet")):
            return "cellular"
        if iface.startswith(("tun", "tap", "wg", "tailscale", "docker", "br-", "veth")):
            return "other"
        return "ethernet"
    if system == "darwin":
        m = re.search(r"interface:\s*(\S+)", _run("route", "-n", "get", "default"))
        iface = m.group(1) if m else ""
        if not iface:
            return None
        ports = _run("networksetup", "-listallhardwareports")
        m = re.search(rf"Hardware Port:\s*(.+)\nDevice:\s*{re.escape(iface)}\b", ports)
        port = (m.group(1) if m else "").lower()
        if "wi-fi" in port or "airport" in port:
            return "wifi"
        if "ethernet" in port or "lan" in port or "thunderbolt" in port:
            return "ethernet"
        return "other" if port else None
    return None


def _virtual() -> bool | None:
    system = _os_name()
    if system == "linux":
        if shutil.which("systemd-detect-virt"):
            try:
                rc = subprocess.run(["systemd-detect-virt"], capture_output=True, timeout=_PROBE_TIMEOUT,
                                    check=False, stdin=subprocess.DEVNULL).returncode
                return rc == 0
            except (OSError, subprocess.SubprocessError):
                pass
        if os.path.exists("/.dockerenv"):
            return True
        return " hypervisor" in _read("/proc/cpuinfo").replace("\n", " ") or None
    if system == "darwin":
        out = _run("sysctl", "-n", "kern.hv_vmm_present")
        return out == "1" if out else None
    return None


def _serial() -> str | None:
    system = _os_name()
    serial = ""
    if system == "linux":
        serial = _read("/sys/class/dmi/id/product_serial")  # root-only on most distros: then unknown
    elif system == "darwin":
        m = re.search(r'"IOPlatformSerialNumber" = "([^"]+)"', _run("ioreg", "-rd1", "-c", "IOPlatformExpertDevice"))
        serial = m.group(1) if m else ""
    elif system == "windows":
        lines = _run("powershell", "-NoProfile", "-Command", "(Get-CimInstance Win32_BIOS).SerialNumber").splitlines()
        serial = lines[0].strip() if lines else ""
    serial = serial.strip()[:64]
    return None if serial.lower() in _JUNK_SERIALS else serial


def _host_class() -> str:
    system = _os_name()
    if system == "linux":
        if os.environ.get("ANDROID_ROOT"):
            return "phone"
        product = _read("/sys/class/dmi/id/product_name")  # Apple firmware's chassis type is unreliable
        if product.startswith("MacBook"):
            return "laptop"
        if product.startswith(("iMac", "Macmini", "MacPro", "Mac")):
            return "desktop"
        chassis = _read("/sys/class/dmi/id/chassis_type")
        if chassis.isdigit():
            n = int(chassis)
            if n in (8, 9, 10, 14, 30, 31, 32):
                return "laptop"
            if n in (3, 4, 5, 6, 7, 13, 15, 16, 24, 35, 36):
                return "desktop"
            if n in (17, 23, 25, 28):
                return "server"
    elif system == "darwin":
        model = _run("sysctl", "-n", "hw.model")
        if model:
            return "laptop" if model.startswith("MacBook") else "desktop"
    return "unknown"


def build_report() -> dict[str, Any]:
    """The POST body (housing.v1 ReportRequest). Unknown facts are left out."""
    from windyfly import __version__

    storage_type, storage_gb = _storage()
    facts: dict[str, Any] = {
        "os": _os_name(),
        "arch": platform.machine().lower() or None,
        "runtime": f"windyfly {__version__}",
        "cpu_model": _cpu_model(),
        "gpu_model": _gpu_model(),
        "storage_type": storage_type,
        "storage_gb": storage_gb,
        "connection_type": _connection_type(),
        "virtual": _virtual(),
        "serial": _serial(),
    }
    snapshot = {k: (v[:120] if isinstance(v, str) and k.endswith("_model") else v)
                for k, v in facts.items() if v is not None}
    return {"host": host_id(), "host_class": _host_class(), "snapshot": snapshot}


# ── the one call ─────────────────────────────────────────────────────

def report_once(*, transport: httpx.BaseTransport | None = None) -> str:
    """Send the report. Returns Eternitas's ``snapshot`` word (recorded, unchanged, not_collected,
    none) or a short reason it did not go (``off``, ``no_passport``, ``no_token``, ``http_<n>``,
    ``unreachable``). Never raises, never retries, never logs a fact."""
    if not enabled():
        return "off"
    try:
        from windyfly.agent import service_auth
        from windyfly.eternitas import agent_keys as ak

        passport = ak.current_passport()
        if not passport:
            return "no_passport"
        url = f"{ak._base_url()}/api/v1/bots/{passport}/housing"  # noqa: SLF001
        body = build_report()
        resp = None
        deadline = time.monotonic() + _TIMEOUT
        with httpx.Client(timeout=_TIMEOUT, transport=transport) as client:
            for attempt in (1, 2):
                left = deadline - time.monotonic()
                if left <= 0.05:
                    break
                try:
                    headers = service_auth.agent_headers(AUDIENCE, "POST", url)
                except service_auth.ServiceAuthError:
                    return "no_token"
                resp = client.post(url, json=body, headers=headers, timeout=left)
                if resp.status_code != 401 or attempt == 2:
                    break
                service_auth.forget_token()  # a stale cached mint: one fresh try
        if resp is None or resp.status_code != 201:
            return f"http_{getattr(resp, 'status_code', 0)}"
        word = str((resp.json() or {}).get("snapshot") or "none")
        logger.info("[housing] reported: %s", word)
        return word
    except httpx.HTTPError:
        return "unreachable"
    except Exception as exc:  # noqa: BLE001  (a report must never disturb the agent)
        logger.debug("[housing] report failed: %s", type(exc).__name__)
        return "error"


def report_in_background() -> threading.Thread | None:
    """Boot: fire-and-forget on a daemon thread. Does nothing when off, under pytest, or when
    the agent keys are disabled."""
    from windyfly.eternitas.agent_keys import disabled

    if not enabled() or disabled():
        return None
    t = threading.Thread(target=report_once, name="housing-report", daemon=True)
    t.start()
    return t
