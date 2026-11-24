"""The plex.tv calls sign-up makes: what an invite says, what is read back, and what is never read."""
import asyncio

import httpx
import pytest

from tests import mock_signup as M


@pytest.fixture()
def acct():
    from app.plex import PlexAccount
    M.reset()
    return PlexAccount(httpx.AsyncClient(transport=httpx.ASGITransport(app=M.app)), "test-client")


def run(coro):
    return asyncio.run(coro)


def test_libraries_and_shares(acct):
    libs = run(acct.server_sections(M.OWNER_TOKEN, M.MACHINE))
    assert [(x["id"], x["title"]) for x in libs] == [("11", "TV Shows"), ("12", "Movies"), ("14", "4k Movies")]
    shares = run(acct.shares(M.OWNER_TOKEN, M.MACHINE))
    assert shares == [{"id": "500", "user_id": "2001", "username": "sable", "email": "sable@example.com", "sections": ["11", "12"]}]
    assert "SECRET-ACCESS-TOKEN" not in str(shares)   # each record carries that person's token; it is not read


def test_invite_then_accept(acct):
    run(acct.invite(M.OWNER_TOKEN, M.MACHINE, "newbie@example.com", ["11", "12"]))
    body = M.calls[0]["body"]
    assert body["server_id"] == M.MACHINE and body["shared_server"] == {"library_section_ids": [11, 12], "invited_email": "newbie@example.com"}
    sent = run(acct.sent_invites(M.OWNER_TOKEN))
    assert [(i["email"], i["servers"]) for i in sent] == [("newbie@example.com", ["GALAXY"])]
    assert len(run(acct.shares(M.OWNER_TOKEN, M.MACHINE))) == 1   # not a share until accepted
    waiting = run(acct.received_invites("tok-new"))
    assert waiting[0]["username"] == "galaxyowner" and waiting[0]["server"] == "1"
    run(acct.accept_invite("tok-new", waiting[0]))
    assert M.calls[-1]["params"] == {"friend": "1", "home": "0", "server": "1"}
    assert [s["user_id"] for s in run(acct.shares(M.OWNER_TOKEN, M.MACHINE))] == ["2001", "3001"]


def test_an_invite_with_no_libraries_is_never_sent(acct):
    from app.plex import PlexError
    for bad in ([], ["not-a-number"]):
        with pytest.raises(PlexError):
            run(acct.invite(M.OWNER_TOKEN, M.MACHINE, "newbie@example.com", bad))
    with pytest.raises(PlexError):
        run(acct.invite(M.OWNER_TOKEN, M.MACHINE, "", ["11"]))
    assert M.calls == []


def test_errors_say_what_failed_and_never_the_token(acct):
    from app.plex import PlexError
    M.state["fail"]["invite"] = 422
    with pytest.raises(PlexError) as e:
        run(acct.invite(M.OWNER_TOKEN, M.MACHINE, "newbie@example.com", ["11"]))
    assert "HTTP 422" in str(e.value) and M.OWNER_TOKEN not in str(e.value)
    with pytest.raises(PlexError) as e:
        run(acct.received_invites("a-token-plex-does-not-know"))
    assert e.value.status == 401 and "a-token-plex-does-not-know" not in str(e.value)
    with pytest.raises(PlexError):
        run(acct.accept_invite("tok-new", {"id": "../users", "friend": "1"}))
