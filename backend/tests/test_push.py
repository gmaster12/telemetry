"""Phone & computer alerts (Web Push, app/push.py) and members' issue-reply notices (notify.member_issues_tick)."""
import asyncio
import base64
import json
import struct
import sys

import httpx
import pytest
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from fastapi.testclient import TestClient

from app.push import Push, allowed_endpoint, b64u, encrypt, unb64u


def browser():
    """A browser's subscription keys: (private key, p256dh, auth)."""
    k = ec.generate_private_key(ec.SECP256R1())
    pub = k.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    return k, b64u(pub), b64u(b"0123456789abcdef")


def decrypt(body: bytes, ua_key, p256dh: str, auth: str) -> bytes:
    """What the browser does (RFC 8291), written separately from push.encrypt."""
    salt, rs, idlen = body[:16], struct.unpack("!I", body[16:20])[0], body[20]
    as_pub = body[21:21 + idlen]
    assert rs == 4096 and idlen == 65
    secret = ua_key.exchange(ec.ECDH(), ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), as_pub))
    h = lambda salt_, ikm, info, n: HKDF(hashes.SHA256(), n, salt_, info).derive(ikm)
    ikm = h(unb64u(auth), secret, b"WebPush: info\x00" + unb64u(p256dh) + as_pub, 32)
    plain = AESGCM(h(salt, ikm, b"Content-Encoding: aes128gcm\x00", 16)).decrypt(h(salt, ikm, b"Content-Encoding: nonce\x00", 12), body[21 + idlen:], None)
    assert plain.endswith(b"\x02")
    return plain[:-1]


def test_encryption_is_what_a_browser_can_read():
    k, pub, auth = browser()
    msg = json.dumps({"title": "Dune is ready", "body": "On Plex now."}).encode()
    assert decrypt(encrypt(msg, pub, auth), k, pub, auth) == msg


def test_only_known_push_services_are_accepted():
    for ok in ("https://fcm.googleapis.com/fcm/send/abc", "https://web.push.apple.com/QX", "https://updates.push.services.mozilla.com/wpush/v2/x",
               "https://wns2-bl2p.notify.windows.com/w/?token=x"):
        assert allowed_endpoint(ok), ok
    for bad in ("http://fcm.googleapis.com/x", "https://127.0.0.1/x", "https://evil.com/fcm.googleapis.com", "https://fcm.googleapis.com.evil.com/x",
                "https://169.254.169.254/latest"):
        assert not allowed_endpoint(bad), bad


class FakeService:
    def __init__(self):
        self.got = []
        self.status = {}

    def handler(self, req: httpx.Request):
        self.got.append(req)
        return httpx.Response(self.status.get(str(req.url), 201))


@pytest.fixture()
def app(tmp_path, monkeypatch):
    monkeypatch.setenv("SECRET_KEY", Fernet.generate_key().decode())
    monkeypatch.setenv("ADMIN_PLEX_EMAIL", "boss@x.com")
    monkeypatch.setenv("DB_PATH", str(tmp_path / "t.db"))
    monkeypatch.setenv("WEB_DIR", str(tmp_path / "web"))
    (tmp_path / "web").mkdir()
    (tmp_path / "web" / "sw.js").write_text("self.addEventListener('push', function () {});")
    for m in ["app.main", "app.config"]:
        sys.modules.pop(m, None)
    import app.main as M
    from app import auth
    auth._failures.clear()
    yield M
    for m in ["app.main", "app.config"]:
        sys.modules.pop(m, None)


def client(M, pid, email):
    from app.auth import new_session
    M.directory.upsert_user({"id": pid, "username": pid, "email": email}, shared=True)
    c = TestClient(M.app)
    c.cookies.set("gst_session", new_session(M.directory, "plex", pid))
    return c


H = {"X-Requested-With": "greystar"}


def sub_body(name):
    k, pub, auth = browser()
    return k, {"endpoint": f"https://fcm.googleapis.com/fcm/send/{name}", "p256dh": pub, "auth": auth, "device": f"{name} phone"}


