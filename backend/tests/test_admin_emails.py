"""ADMIN_PLEX_EMAIL can name several administrators (Glenn, Nov 16 2026)."""


def test_several_admin_emails(monkeypatch):
    from app.auth import admin_plex_email, is_admin_email
    monkeypatch.setenv("ADMIN_PLEX_EMAIL", " Cluster@Mail.com , huboiler@gmail.com,")
    assert is_admin_email("cluster@mail.com") and is_admin_email("HUBOILER@gmail.com ")
    assert not is_admin_email("someone@x.com") and not is_admin_email(None) and not is_admin_email("")
    assert admin_plex_email() == "cluster@mail.com, huboiler@gmail.com"
    monkeypatch.setenv("ADMIN_PLEX_EMAIL", "cluster@mail.com")
    assert is_admin_email("cluster@mail.com") and not is_admin_email("huboiler@gmail.com")
    monkeypatch.delenv("ADMIN_PLEX_EMAIL")
    assert admin_plex_email() is None and not is_admin_email("cluster@mail.com")
