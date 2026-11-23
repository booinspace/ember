"""Check reproducible synthetic scoring inputs without models or Windows services."""

from pathlib import Path

from pytest import MonkeyPatch


def test_fixture_is_seeded_and_labels_refer_to_history(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2] / "scripts"))
    from benchmark_memory_synthetic import fixture

    sessions, cases = fixture(29045, 24, 256)
    assert (sessions, cases) == fixture(29045, 24, 256)
    assert (sessions, cases) != fixture(29046, 24, 256)
    assert len(sessions) == len({row.id for row in sessions}) == 352
    assert len(cases) == len({case.id for case in cases}) == 120
    index = {row.id: row for row in sessions}
    for case in cases:
        assert set(case.required).isdisjoint(case.stale)
        assert set(case.required + case.stale) <= index.keys()
        if case.category == "correction":
            assert index[case.stale[0]].day < index[case.required[0]].day
        if case.category == "multi_hop_alias":
            assert len(case.required) == 2
            assert index[case.required[0]].text.split()[0] in case.query
        if case.category == "unanswerable":
            assert not case.required
