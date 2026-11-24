"""Sign-up through the real app: what the public can reach, what only admins can, and the headers on each page."""
import json
import sys

import httpx
import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from tests import mock_signup as M

H = {"X-Requested-With": "greystar"}


@pytest.fixture()
def app(tmp_path, monkeypatch):
    monkeypatch.setenv("SECRET_KEY", Fernet.generate_key().decode())
    monkeypatch.setenv("ADMIN_PLEX_EMAIL", "cluster@mail.com")
    monkeypatch.setenv("DB_PATH", str(tmp_path / "t.db"))
    web = tmp_path / "web"
    web.mkdir()
    for name in ("signup.html", "login.html", "index.html"):
        (web / name).write_text("<!doctype html><title>x</title>")
    monkeypatch.setenv("WEB_DIR", str(web))
    for m in ["app.main", "app.config"]:
        sys.modules.pop(m, None)
    import app.main as A
    from app import auth
    auth._failures.clear()
    M.reset()
    A.signup.http = httpx.AsyncClient(transport=httpx.ASGITransport(app=M.app))
    A.galaxy = A.store.add_server({"name": "GALAXY", "plex_url": "http://galaxy.test:32400", "machine_id": M.MACHINE, "owner_plex_id": "900"}, "srv-token")
    A.store.save_account({"id": "900", "username": "galaxyowner", "title": "Galaxy Owner", "email": "owner@greystar.test"}, M.OWNER_TOKEN)
    yield A
    for m in ["app.main", "app.config"]:
        sys.modules.pop(m, None)


def signed_in(A, plex_id, email):
    from app.auth import new_session
    A.directory.upsert_user({"id": plex_id, "username": plex_id, "email": email}, shared=True)
    c = TestClient(A.app)
    c.cookies.set("gst_session", new_session(A.directory, "plex", plex_id))
    return c


def admin(A):
    return signed_in(A, "9001", "Cluster@Mail.com")


READY = {"stripe_key": M.STRIPE_KEY, "publishable_key": M.PUBLISHABLE, "sections": ["11", "12"], "price_id": M.PRICE, "cap": 100}


def set_up(A, **over):
    a = admin(A)
    r = a.put("/api/settings/signup", json={**READY, "server_id": A.galaxy, **over}, headers=H)
    assert r.status_code == 200, r.text
    r = a.put("/api/settings/signup", json={"enabled": True}, headers=H)
    assert r.status_code == 200, r.text
    return a


def plex_sign_in(c, token="tok-new"):
    pin = c.post("/api/signup/plex/start", headers=H).json()
    assert c.get(f"/api/signup/plex/check/{pin['pin_id']}").json() == {"pending": True}
    M.approve(pin["pin_id"], token)
    return c.get(f"/api/signup/plex/check/{pin['pin_id']}")


def test_closed_until_an_admin_turns_it_on(app):
    c = TestClient(app.app)
    cfg = c.get("/api/signup/config").json()
    assert cfg["open"] is False and cfg["reason"] == "off" and cfg["plan"] is None and cfg["publishable_key"] is None
    assert c.get("/api/auth/options").json()["signup"] is False
    assert c.post("/api/signup/plex/start", headers=H).status_code == 403
    assert c.post("/api/signup/checkout", headers=H).status_code == 401 and c.post("/api/signup/finish", headers=H).status_code == 401
    assert c.get("/api/signup/status").json() == {"step": "plex"}


