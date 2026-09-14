"""Regression group: rosbags must read the rosbag2-recorded fixture bags (both storages).

These pass on the unmodified upstream tree and guard the untouched reader path.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from rosbag2_verify import FIXTURES, bag_metadata
from rosbags.rosbag2 import Reader

FIXTURE_DIRS = sorted(str(p.relative_to(FIXTURES)) for p in FIXTURES.glob('*/*') if (p / 'metadata.yaml').is_file())

# rosbags 0.11.5 cannot open two of the fixtures on its own (schema encoding 'unknown' for
# service/action types; RIHS01 hashes in the Go-converted talker.db3 that do not match the
# definitions). Those are upstream reader limitations, unrelated to the writers under test.
READABLE = [
    'mcap/cdr_test',
    'mcap/convert_a',
    'mcap/talker',
    'mcap/wbag',
    'sqlite3/cdr_test',
    'sqlite3/convert_a',
    'sqlite3/wbag',
]


def test_fixture_inventory() -> None:
    assert FIXTURE_DIRS == [
        'mcap/bag_with_topics_and_service_events_and_action',
        'mcap/cdr_test',
        'mcap/convert_a',
        'mcap/talker',
        'mcap/wbag',
        'sqlite3/bag_with_topics_and_service_events_and_action',
        'sqlite3/cdr_test',
        'sqlite3/convert_a',
        'sqlite3/talker',
        'sqlite3/wbag',
    ]


@pytest.mark.parametrize('rel', READABLE)
def test_fixture_bag_reads_consistently(rel: str) -> None:
    bagdir: Path = FIXTURES / rel
    meta = bag_metadata(bagdir)
    expected_counts = {t['topic_metadata']['name']: t['message_count'] for t in meta['topics_with_message_count']}
    with Reader(bagdir) as reader:
        assert reader.message_count == meta['message_count']
        got: dict[str, int] = {c.topic: 0 for c in reader.connections}
        for conn, timestamp, data in reader.messages():
            got[conn.topic] += 1
            assert isinstance(timestamp, int) and len(data) > 0
        assert sum(got.values()) == meta['message_count']
        assert {k: v for k, v in got.items() if v} == {k: v for k, v in expected_counts.items() if v}
        assert {c.topic: c.msgcount for c in reader.connections} == expected_counts
