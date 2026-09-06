from app.services.sql_safety import SqlSafetyService


def test_write_requires_single_use_preview_token() -> None:
    service = SqlSafetyService(60)
    preview = service.preview("UPDATE T SET A=1", "JUST_DATA")
    assert preview["containsWrite"] is True
    assert service.validate(preview["previewToken"], "UPDATE T SET A=1", "JUST_DATA", True)
    assert not service.validate(preview["previewToken"], "UPDATE T SET A=1", "JUST_DATA", True)


def test_write_detection_ignores_leading_comments() -> None:
    service = SqlSafetyService(60)
    preview = service.preview("-- explain first\nDROP TABLE T", "JUST_DATA")
    assert preview["containsWrite"] is True


def test_select_does_not_require_confirmation() -> None:
    service = SqlSafetyService()
    assert service.validate(None, "SELECT 1", "JUST_DATA", False)
