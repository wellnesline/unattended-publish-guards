"""synthetic test configuration (no real data is used anywhere in these tests).

Layout assumption: this repository's files live flat at the repo root (scripts + tests/).
The tests put the repo root and tests/ on sys.path. The video size and the allowed
duration range are settings (PUBLISH_GUARD_VIDEO_SIZE / PUBLISH_GUARD_VIDEO_SECONDS);
the tests use 360x640 and 30-120.
"""
import os
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1]
os.environ.setdefault("PUBLISH_GUARD_VIDEO_SIZE", "360x640")  # synthetic size (keeps the test video small)
os.environ.setdefault("PUBLISH_GUARD_VIDEO_SECONDS", "30-120")
os.environ.setdefault("EXPECTED_CHANNEL_ID", "<CHANNEL_ID>")
for p in (SCRIPTS, SCRIPTS / "tests"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))
