from datetime import date

import pytest

from uspto_trademark_radar.restore import plan_restore


def test_plan_restore_picks_latest_edition_and_post_cutoff_dailies():
    repo_files = [
        "README.md",
        # older edition — mirror is append-only, must be skipped
        "annual/apc18840407-20241231-01.zip",
        "annual/apc18840407-20241231-02.zip",
        # latest edition
        "annual/apc18840407-20251231-01.zip",
        "annual/apc18840407-20251231-02.zip",
        # dailies: one superseded by the 2025 cutoff, two live
        "daily/apc251230.zip",
        "daily/apc260102.zip",
        "daily/apc260103.zip",
    ]
    cutoff, wanted = plan_restore(repo_files)
    assert cutoff == date(2025, 12, 31)
    assert wanted == [
        "annual/apc18840407-20251231-01.zip",
        "annual/apc18840407-20251231-02.zip",
        "daily/apc260102.zip",
        "daily/apc260103.zip",
    ]


def test_plan_restore_rejects_empty_mirror():
    with pytest.raises(SystemExit, match="no parseable annual snapshot"):
        plan_restore(["README.md", "daily/apc260102.zip"])