def test_vapid_signature_checks_out(app):
    M = app
    jwt = M.push._vapid("https://fcm.googleapis.com/fcm/send/x", 1000)
    t, k = jwt.split("t=")[1].split(", k=")
    head, claims, sig = t.split(".")
    assert json.loads(unb64u(claims)) == {"aud": "https://fcm.googleapis.com", "exp": 1000 + 12 * 3600, "sub": "mailto:admin@greystarnetworks.com"}
    raw = unb64u(sig)
    pub = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), unb64u(k))
    pub.verify(encode_dss_signature(int.from_bytes(raw[:32], "big"), int.from_bytes(raw[32:], "big")), f"{head}.{claims}".encode(), ec.ECDSA(hashes.SHA256()))
    assert k == M.push.public_key() and M.store.get_setting("push_vapid_private").startswith("-----BEGIN PRIVATE KEY")
    raw_db = M.store.db.execute("SELECT value FROM settings WHERE key = 'push_vapid_private'").fetchone()[0]
    assert "BEGIN PRIVATE KEY" not in raw_db   # sealed at rest


def test_turn_on_test_choices_and_routing(app):
    M = app
    svc = FakeService()
    M.push.http = httpx.AsyncClient(transport=httpx.MockTransport(svc.handler))
    amy, bob, boss = client(M, "2001", "amy@x.com"), client(M, "2002", "bob@x.com"), client(M, "9001", "boss@x.com")
    ka, sa = sub_body("amy")
    kb, sb = sub_body("bob")
    kx, sx = sub_body("boss")
    assert amy.post("/api/push/subscribe", json={**sa, "endpoint": "https://evil.com/x"}, headers=H).status_code == 400
    for c, s in ((amy, sa), (bob, sb), (boss, sx)):
        assert c.post("/api/push/subscribe", json=s, headers=H).status_code == 200
    cfg = amy.get("/api/push", params={"endpoint": sa["endpoint"]}).json()
    assert cfg["thisDevice"] is True and cfg["publicKey"] == M.push.public_key() and len(cfg["devices"]) == 1
    assert cfg["choices"]["ready"] is True and cfg["choices"]["started"] is False and "admin" not in cfg["choices"]
    assert "admin" in boss.get("/api/push").json()["choices"]
    # A test goes to the person's own device only, and the browser can read it.
    r = amy.post("/api/push/test", headers=H).json()
    assert r["results"] == [{"device": "amy phone", "ok": True, "status": 201}]
    req = svc.got[-1]
    assert str(req.url) == sa["endpoint"] and req.headers["Content-Encoding"] == "aes128gcm" and req.headers["Authorization"].startswith("vapid t=")
    assert json.loads(decrypt(req.content, ka, sa["p256dh"], sa["auth"]))["title"] == "Greystar test alert"
    # News from the Notifier: Amy's ready goes to Amy; her "started" is off by default; admin news goes to the admin.
    svc.got.clear()
    M.notifier.post("plex:2001", "seerr:1:ready", "ready", "Dune is ready", "On Plex now.", "Open", "#/requests/movie/1")
    M.notifier.post("plex:2001", "seerr:2:started", "started", "Alien started downloading", "x", "Track it", "#/requests/movie/2")
    M.notifier.post("admins", "server:1", "server", "ATLAS is not answering", "x", "Open", "#/servers", urgent=True)
    M.notifier.post("plex:2001", "seerr:1:ready", "ready", "Dune is ready", "On Plex now.", "Open", "#/requests/movie/1")  # unchanged: not news
    assert asyncio.run(M.push.flush()) == 2
    sent = {str(x.url): json.loads(decrypt(x.content, *( (ka, sa["p256dh"], sa["auth"]) if "amy" in str(x.url) else (kx, sx["p256dh"], sx["auth"])))) for x in svc.got}
    assert sent[sa["endpoint"]] == {"title": "Dune is ready", "body": "On Plex now.", "url": "/#/requests/movie/1", "tag": "seerr:1:ready", "kind": "ready"}
    assert sent[sx["endpoint"]]["title"] == "ATLAS is not answering" and sb["endpoint"] not in sent
    # Amy turns "started" on; a dropped subscription (410) is forgotten.
    assert amy.put("/api/push/choices", json={"choices": {"started": True}}, headers=H).json()["choices"]["started"] is True
    svc.status[sa["endpoint"]] = 410
    M.notifier.post("plex:2001", "seerr:3:started", "started", "Heat started downloading", "x", "Track it", "#/requests/movie/3")
    asyncio.run(M.push.flush())
    assert amy.get("/api/push").json()["devices"] == []
    # Off for a device; someone else's device cannot be removed.
    bid = bob.get("/api/push").json()["devices"][0]["id"]
    assert amy.delete(f"/api/push/devices/{bid}", headers=H).status_code == 404
    assert bob.post("/api/push/unsubscribe", json={"endpoint": sb["endpoint"]}, headers=H).json() == {"ok": True}


