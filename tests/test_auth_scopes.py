from mcp_google_workspace.auth import google_auth


def test_default_scopes_include_new_services_and_exclude_meet(monkeypatch):
    monkeypatch.delenv("ENABLE_CHAT", raising=False)
    monkeypatch.delenv("ENABLE_KEEP", raising=False)
    monkeypatch.delenv("ENABLE_MEET", raising=False)

    scopes = google_auth.get_google_scopes()

    assert set(google_auth.SHEETS_SCOPES).issubset(scopes)
    assert set(google_auth.DOCS_SCOPES).issubset(scopes)
    assert set(google_auth.TASKS_SCOPES).issubset(scopes)
    assert set(google_auth.PEOPLE_SCOPES).issubset(scopes)
    assert set(google_auth.FORMS_SCOPES).issubset(scopes)
    assert set(google_auth.SLIDES_SCOPES).issubset(scopes)
    assert set(google_auth.MEET_SCOPES).isdisjoint(scopes)


def test_meet_scopes_are_flagged(monkeypatch):
    monkeypatch.setenv("ENABLE_MEET", "true")

    scopes = google_auth.get_google_scopes()

    assert set(google_auth.MEET_SCOPES).issubset(scopes)


def test_delete_cached_token_removes_only_the_resolved_token(monkeypatch, tmp_path):
    credentials = tmp_path / "credentials.json"
    token = tmp_path / "token.json"
    credentials.write_text("{}", encoding="utf-8")
    token.write_text("cached-token", encoding="utf-8")
    monkeypatch.setenv("MCP_CREDENTIALS_DIR", str(tmp_path))

    assert google_auth.delete_cached_token() is True
    assert not token.exists()
    assert credentials.exists()
