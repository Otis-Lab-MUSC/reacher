"""The update job state: transitions, the status payload, and staging cleanup."""

from pathlib import Path

import pytest

from reacher.update.state import UpdateState, UpdateStatus


def test_a_new_state_is_idle_and_empty():
    state = UpdateState()
    assert state.status is UpdateStatus.IDLE
    assert state.percent == 0
    assert state.verified is False
    assert state.error is None
    assert state.staged_dir is None


def test_payload_has_the_keys_the_frontend_polls():
    payload = UpdateState().to_payload()
    assert payload == {
        "status": "idle",
        "percent": 0,
        "asset_name": None,
        "verified": False,
        "error": None,
    }


def test_payload_never_exposes_a_filesystem_path(tmp_path: Path):
    state = UpdateState()
    state.begin("labrynth-3.0.1-beta.17-linux-x64.tar.gz", tmp_path)
    state.mark_ready(tmp_path / "Labrynth")
    assert str(tmp_path) not in str(state.to_payload())


def test_begin_enters_downloading_and_records_the_asset(tmp_path: Path):
    state = UpdateState()
    state.begin("asset.tar.gz", tmp_path)
    assert state.status is UpdateStatus.DOWNLOADING
    assert state.asset_name == "asset.tar.gz"
    assert state.staging_root == tmp_path


def test_begin_while_downloading_is_refused(tmp_path: Path):
    state = UpdateState()
    state.begin("a.tar.gz", tmp_path)
    with pytest.raises(RuntimeError):
        state.begin("b.tar.gz", tmp_path)


def test_progress_is_clamped_to_zero_through_one_hundred(tmp_path: Path):
    state = UpdateState()
    state.begin("a.tar.gz", tmp_path)
    state.set_progress(-5)
    assert state.percent == 0
    state.set_progress(250)
    assert state.percent == 100
    state.set_progress(42)
    assert state.percent == 42


def test_mark_ready_sets_verified_and_full_progress(tmp_path: Path):
    state = UpdateState()
    state.begin("a.tar.gz", tmp_path)
    staged = tmp_path / "Labrynth"
    state.mark_ready(staged)
    assert state.status is UpdateStatus.READY
    assert state.verified is True
    assert state.percent == 100
    assert state.staged_dir == staged


def test_mark_failed_records_the_message_and_clears_verified(tmp_path: Path):
    state = UpdateState()
    state.begin("a.tar.gz", tmp_path)
    state.mark_failed("the hash does not match")
    assert state.status is UpdateStatus.ERROR
    assert state.error == "the hash does not match"
    assert state.verified is False
    assert state.staged_dir is None


def test_begin_is_allowed_again_after_an_error(tmp_path: Path):
    state = UpdateState()
    state.begin("a.tar.gz", tmp_path)
    state.mark_failed("boom")
    state.begin("a.tar.gz", tmp_path)
    assert state.status is UpdateStatus.DOWNLOADING
    assert state.error is None


def test_reset_removes_the_staging_folder(tmp_path: Path):
    staging = tmp_path / "labrynth-update_x"
    staging.mkdir()
    (staging / "file").write_text("x")
    state = UpdateState()
    state.begin("a.tar.gz", staging)
    state.reset()
    assert not staging.exists()
    assert state.status is UpdateStatus.IDLE
    assert state.staging_root is None


def test_reset_tolerates_a_staging_folder_that_is_already_gone(tmp_path: Path):
    state = UpdateState()
    state.begin("a.tar.gz", tmp_path / "missing")
    state.reset()
    assert state.status is UpdateStatus.IDLE


def test_mark_launching_follows_ready_and_keeps_the_staged_folder(tmp_path: Path):
    state = UpdateState()
    state.begin("a.tar.gz", tmp_path)
    staged = tmp_path / "Labrynth"
    state.mark_ready(staged)
    state.mark_launching()
    assert state.status is UpdateStatus.LAUNCHING
    assert state.staged_dir == staged
    assert state.to_payload()["status"] == "launching"


def test_begin_is_refused_while_launching(tmp_path: Path):
    state = UpdateState()
    state.begin("a.tar.gz", tmp_path)
    state.mark_ready(tmp_path / "Labrynth")
    state.mark_launching()
    with pytest.raises(RuntimeError):
        state.begin("b.tar.gz", tmp_path)


def test_busy_is_true_only_while_downloading_or_launching(tmp_path: Path):
    state = UpdateState()
    assert state.busy is False
    state.begin("a.tar.gz", tmp_path)
    assert state.busy is True
    state.mark_ready(tmp_path / "Labrynth")
    assert state.busy is False
    state.mark_launching()
    assert state.busy is True
    state.mark_failed("x")
    assert state.busy is False