def test_from_nothing_to_signed_in(app):
    a = set_up(app)
    c = TestClient(app.app)
    cfg = c.get("/api/signup/config").json()
    assert cfg["open"] is True and cfg["plan"]["amount"] == 1599 and cfg["plan"]["interval"] == "month"
    assert cfg["publishable_key"] == M.PUBLISHABLE and cfg["server"] == "GALAXY"
    assert c.get("/api/auth/options").json()["signup"] is True
    r = plex_sign_in(c)
    assert r.json() == {"name": "New Bie", "email": "newbie@example.com", "server": "GALAXY", "step": "pay"}
    assert "httponly" in r.headers["set-cookie"].lower() and "path=/api/signup/" in r.headers["set-cookie"].lower()
    assert c.post("/api/signup/finish", headers=H).status_code == 409   # not before they are on the server
    out = c.post("/api/signup/checkout", headers=H).json()
    assert out["publishable_key"] == M.PUBLISHABLE and out["client_secret"].endswith("_secret_abc")
    assert c.get("/api/signup/status").json()["step"] == "pay"
    M.pay(next(iter(M.state["sessions"])))
    row = app.signup.rows()[0]
    app.signup._set(row["id"], checked=0)   # the page asks again a few seconds later
    assert c.get("/api/signup/status").json()["step"] == "done"
    assert c.get("/api/me").status_code == 401   # not signed in to the dashboard until they ask
    assert c.post("/api/signup/finish", headers=H).json() == {"ok": True}
    me = c.get("/api/me").json()
    assert me["role"] == "viewer" and me["name"] == "New Bie" and "requests" in me["sections"]
    assert c.get("/signup.html", follow_redirects=False).headers["location"] == "/"   # members have no use for the sign-up page
    assert c.post("/api/signup/finish", headers=H).status_code == 401   # the sign-up cookie is spent
    listed = a.get("/api/settings/signup").json()
    assert listed["counts"]["active"] == 1 and listed["rows"][0]["state"] == "active" and listed["rows"][0]["email"] == "newbie@example.com"
    assert a.get("/api/settings/summary").json()["signup"] == {"enabled": True, "ready": True, "counts": listed["counts"], "server": "GALAXY"}


def test_members_and_payers_are_sent_to_sign_in_not_to_pay(app):
    set_up(app)
    c = TestClient(app.app)
    r = plex_sign_in(c, "tok-old")
    assert r.json() == {"step": "member"} and "set-cookie" not in r.headers
    assert c.post("/api/signup/checkout", headers=H).status_code == 401 and M.state["sessions"] == {}


def test_full_means_no_payment_form(app):
    set_up(app, cap=1)
    c = TestClient(app.app)
    cfg = c.get("/api/signup/config").json()
    assert cfg["open"] is False and cfg["reason"] == "full" and "full" in cfg["message"]
    assert plex_sign_in(c).json()["step"] == "closed"
    assert c.post("/api/signup/checkout", headers=H).status_code == 401 and M.state["sessions"] == {}


def test_filling_up_while_someone_is_on_the_page(app):
    a = set_up(app)
    c = TestClient(app.app)
    plex_sign_in(c)
    a.put("/api/settings/signup", json={"cap": 1}, headers=H)
    app.signup._capacity = None
    assert c.post("/api/signup/checkout", headers=H).status_code == 409 and M.state["sessions"] == {}


def test_a_sign_in_can_only_be_finished_by_the_browser_that_started_it(app):
    set_up(app)
    c, thief = TestClient(app.app), TestClient(app.app)
    pin = c.post("/api/signup/plex/start", headers=H).json()
    M.approve(pin["pin_id"], "tok-new")
    assert thief.get(f"/api/signup/plex/check/{pin['pin_id']}").status_code == 400
    assert c.get(f"/api/signup/plex/check/{pin['pin_id']}").json()["step"] == "pay"
    assert thief.get("/api/signup/status").json() == {"step": "plex"}


def test_posts_need_the_site_header_and_starts_are_limited(app):
    set_up(app)
    c = TestClient(app.app)
    assert c.post("/api/signup/plex/start").status_code == 403
    codes = [c.post("/api/signup/plex/start", headers={**H, "X-Forwarded-For": f"10.0.0.{i}, 203.0.113.9"}).status_code for i in range(22)]
    assert codes[:20] == [200] * 20 and codes[20:] == [429, 429]


def test_stripe_trouble_is_not_shown_raw(app):
    set_up(app)
    c = TestClient(app.app)
    plex_sign_in(c)
    M.state["fail"]["checkout"] = 402
    r = c.post("/api/signup/checkout", headers=H)
    assert r.status_code == 502 and "mock" not in r.text and "payment form" in r.json()["detail"]


