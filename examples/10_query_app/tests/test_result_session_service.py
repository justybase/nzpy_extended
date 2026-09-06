from pathlib import Path

from app.services.result_session_service import ResultSessionManager


def test_result_session_pages_filters_and_preserves_numeric_sort(tmp_path: Path) -> None:
    manager = ResultSessionManager(tmp_path, ttl_seconds=600, default_page_size=2)
    manifest = manager.create("q1", "r1", 0, 1, [{"ColumnName": "amount", "DataType": "DECIMAL"}, {"ColumnName": "name", "DataType": "VARCHAR"}])
    manager.append(manifest["session_id"], [["100.25", "b"], ["2.5", "a"], [None, "c"]])
    manager.complete(manifest["session_id"])
    page = manager.page(manifest["session_id"], limit=2, sorting=[{"columnIndex": 0, "desc": False}])
    assert page["rows"] == [[None, "c"], ["2.5", "a"]]
    filtered = manager.page(manifest["session_id"], global_filter="b")
    assert filtered["rows"] == [["100.25", "b"]]
    manager.close_all()
    restored = ResultSessionManager(tmp_path, ttl_seconds=600, default_page_size=2)
    assert restored.manifest(manifest["session_id"])["completed"] is True
    assert restored.page(manifest["session_id"], offset=1, limit=1)["rows"] == [["2.5", "a"]]
