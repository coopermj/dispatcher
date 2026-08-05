"""Upload timeout must scale with file size — 60s killed every ~125MB upload."""
from modules.remarkable import upload_timeout_for


def test_small_file_gets_floor_timeout(tmp_path):
    f = tmp_path / "small.pdf"
    f.write_bytes(b"x" * 1_000_000)  # 1 MB
    assert upload_timeout_for(str(f)) == 60


def test_large_file_scales_ten_seconds_per_mb(tmp_path):
    f = tmp_path / "big.pdf"
    f.write_bytes(b"x" * 126_000_000)  # ~126 MB (today's TMD)
    assert upload_timeout_for(str(f)) >= 1200


def test_missing_file_gets_floor(tmp_path):
    assert upload_timeout_for(str(tmp_path / "nope.pdf")) == 60
