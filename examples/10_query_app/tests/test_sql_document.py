from app.services.sql_document import command_type, is_write_statement, split_statements, statement_for_cursor


def test_split_statements_ignores_literals_and_comments() -> None:
    sql = "SELECT ';' AS value; -- comment;\nSELECT 2"
    statements = split_statements(sql)
    assert [item.sql for item in statements] == ["SELECT ';' AS value", "-- comment;\nSELECT 2"]


def test_cursor_selection_and_write_classification() -> None:
    sql = "SELECT 1; UPDATE T SET A=2;"
    assert statement_for_cursor(sql, 12).sql.startswith("UPDATE")
    assert statement_for_cursor("SELECT 1;", len("SELECT 1;")).sql == "SELECT 1"
    assert command_type("  delete from T") == "DELETE"
    assert is_write_statement("CREATE TABLE T(ID INT)")


def test_comments_do_not_become_executable_statements() -> None:
    assert split_statements("-- only a comment;\n") == []
