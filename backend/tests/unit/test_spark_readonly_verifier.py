"""Acceptance cannot mistake unsupported SQL or an outage for authorization."""
import pytest

from scripts.verify_spark_readonly import verify_source, connection_file


class Cursor:
    description = [("id",), ("amount",)]

    def __init__(self, denial="Permission denied", rows=None):
        self.denial = denial
        self.rows = rows if rows is not None else [(1, 7)]
        self.statements = []

    def execute(self, sql):
        self.statements.append(sql)
        if not sql.startswith("SELECT") and self.denial:
            raise RuntimeError(self.denial)

    def fetchall(self):
        return self.rows


def test_readonly_requires_all_denials_and_unchanged_reads():
    owner, reader = Cursor(denial=None), Cursor()
    report = verify_source(reader, owner, "aind_authz_test")
    assert report.passed and len(report.checks) == 7
    assert sum(sql.startswith("SELECT") for sql in reader.statements) == 8
    assert len([sql for sql in owner.statements if sql.startswith("DROP TABLE IF EXISTS")]) == 3
    assert all("aind_authz_test.probe_" in sql for sql in owner.statements)


@pytest.mark.parametrize("denial", [None, "syntax error", "table not found", "timeout", "unsupported operation"])
def test_non_authorization_errors_or_success_never_pass(denial):
    owner = Cursor(denial=None)
    report = verify_source(Cursor(denial=denial), owner, "aind_authz_test")
    assert not report.passed and report.reason.startswith("insert:")
    assert not report.checks
    assert len([sql for sql in owner.statements if sql.startswith("DROP TABLE IF EXISTS")]) == 3


def test_database_guard_prevents_all_source_operations():
    owner, reader = Cursor(), Cursor()
    with pytest.raises(ValueError):
        verify_source(reader, owner, "production")
    assert owner.statements == reader.statements == []


def test_reader_visibility_must_match_seed():
    report = verify_source(Cursor(rows=[]), Cursor(denial=None), "aind_authz_test")
    assert not report.passed and report.reason == "reader_snapshot_mismatch"


def test_cleanup_failure_invalidates_a_successful_probe():
    class CleanupFailure(Cursor):
        def execute(self, sql):
            super().execute(sql)
            if sql.startswith("DROP TABLE"):
                raise RuntimeError("cleanup denied")
    report = verify_source(Cursor(), CleanupFailure(denial=None), "aind_authz_test")
    assert not report.passed and report.reason == "fixture_cleanup_failed"


def test_tls_plain_and_dedicated_catalog_are_required(workdir):
    path = workdir / "unsafe.json"
    path.write_text('{"host":"localhost","user":"reader","password":"test","database":"production","auth_mechanism":"NOSASL"}')
    with pytest.raises(ValueError):
        connection_file(path)