def test_only_admins_reach_the_settings(app):
    set_up(app)
    row_id = "nope"
    v, anon = signed_in(app, "2001", "viewer@x.com"), TestClient(app.app)
    for path in ("/api/settings/signup", f"/api/settings/signup/libraries?server_id={app.galaxy}", "/api/settings/signup/prices"):
        assert v.get(path).status_code == 403 and anon.get(path).status_code == 401, path
    for method, path in (("put", "/api/settings/signup"), ("post", "/api/settings/signup/test"), ("post", f"/api/settings/signup/{row_id}/retry"),
                         ("delete", f"/api/settings/signup/{row_id}")):
        kw = {"json": {"enabled": False}} if method == "put" else {}
        assert getattr(v, method)(path, headers=H, **kw).status_code == 403, path
        assert getattr(anon, method)(path, headers=H, **kw).status_code == 401, path
    assert app.signup.settings()["enabled"] is True


def test_settings_never_hand_back_a_secret(app):
    a = set_up(app)
    c = TestClient(app.app)
    plex_sign_in(c)
    c.post("/api/signup/checkout", headers=H)
    text = a.get("/api/settings/signup").text + c.get("/api/signup/status").text + c.get("/api/signup/config").text
    assert '"has_key":true' in text.replace(" ", "") and '"mode":"test"' in text.replace(" ", "")
    for secret in (M.STRIPE_KEY, M.OWNER_TOKEN, "tok-new", "srv-token", "token_hash", "plex_token"):
        assert secret not in text


def test_settings_refuse_what_would_not_work(app):
    a = admin(app)
    put = lambda body: a.put("/api/settings/signup", json=body, headers=H)
    assert put({"enabled": True}).status_code == 422                                   # nothing set up yet
    assert put({"stripe_key": "pk_test_wrongkind"}).status_code == 422
    assert put({"stripe_key": "rk_test_not_the_key"}).status_code == 422                # Stripe says no
    assert put({"publishable_key": "sk_test_oops"}).status_code == 422
    assert put({"server_id": 999}).status_code == 404
    off = app.store.add_server({"name": "OFF", "plex_url": "http://off.test:32400", "machine_id": "m-off", "owner_plex_id": "900", "enabled": 0}, "t")
    nobody = app.store.add_server({"name": "NOBODY", "plex_url": "http://n.test:32400", "machine_id": "m-n", "owner_plex_id": "12345"}, "t")
    assert put({"server_id": off}).status_code == 422 and put({"server_id": nobody}).status_code == 422
    assert put({"sections": ["11"]}).status_code == 422                                 # no server chosen yet
    assert put({"server_id": app.galaxy, "sections": []}).status_code == 422
    assert put({"sections": ["11", "999"]}).status_code == 422                          # not a library of this server
    assert put({"stripe_key": M.STRIPE_KEY, "price_id": "price_nope"}).status_code == 422
    assert put({"price_id": "price_once"}).status_code == 422                           # a one-time price is not a subscription
    assert put({"cap": 101}).status_code == 422 and put({"cap": 0}).status_code == 422  # Plex stops at 100
    r = put({"sections": ["11", "12"], "price_id": M.PRICE, "publishable_key": "pk_live_other"})
    assert r.status_code == 200 and any("same mode" in p for p in r.json()["problems"])
    assert put({"enabled": True}).status_code == 422
    assert put({"publishable_key": M.PUBLISHABLE, "enabled": True}).json()["enabled"] is True
    assert put({"publishable_key": ""}).json()["enabled"] is False                      # breaking the setup closes the door


def test_changing_server_drops_the_old_servers_libraries(app):
    a = set_up(app)
    other = app.store.add_server({"name": "OTHER", "plex_url": "http://o.test:32400", "machine_id": "m-o", "owner_plex_id": "900"}, "t")
    s = a.put("/api/settings/signup", json={"server_id": other}, headers=H).json()
    assert s["sections"] == [] and s["enabled"] is False


