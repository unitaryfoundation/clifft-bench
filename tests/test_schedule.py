from clifft_bench.schedule import balanced_schedule


def test_pair_uses_abba_order() -> None:
    schedule = balanced_schedule(["A", "B"], 2)
    assert [item.case_id for item in schedule] == ["A", "B", "B", "A"]
    assert [item.repetition for item in schedule] == [0, 0, 1, 1]
    assert [item.sequence_index for item in schedule] == [0, 1, 2, 3]


def test_empty_schedule_is_empty() -> None:
    assert balanced_schedule([], 3) == []
