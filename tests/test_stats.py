import json
from zoneinfo import ZoneInfo

from journal_bot.stats import RequestStats


def test_request_stats_are_grouped_and_persisted(tmp_path):
    path = tmp_path / "request_stats.json"
    stats = RequestStats(path, ZoneInfo("UTC"))

    stats.record(123, "old_name", "Test User", "/today")
    stats.record(123, "new_name", "Test User", "/today")
    stats.record(123, "new_name", "Test User", "/week")

    restored = RequestStats(path, ZoneInfo("UTC")).get_user(123)

    assert restored is not None
    assert restored.username == "new_name"
    assert restored.full_name == "Test User"
    assert restored.total == 3
    assert restored.commands == {"/today": 2, "/week": 1}
    assert json.loads(path.read_text(encoding="utf-8"))["version"] == 1


def test_request_stats_are_sorted_by_total(tmp_path):
    stats = RequestStats(tmp_path / "request_stats.json", ZoneInfo("UTC"))
    stats.record(20, None, "Second", "/today")
    stats.record(10, "first", "First", "/today")
    stats.record(10, "first", "First", "/week")

    assert [item.user_id for item in stats.all_users()] == [10, 20]
