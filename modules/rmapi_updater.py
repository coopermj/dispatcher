"""Self-update for the rmapi binary.

rmapi talks to reMarkable's undocumented cloud API, which changes without
notice; when it does, every `rmapi put` fails until ddvk ships a new release
(usually within hours — v0.0.35 landed 2026-08-19, the day the box's uploads
broke). This module lets the pipeline install that release itself when rmapi
looks broken, so the failure email says "fixed" instead of "please fix".

Policy: only ever moves forward to a newer release tag, only on Linux, and only
swaps the binary in after the downloaded one passes a real cloud call (`ls`).
The previous binary is kept beside it as `rmapi.prev`.

Network (`fetch_release`, `download`) and subprocess (`run`) are injectable so
the logic is testable without GitHub or a real rmapi.
"""

import json
import os
import platform
import re
import shutil
import subprocess
import tarfile
import urllib.request
from pathlib import Path

from config.settings import RMAPI_SELF_UPDATE, RMAPI_RELEASE_REPO

# Release asset per (platform.system(), platform.machine()). Deliberately no
# macOS entry: the Mac is not the scheduler and swapping binaries under a
# developer is not "self-healing".
ASSET_BY_PLATFORM = {
    ("Linux", "x86_64"): "rmapi-linux-amd64.tar.gz",
    ("Linux", "amd64"): "rmapi-linux-amd64.tar.gz",
    ("Linux", "aarch64"): "rmapi-linux-arm64.tar.gz",
    ("Linux", "arm64"): "rmapi-linux-arm64.tar.gz",
}
COMMAND_TIMEOUT = 30
DOWNLOAD_TIMEOUT = 300


class UpdateResult:
    """Outcome of one self_update() call.

    status: 'updated' | 'already-latest' | 'verify-failed' | 'error' |
            'unsupported-platform' | 'disabled'
    """

    def __init__(self, status, old_version=None, new_version=None, detail=""):
        self.status = status
        self.old_version = old_version
        self.new_version = new_version
        self.detail = detail

    def summary(self):
        """One line for the failure report."""
        if self.status == "updated":
            return (f"self-healed: rmapi updated {self.old_version or '(missing)'} → "
                    f"{self.new_version} and the failed operation was retried")
        if self.status == "already-latest":
            return (f"update check: rmapi {self.old_version} is already the latest release "
                    f"— these failures are not a stale client")
        if self.status == "verify-failed":
            return (f"update check: {self.new_version} downloaded but failed verification "
                    f"({self.detail}); kept {self.old_version}")
        if self.status == "unsupported-platform":
            return f"update check skipped: {self.detail}"
        if self.status == "disabled":
            return "update check skipped: RMAPI_SELF_UPDATE=false"
        return f"update check failed: {self.detail}"

    def __repr__(self):
        return f"UpdateResult({self.status!r}, {self.old_version!r} → {self.new_version!r}, {self.detail!r})"


def parse_version(text):
    """'v0.0.35\\n' / 'rmapi version 0.0.36' → (0, 0, 35); None if no digits."""
    if not text:
        return None
    m = re.search(r"(\d+(?:\.\d+)*)", text)
    if not m:
        return None
    return tuple(int(p) for p in m.group(1).split("."))


def is_newer(candidate, installed):
    """True if `candidate` (tag string) is strictly newer than `installed`
    (tag string or None). An unparsable installed version counts as older:
    a broken binary that can't even print its version should be replaced."""
    cand = parse_version(candidate)
    if cand is None:
        return False
    inst = parse_version(installed)
    if inst is None:
        return True
    return cand > inst


def installed_version(rmapi_path, run=subprocess.run):
    """Tag string printed by `rmapi version`, or None if it can't be read."""
    if not os.path.exists(rmapi_path):
        return None
    try:
        r = run([rmapi_path, "version"], capture_output=True, text=True, timeout=COMMAND_TIMEOUT)
        if r.returncode != 0:
            return None
        v = parse_version(r.stdout)
        return f"v{'.'.join(str(p) for p in v)}" if v else None
    except Exception:
        return None