def test_service_worker_is_public_and_never_cached(app):
    r = TestClient(app.app).get("/sw.js")
    assert r.status_code == 200 and r.headers["cache-control"] == "no-cache" and r.headers["service-worker-allowed"] == "/"


# ---------- issue replies for the member who reported it ----------
class FakeSeerr:
    def __init__(self):
        self.issues = {}

    def configured(self):
        return True

    def client(self):
        outer = self

        class C:
            async def call(self, method, path, params=None, body=None):
                if path == "/issue":
                    return {"results": [{k: v for k, v in i.items() if k != "comments"} | {"comments": [{"id": c["id"]} for c in i["comments"]]}
                                        for i in outer.issues.values()]}
                return outer.issues[int(path.rsplit("/", 1)[1])]
        return C()

    async def title_of(self, kind, tmdb):
        return {"title": "Dune", "poster": None}


def issue(iid, comments, status=1, updated="2026-11-23T01:00:00.000Z", created="2026-11-20T01:00:00.000Z"):
    return {"id": iid, "status": status, "createdBy": {"id": 7}, "createdAt": created, "updatedAt": updated, "issueType": 1,
            "media": {"mediaType": "movie", "tmdbId": 438631}, "problemSeason": 0, "problemEpisode": 0,
            "comments": [{"id": cid, "user": {"id": uid}, "message": msg} for cid, uid, msg in comments]}


def test_member_hears_replies_and_the_fix_once(app):
    M = app
    from app.notify import Notifier
    seerr = FakeSeerr()
    n = Notifier(M.store, seerr=seerr)
    M.seerr.save_link("2001", 7, "matched")   # member 2001 is Seerr user 7, who reports the problems below
    mine = lambda: [r["title"] for r in M.store.db.execute("SELECT title FROM notifications WHERE who = 'plex:2001' ORDER BY id")]
    seerr.issues[1] = issue(1, [(10, 7, "No sound"), (11, 1, "Old reply")])
    asyncio.run(n.member_issues_tick(now=2_000_000_000))      # first run: where things stand, nothing said
    assert mine() == []
    seerr.issues[1] = issue(1, [(10, 7, "No sound"), (11, 1, "Old reply"), (12, 7, "Still broken"), (13, 1, "Replaced the file")],
                            updated="2026-11-23T02:00:00.000Z")
    asyncio.run(n.member_issues_tick(now=2_000_000_100))
    assert mine() == ["Reply about your report on Dune"]       # the admin's reply only, not their own comment
    seerr.issues[1] = issue(1, [(10, 7, "No sound"), (11, 1, "Old reply"), (12, 7, "Still broken"), (13, 1, "Replaced the file")],
                            status=2, updated="2026-11-23T03:00:00.000Z")
    asyncio.run(n.member_issues_tick(now=2_000_000_200))
    asyncio.run(n.member_issues_tick(now=2_000_000_300))      # nothing changed: nothing again
    assert mine() == ["Reply about your report on Dune", "Fixed: the problem you reported with Dune"]
    body = M.store.db.execute("SELECT body FROM notifications WHERE key = 'missue:1:c13'").fetchone()[0]
    assert body == "\u201cReplaced the file\u201d"
    # A new issue made after the first run: an admin reply on it is news straight away.
    seerr.issues[2] = issue(2, [(20, 7, "Wrong movie"), (21, 1, "Fixed it")], created="2033-06-01T00:00:00.000Z", updated="2033-06-01T01:00:00.000Z")
    asyncio.run(n.member_issues_tick(now=2_000_000_400))
    assert mine()[-1] == "Reply about your report on Dune"
