"""Fixed-commit Linux packs must remain discoverable without selecting another build."""

import json
import os

import pytest

from hermes_cli import main_desktop
from hermes_cli.steward import read_install_stamp

pytestmark = pytest.mark.platforms("linux")
COMMIT = "71d34bad1045610252eda02ee79549436896e784"
WRONG_COMMIT = COMMIT[:7] + "0" * 33


@pytest.fixture(autouse=True)
def fixed_identity(monkeypatch):
    monkeypatch.setenv("HERMES_BUILD_COMMIT", COMMIT)
    monkeypatch.delenv("HERMES_DESKTOP_VARIANT", raising=False)
    monkeypatch.delenv("_HERMES_CHANNEL_REQUEST_JSON", raising=False)
    monkeypatch.setenv("HERMES_PAYLOAD_TAG", "v0.21.7+canary.20261013T010203Z")


def packaged(release, name, stamp, *, layout="linux-unpacked", directory=False):
    root = release / layout
    root.mkdir(parents=True, exist_ok=True)
    exe = root / name
    if directory:
        exe.mkdir()
    else:
        exe.write_bytes(b"packaged app")
    resources = root / "resources"
    resources.mkdir(exist_ok=True)
    if stamp is not None:
        (resources / "install-stamp.json").write_text(stamp, encoding="utf-8")
    return exe


@pytest.mark.parametrize("variant,layout,name,stamp,directory,commit,channel,selected", [
    ("", "linux-unpacked", "Hermes 71d34ba", COMMIT, False, COMMIT, "", "fixed"),
    ("light", "linux-arm64-unpacked", "Hermes Light 71d34ba", COMMIT, False, COMMIT, "", "fixed"),
    ("bundled", "linux-unpacked", "Hermes Agent 71d34ba", COMMIT, False, COMMIT, "", "fixed"),
    ("", "linux-unpacked", "Hermes fffffff", COMMIT, False, COMMIT, "", None),
    ("", "linux-unpacked", "Hermes 71d34ba", WRONG_COMMIT, False, COMMIT, "", None),
    ("", "linux-unpacked", "Hermes 71d34ba", None, False, COMMIT, "", None),
    ("", "linux-unpacked", "Hermes 71d34ba", "malformed", False, COMMIT, "", None),
    ("", "linux-unpacked", "Hermes 71d34ba", "scalar", False, COMMIT, "", None),
    ("", "linux-unpacked", "Hermes 71d34ba", COMMIT, True, COMMIT, "", None),
    ("store", "linux-unpacked", "Hermes Agent 71d34ba", COMMIT, False, COMMIT, "", None),
    ("unknown", "linux-unpacked", "Hermes 71d34ba", COMMIT, False, COMMIT, "", None),
    ("bundled", "linux-unpacked", "Hermes Agent 71d34ba", COMMIT, False, COMMIT, "{}", None),
    ("", "linux-unpacked", "Hermes 71d34ba", COMMIT, False, "", "", "stable"),
    ("", "linux-unpacked", "Hermes 71d34ba", COMMIT, False, COMMIT[:7], "", "stable"),
    ("", "linux-unpacked", "Hermes 71d34ba", COMMIT, False, COMMIT.upper(), "", "stable"),
], ids=[
    "full-exact-commit", "light-arm64-exact-commit", "bundled-exact-commit",
    "wrong-filename-refused", "same-prefix-wrong-full-commit-refused",
    "missing-stamp-refused", "malformed-stamp-refused", "scalar-stamp-refused",
    "directory-candidate-refused", "store-variant-refused", "unknown-variant-refused",
    "conflicting-channel-refused", "ordinary-stable-discovery",
    "short-commit-uses-ordinary-discovery", "uppercase-commit-uses-ordinary-discovery",
])
def test_linux_discovery_respects_fixed_identity(
    tmp_path, monkeypatch, variant, layout, name, stamp, directory, commit, channel, selected,
):
    monkeypatch.setenv("HERMES_BUILD_COMMIT", commit)
    monkeypatch.setenv("HERMES_DESKTOP_VARIANT", variant)
    monkeypatch.setenv("_HERMES_CHANNEL_REQUEST_JSON", channel)
    stamp_text = "{" if stamp == "malformed" else json.dumps(0 if stamp == "scalar" else {"commit": stamp})
    fixed = packaged(tmp_path, name, None if stamp is None else stamp_text, layout=layout, directory=directory)
    # Newer ordinary and differently suffixed builds must not outrank the fixed build.
    stable = packaged(tmp_path, "Hermes", None)
    other = packaged(tmp_path, "Hermes ffffffe", None)
    os.utime(stable, (2_000_000_000, 2_000_000_000))
    os.utime(other, (2_000_000_001, 2_000_000_001))
    assert main_desktop._desktop_packaged_executable_in(tmp_path) == {"fixed": fixed, "stable": stable, None: None}[selected]


@pytest.mark.parametrize("stamp_commit", [COMMIT, WRONG_COMMIT],
                         ids=["matching-commit-promoted", "same-prefix-wrong-commit-keeps-live"])
def test_fixed_commit_promotion_publishes_only_matching_identity(tmp_path, monkeypatch, stamp_commit):
    desktop = tmp_path / "desktop"
    live = packaged(desktop / "release", "Hermes", None)
    live.write_bytes(b"previous app")
    staging = desktop / ".staging-test"
    packaged(staging, "Hermes 71d34ba", json.dumps({"commit": stamp_commit}))
    monkeypatch.setattr(main_desktop, "_stop_desktop_processes_locking_build", lambda *a, **kw: [])
    if stamp_commit != COMMIT:
        with pytest.raises(RuntimeError, match="no launchable app"):
            main_desktop._promote_staged_desktop_app(desktop, staging)
        assert live.read_bytes() == b"previous app"
    else:
        promoted = main_desktop._promote_staged_desktop_app(desktop, staging)
        assert promoted.read_bytes() == b"packaged app"
        assert main_desktop._desktop_packaged_executable(desktop) == promoted
        assert read_install_stamp(main_desktop._packaged_resources_dir(desktop))["commit"] == COMMIT
        assert not live.exists()
    assert not staging.exists()