def test_the_test_button_charges_nobody(app):
    a = set_up(app)
    out = a.post("/api/settings/signup/test", headers=H).json()
    assert out["stripe"] == {"ok": True, "livemode": False}
    assert out["plex"]["ok"] is True and out["plex"]["sharing"] == ["TV Shows", "Movies"] and out["plex"]["not_sharing"] == ["4k Movies"]
    assert out["capacity"]["used"] == 1 and out["capacity"]["cap"] == 100
    assert [s["status"] for s in M.state["sessions"].values()] == ["expired"]
    assert app.signup.rows() == [] and [c for c in M.calls if c["what"] == "invite"] == []
    libs = a.get(f"/api/settings/signup/libraries?server_id={app.galaxy}").json()["libraries"]
    assert [x["title"] for x in libs] == ["TV Shows", "Movies", "4k Movies"]
    prices = a.get("/api/settings/signup/prices").json()["prices"]
    assert [p["id"] for p in prices] == [M.PRICE] and prices[0]["name"] == "Standard Monthly"


def test_retry_and_forget(app):
    a = set_up(app)
    c = TestClient(app.app)
    plex_sign_in(c)
    c.post("/api/signup/checkout", headers=H)
    sid = app.signup.rows()[0]["id"]
    assert a.post(f"/api/settings/signup/{sid}/retry", headers=H).json()["row"]["state"] == "checkout_open"
    M.pay(next(iter(M.state["sessions"])))
    M.state["fail"]["invite"] = 500
    assert a.post(f"/api/settings/signup/{sid}/retry", headers=H).json()["row"]["state"] == "paid"
    assert a.delete(f"/api/settings/signup/{sid}", headers=H).status_code == 409   # paid and not in: not dropped by accident
    M.state["fail"].clear()
    assert a.post(f"/api/settings/signup/{sid}/retry", headers=H).json()["row"]["state"] == "active"
    assert a.post(f"/api/settings/signup/{sid}/retry", headers=H).status_code == 409
    assert a.delete(f"/api/settings/signup/{sid}", headers=H).json() == {"ok": True} and app.signup.rows() == []
    assert [s["userID"] for s in M.state["shares"]] == ["2001", "3001"]   # forgetting the record leaves the share alone
    assert a.delete(f"/api/settings/signup/{sid}", headers=H).status_code == 404


def test_only_the_sign_up_page_may_load_stripe(app):
    c = TestClient(app.app)
    strict = c.get("/login.html")
    assert strict.status_code == 200
    assert strict.headers["content-security-policy"] == app.CSP and strict.headers["permissions-policy"] == app.PERMISSIONS
    assert "stripe" not in app.CSP and "payment=()" in app.PERMISSIONS
    for path in ("/healthz", "/api/signup/config", "/signup.js"):
        assert "stripe" not in c.get(path).headers["content-security-policy"], path
    page = c.get("/signup.html")
    csp = page.headers["content-security-policy"]
    assert page.status_code == 200
    assert "script-src 'self' https://js.stripe.com" in csp and "frame-src https://js.stripe.com" in csp and "https://api.stripe.com" in csp
    assert "frame-ancestors 'none'" in csp and "object-src 'none'" in csp and "unsafe-eval" not in csp and "script-src 'self' 'unsafe-inline'" not in csp
    assert 'payment=(self "https://js.stripe.com"' in page.headers["permissions-policy"] and "camera=()" in page.headers["permissions-policy"]
    assert page.headers["x-frame-options"] == "DENY"


def test_a_short_plex_hiccup_does_not_page_the_admins(app):
    set_up(app)
    c = TestClient(app.app)
    plex_sign_in(c)
    c.post("/api/signup/checkout", headers=H)
    M.pay(next(iter(M.state["sessions"])))
    M.state["fail"]["invite"] = 503
    sid = app.signup.rows()[0]["id"]
    app.signup._set(sid, checked=0)
    for _ in range(6):   # the page asks every three seconds
        v = c.get("/api/signup/status").json()
    row = app.signup.get(sid)
    assert row["state"] == "paid" and row["attempts"] == 1 and not row["alerted"] and v["stuck"] is False
    M.state["fail"].clear()
    app.signup._set(sid, checked=0)
    assert c.get("/api/signup/status").json()["step"] == "done"
