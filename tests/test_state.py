"""The novelty gate is the whole product, so it gets the most tests."""
from datetime import date

from radar.model import Listing
from radar.state import State, adopt_baseline


def listing(url, company="Acme", role="SWE Intern", source="list"):
    return Listing(source=source, company=company, role=role, url=url)


class TestNovelty:
    def test_everything_is_new_against_empty_state(self, tmp_path):
        state = State(tmp_path / "s.json")
        diff = state.diff([listing("https://x.com/1")])
        assert len(diff.new) == 1
        assert diff.first_run

    def test_recorded_listings_stop_being_new(self, tmp_path):
        state = State(tmp_path / "s.json")
        batch = [listing("https://x.com/1")]
        state.record(batch)
        assert state.diff(batch).new == []

    def test_only_the_unseen_posting_is_reported(self, tmp_path):
        state = State(tmp_path / "s.json")
        state.record([listing("https://x.com/1")])
        diff = state.diff([listing("https://x.com/1"),
                           listing("https://x.com/2")])
        assert [l.url for l in diff.new] == ["https://x.com/2"]

    def test_tracking_params_do_not_resurrect_a_known_posting(self, tmp_path):
        """A list changing its link format must not re-alert everything."""
        state = State(tmp_path / "s.json")
        state.record([listing("https://x.com/1?utm_source=old")])
        diff = state.diff([listing("https://x.com/1?utm_source=NEW")])
        assert diff.new == []

    def test_a_months_old_posting_never_alerts_twice(self, tmp_path):
        """The Oracle case: open for months, seen once, silent forever."""
        path = tmp_path / "s.json"
        stale = [listing("https://oracle.com/job/334333", company="Oracle")]
        state = State.load(path)
        state.record(stale, date(2026, 5, 1))
        state.save(date(2026, 5, 1))
        for _ in range(5):
            state = State.load(path)
            assert state.diff(stale).new == []
            state.record(stale)
            state.save()


class TestPersistence:
    def test_survives_a_save_load_cycle(self, tmp_path):
        path = tmp_path / "s.json"
        state = State(path)
        state.record([listing("https://x.com/1")], date(2026, 8, 3))
        state.save(date(2026, 8, 3))

        reloaded = State.load(path)
        assert reloaded.diff([listing("https://x.com/1")]).new == []
        assert reloaded.last_run == "2026-08-03"

    def test_first_seen_is_preserved_across_runs(self, tmp_path):
        """When a role opened is the one fact worth keeping."""
        state = State(tmp_path / "s.json")
        state.record([listing("https://x.com/1")], date(2026, 8, 1))
        state.record([listing("https://x.com/1")], date(2026, 8, 9))
        entry = state.seen["https://x.com/1"]
        assert entry["first_seen"] == "2026-08-01"
        assert entry["last_seen"] == "2026-08-09"

    def test_missing_file_reads_as_empty(self, tmp_path):
        assert State.load(tmp_path / "nope.json").is_empty

    def test_corrupt_file_does_not_crash_the_run(self, tmp_path):
        path = tmp_path / "s.json"
        path.write_text("{ this is not json", encoding="utf-8")
        assert State.load(path).is_empty

    def test_saved_keys_are_sorted_for_readable_diffs(self, tmp_path):
        path = tmp_path / "s.json"
        state = State(path)
        state.record([listing("https://x.com/9"), listing("https://x.com/1")])
        state.save()
        import json
        keys = list(json.loads(path.read_text(encoding="utf-8"))["seen"])
        assert keys == sorted(keys)


class TestBaseline:
    def test_baseline_records_without_reporting(self, tmp_path):
        path = tmp_path / "s.json"
        state = State.load(path)
        batch = [listing(f"https://x.com/{i}") for i in range(200)]
        assert adopt_baseline(state, batch) == 200
        assert State.load(path).diff(batch).new == []

    def test_a_genuinely_new_posting_still_lands_after_baseline(self, tmp_path):
        path = tmp_path / "s.json"
        state = State.load(path)
        adopt_baseline(state, [listing("https://x.com/1")])
        diff = State.load(path).diff([listing("https://x.com/1"),
                                      listing("https://x.com/2")])
        assert [l.url for l in diff.new] == ["https://x.com/2"]