def fetch_latest_release(repo):
    """GitHub 'latest release' JSON for owner/repo (unauthenticated; 60/hr is plenty)."""
    req = urllib.request.Request(
        f"https://api.github.com/repos/{repo}/releases/latest",
        headers={"Accept": "application/vnd.github+json", "User-Agent": "dispatchweb-rmapi-updater"},
    )
    with urllib.request.urlopen(req, timeout=COMMAND_TIMEOUT) as resp:
        return json.load(resp)


def download_file(url, dest: Path):
    req = urllib.request.Request(url, headers={"User-Agent": "dispatchweb-rmapi-updater"})
    with urllib.request.urlopen(req, timeout=DOWNLOAD_TIMEOUT) as resp, open(dest, "wb") as out:
        shutil.copyfileobj(resp, out)


def _extract_rmapi(tarball: Path, dest: Path):
    with tarfile.open(tarball, "r:gz") as tar:
        member = next((m for m in tar.getmembers() if Path(m.name).name == "rmapi" and m.isfile()), None)
        if member is None:
            raise FileNotFoundError("no 'rmapi' file inside release tarball")
        src = tar.extractfile(member)
        with open(dest, "wb") as out:
            shutil.copyfileobj(src, out)
    dest.chmod(0o755)


def _verify(candidate: Path, run):
    """The new binary must print a version AND complete a real cloud call."""
    for args in (["version"], ["ls", "/"]):
        r = run([str(candidate)] + args, capture_output=True, text=True, timeout=COMMAND_TIMEOUT)
        if r.returncode != 0:
            return False, f"`rmapi {' '.join(args)}` exited {r.returncode}: {(r.stderr or r.stdout).strip()[:200]}"
    return True, ""


def self_update(rmapi_path, *, repo=None, run=subprocess.run, fetch_release=fetch_latest_release,
                download=download_file, platform_key=None):
    """Install the latest rmapi release if it is newer than the installed one.

    Returns an UpdateResult; never raises. Swaps the binary only after the
    downloaded one passes `_verify`; the old binary stays as `<path>.prev`.
    """
    if not RMAPI_SELF_UPDATE:
        return UpdateResult("disabled")

    key = platform_key or (platform.system(), platform.machine())
    asset_name = ASSET_BY_PLATFORM.get(key)
    if asset_name is None:
        return UpdateResult("unsupported-platform", detail=f"no rmapi release asset for {key[0]}/{key[1]}")

    target = Path(os.path.expanduser(rmapi_path))
    old = installed_version(str(target), run=run)
    repo = repo or RMAPI_RELEASE_REPO

    try:
        release = fetch_release(repo)
        latest = release["tag_name"]
        asset = next(a for a in release.get("assets", []) if a["name"] == asset_name)
    except StopIteration:
        return UpdateResult("error", old_version=old, detail=f"release has no asset {asset_name}")
    except Exception as e:
        return UpdateResult("error", old_version=old, detail=f"could not read latest release of {repo}: {e}")

    if not is_newer(latest, old):
        return UpdateResult("already-latest", old_version=old, new_version=latest)

    print(f"⬇️  rmapi {old or '(missing)'} → {latest}: downloading {asset_name} from {repo}")
    target.parent.mkdir(parents=True, exist_ok=True)
    tarball = target.with_name(target.name + ".download")
    candidate = target.with_name(target.name + ".new")
    try:
        download(asset["browser_download_url"], tarball)
        _extract_rmapi(tarball, candidate)
        ok, why = _verify(candidate, run)
        if not ok:
            return UpdateResult("verify-failed", old_version=old, new_version=latest, detail=why)
        if target.exists():
            os.replace(target, target.with_name(target.name + ".prev"))
        os.replace(candidate, target)
        print(f"✅ rmapi updated to {latest} (previous kept as {target.name}.prev)")
        return UpdateResult("updated", old_version=old, new_version=latest)
    except Exception as e:
        return UpdateResult("error", old_version=old, new_version=latest, detail=f"{type(e).__name__}: {e}")
    finally:
        for tmp in (tarball, candidate):
            try:
                tmp.unlink()
            except OSError:
                pass
