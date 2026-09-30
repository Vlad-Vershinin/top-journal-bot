import json
from zoneinfo import ZoneInfo

from journal_bot.bot import ScheduleBot
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
    assert restored.sources == {"command": 3}
    assert json.loads(path.read_text(encoding="utf-8"))["version"] == 1


def test_request_stats_are_sorted_by_total(tmp_path):
    stats = RequestStats(tmp_path / "request_stats.json", ZoneInfo("UTC"))
    stats.record(20, None, "Second", "/today")
    stats.record(10, "first", "First", "/today")
    stats.record(10, "first", "First", "/week")

    assert [item.user_id for item in stats.all_users()] == [10, 20]


def test_legacy_statistics_keep_totals_and_show_unknown_sources(tmp_path):
    path = tmp_path / "request_stats.json"
    stats = RequestStats(path, ZoneInfo("UTC"))
    stats.record(123, "test", "Test", "/today")
    payload = json.loads(path.read_text(encoding="utf-8"))
    del payload["users"]["123"]["sources"]
    path.write_text(json.dumps(payload), encoding="utf-8")
    stats.record(123, "test", "Test", "/week", source="keyboard")
    restored = stats.get_user(123)
    assert restored.total == 2
    assert restored.commands == {"/today": 1, "/week": 1}
    assert restored.sources == {"keyboard": 1}
    assert ScheduleBot._stats_source_lines([restored]) == [
        "С клавиатуры: 1", "Командами: 0", "Ранее без разделения: 1"
    ]
