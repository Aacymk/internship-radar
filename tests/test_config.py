import pytest

from radar.config import ConfigError, load


def write(tmp_path, text):
    p = tmp_path / "config.yml"
    p.write_text(text, encoding="utf-8")
    return p


class TestLoading:
    def test_bare_names_and_board_entries_both_work(self, tmp_path):
        cfg = load(write(tmp_path, """
season: "Summer 2027"
companies:
  - Netflix
  - name: Stripe
    board: greenhouse
    board_id: stripe
"""))
        assert [c.name for c in cfg.companies] == ["Netflix", "Stripe"]
        assert cfg.companies[0].board is None
        assert cfg.companies[1].board == ("greenhouse", "stripe")

    def test_season_defaults_when_omitted(self, tmp_path):
        cfg = load(write(tmp_path, "companies:\n  - Netflix\n"))
        assert cfg.season_parts == ("Summer", "2027")

    def test_job_lists_without_a_url_are_dropped(self, tmp_path):
        cfg = load(write(tmp_path, """
companies:
  - Netflix
job_lists:
  - name: good
    url: https://example.com/README.md
  - name: broken
"""))
        assert [j["name"] for j in cfg.job_lists] == ["good"]


class TestErrorsAreReadable:
    """These messages are read by non-technical users in a CI log."""

    def test_missing_file(self, tmp_path):
        with pytest.raises(ConfigError, match="No config file"):
            load(tmp_path / "nope.yml")

    def test_no_companies(self, tmp_path):
        with pytest.raises(ConfigError, match="lists no companies"):
            load(write(tmp_path, 'season: "Summer 2027"\n'))

    def test_unparseable_yaml(self, tmp_path):
        with pytest.raises(ConfigError, match="not valid YAML"):
            load(write(tmp_path, 'companies: "unclosed\n'))

    def test_misindented_list_is_caught_not_silently_merged(self, tmp_path):
        """YAML folds this into ONE company string instead of erroring, so
        without a check the user watches a company that does not exist."""
        with pytest.raises(ConfigError, match="ran together"):
            load(write(tmp_path, "companies:\n - Google\n  - Meta\n"))

    def test_malformed_season(self, tmp_path):
        cfg = load(write(tmp_path, 'season: "sometime"\ncompanies:\n  - A\n'))
        with pytest.raises(ConfigError, match="Summer 2027"):
            _ = cfg.season_parts

    def test_company_entry_without_a_name(self, tmp_path):
        with pytest.raises(ConfigError, match="missing a name"):
            load(write(tmp_path, "companies:\n  - board: greenhouse\n"))
