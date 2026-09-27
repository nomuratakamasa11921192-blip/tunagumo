import re

import pytest

from src.video.room_tour import _fmt_timestamp, _script_to_srt
from src.video.schemas import VideoScene, VideoScript


@pytest.mark.parametrize("seconds, expected", [
    (-1, "00:00:00,000"),
    (0, "00:00:00,000"),
    (1.2344, "00:00:01,234"),
    (23.999999999, "00:00:24,000"),
    (59.9999, "00:01:00,000"),
    (3599.9999, "01:00:00,000"),
    (3600.001, "01:00:00,001"),
])
def test_srt_timestamp_carries_milliseconds(seconds, expected):
    assert _fmt_timestamp(seconds) == expected


def test_script_srt_ends_at_rounded_duration_with_valid_fields():
    script = VideoScript(title="test", scenes=[
        VideoScene(narration="a", on_screen_text="first"),
        VideoScene(narration="bb", on_screen_text="last"),
    ])
    srt = _script_to_srt(script, 23.999999999)
    timelines = [line for line in srt.splitlines() if " --> " in line]
    assert len(timelines) == 2
    for line in timelines:
        assert re.fullmatch(r"[0-9]{2}:[0-5][0-9]:[0-5][0-9],[0-9]{3} --> [0-9]{2}:[0-5][0-9]:[0-5][0-9],[0-9]{3}", line)
    assert timelines[-1].endswith("00:00:24,000")
