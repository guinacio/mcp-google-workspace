from mcp_google_workspace.auth import google_auth
from mcp_google_workspace.auth.identity import Principal
from cryptography.fernet import Fernet


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


def test_timezone_dependent_capabilities_use_narrow_calendar_settings_scope():
    gmail = google_auth.get_google_scopes(["gmail"])
    sheets = google_auth.get_google_scopes(["sheets"])

    assert set(google_auth.ACCOUNT_TIMEZONE_SCOPES).issubset(gmail)
    assert set(google_auth.CALENDAR_SCOPES).isdisjoint(gmail)
    assert set(google_auth.ACCOUNT_TIMEZONE_SCOPES).isdisjoint(sheets)


def test_delete_cached_token_removes_only_the_authenticated_users_token(monkeypatch, tmp_path):
    credentials = tmp_path / "credentials.json"
    credentials.write_text("{}", encoding="utf-8")
    monkeypatch.setenv("MCP_CREDENTIALS_DIR", str(tmp_path))
    monkeypatch.setenv("MCP_TOKEN_ENCRYPTION_KEY", Fernet.generate_key().decode())
    primary = Principal(issuer="https://issuer.example", subject="primary")
    other = Principal(issuer="https://issuer.example", subject="other")
    store = google_auth.get_token_store()
    store.save_credentials_json(primary, '{"token":"primary"}')
    store.save_credentials_json(other, '{"token":"other"}')
    monkeypatch.setattr(google_auth, "current_principal", lambda: primary)

    assert google_auth.delete_cached_token() is True
    assert store.load_credentials_json(primary) is None
    assert store.load_credentials_json(other) == '{"token":"other"}'
    assert credentials.exists()
