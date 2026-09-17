"""rmapi breaks when reMarkable changes its cloud API; ddvk ships a fix within
hours (v0.0.35 landed 2026-08-19, the day every upload failed). The pipeline
should install that fix itself instead of waiting for a human to notice the
failure email. Network and subprocess are injected so nothing here touches
GitHub or a real rmapi."""
import io
import os
import tarfile
from types import SimpleNamespace

from modules import rmapi_updater as upd
from modules.rmapi_updater import self_update, UpdateResult

LINUX = ("Linux", "x86_64")
ASSET = "rmapi-linux-amd64.tar.gz"


def _tarball_with_rmapi(content: bytes) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        info = tarfile.TarInfo("rmapi")
        info.size = len(content)
        info.mode = 0o755
        tar.addfile(info, io.BytesIO(content))
    return buf.getvalue()


def _release(tag, asset=ASSET):
    return {"tag_name": tag,
            "assets": [{"name": asset, "browser_download_url": f"https://example.invalid/{tag}/{asset}"}]}


class FakeRun:
    """Fake subprocess.run: `rmapi version` prints the version baked into the
    binary's bytes; `ls` succeeds unless the binary content says 'broken'."""

    def __init__(self):
        self.calls = []

    def __call__(self, cmd, **kw):
        self.calls.append(list(cmd))
        exe = cmd[0]
        try:
            body = open(exe, "rb").read()
        except OSError:
            return SimpleNamespace(returncode=127, stdout="", stderr="not found")
        if cmd[1] == "version":
            return SimpleNamespace(returncode=0, stdout=body.split(b"|")[0].decode() + "\n", stderr="")
        if b"broken" in body:
            return SimpleNamespace(returncode=1, stdout="", stderr="boom")
        return SimpleNamespace(returncode=0, stdout="[d] News", stderr="")


def _setup(tmp_path, installed=b"v0.0.35|old", latest="v0.0.36", new_body=b"v0.0.36|new"):
    rmapi = tmp_path / "rmapi"
    if installed is not None:
        rmapi.write_bytes(installed)
        rmapi.chmod(0o755)
    downloads = []

    def download(url, dest):
        downloads.append(url)
        dest.write_bytes(_tarball_with_rmapi(new_body))

    return rmapi, FakeRun(), (lambda repo: _release(latest)), download, downloads


def test_installs_newer_release_and_keeps_previous_binary(tmp_path):
    rmapi, run, fetch, download, downloads = _setup(tmp_path)
    result = self_update(str(rmapi), run=run, fetch_release=fetch, download=download, platform_key=LINUX)
    assert result.status == "updated"
    assert (result.old_version, result.new_version) == ("v0.0.35", "v0.0.36")
    assert rmapi.read_bytes() == b"v0.0.36|new"
    assert (tmp_path / "rmapi.prev").read_bytes() == b"v0.0.35|old"
    assert os.access(rmapi, os.X_OK)
    assert downloads == ["https://example.invalid/v0.0.36/rmapi-linux-amd64.tar.gz"]


def test_does_nothing_when_already_on_latest(tmp_path):
    rmapi, run, fetch, download, downloads = _setup(tmp_path, latest="v0.0.35")
    result = self_update(str(rmapi), run=run, fetch_release=fetch, download=download, platform_key=LINUX)
    assert result.status == "already-latest"
    assert rmapi.read_bytes() == b"v0.0.35|old"
    assert downloads == []


def test_never_downgrades(tmp_path):
    rmapi, run, fetch, download, downloads = _setup(tmp_path, latest="v0.0.34")
    result = self_update(str(rmapi), run=run, fetch_release=fetch, download=download, platform_key=LINUX)
    assert result.status == "already-latest"
    assert downloads == []


def test_keeps_old_binary_when_new_one_fails_verification(tmp_path):
    rmapi, run, fetch, download, _ = _setup(tmp_path, new_body=b"v0.0.36|broken")
    result = self_update(str(rmapi), run=run, fetch_release=fetch, download=download, platform_key=LINUX)
    assert result.status == "verify-failed"
    assert rmapi.read_bytes() == b"v0.0.35|old"
    assert not (tmp_path / "rmapi.prev").exists()
    assert not (tmp_path / "rmapi.new").exists(), "temp download must be cleaned up"


def test_new_binary_is_verified_with_a_real_cloud_call(tmp_path):
    """`version` alone proves nothing about the API; the new binary must `ls`."""
    rmapi, run, fetch, download, _ = _setup(tmp_path)
    self_update(str(rmapi), run=run, fetch_release=fetch, download=download, platform_key=LINUX)
    new_calls = [c for c in run.calls if c[0].endswith("rmapi.new")]
    assert any(c[1] == "ls" for c in new_calls), new_calls


def test_skips_unsupported_platform_without_network(tmp_path):
    rmapi, run, fetch, download, downloads = _setup(tmp_path)
    called = []
    result = self_update(str(rmapi), run=run, fetch_release=lambda r: called.append(r),
                         download=download, platform_key=("Darwin", "arm64"))
    assert result.status == "unsupported-platform"
    assert called == [] and downloads == []


def test_missing_binary_is_installed_fresh(tmp_path):
    rmapi, run, fetch, download, _ = _setup(tmp_path, installed=None)
    result = self_update(str(rmapi), run=run, fetch_release=fetch, download=download, platform_key=LINUX)
    assert result.status == "updated"
    assert result.old_version is None
    assert rmapi.read_bytes() == b"v0.0.36|new"


def test_release_lookup_failure_is_reported_not_raised(tmp_path):
    rmapi, run, _, download, downloads = _setup(tmp_path)

    def fetch(repo):
        raise OSError("github unreachable")

    result = self_update(str(rmapi), run=run, fetch_release=fetch, download=download, platform_key=LINUX)
    assert result.status == "error"
    assert "github unreachable" in result.detail
    assert downloads == []


def test_disabled_by_setting(tmp_path, monkeypatch):
    monkeypatch.setattr(upd, "RMAPI_SELF_UPDATE", False)
    rmapi, run, fetch, download, downloads = _setup(tmp_path)
    called = []
    result = self_update(str(rmapi), run=run, fetch_release=lambda r: called.append(r),
                         download=download, platform_key=LINUX)
    assert result.status == "disabled"
    assert called == []


def test_version_parsing_tolerates_prefixes_and_noise():
    assert upd.parse_version("v0.0.35\n") == (0, 0, 35)
    assert upd.parse_version("rmapi version 0.0.36") == (0, 0, 36)
    assert upd.parse_version("") is None
    assert upd.is_newer("v0.0.36", "v0.0.35")
    assert not upd.is_newer("v0.0.35", "v0.0.35")
    assert upd.is_newer("v0.0.36", None)


def test_update_result_describes_itself_for_the_failure_report():
    r = UpdateResult("updated", old_version="v0.0.35", new_version="v0.0.36", detail="")
    assert "v0.0.35" in r.summary() and "v0.0.36" in r.summary()
    assert "self-healed" in r.summary().lower()
    r2 = UpdateResult("already-latest", old_version="v0.0.35", new_version="v0.0.35", detail="")
    assert "not a stale client" in r2.summary().lower()
