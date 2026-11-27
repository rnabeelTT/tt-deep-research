import pytest
from starlette.testclient import TestClient

from ttdr import ui_server
from ttdr.history import OWNER, ChatHistory

ALICE, BOB = {OWNER: "alice-browser-1"}, {OWNER: "bob-browser-22"}


class FakeRedis:
    def __init__(self):
        self.strings, self.hashes = {}, {}

    async def get(self, key):
        return self.strings.get(key)

    async def set(self, key, value):
        self.strings[key] = value

    async def hset(self, key, field, value):
        self.hashes.setdefault(key, {})[field] = value

    async def hvals(self, key):
        return list(self.hashes.get(key, {}).values())

    async def hdel(self, key, field):
        self.hashes.get(key, {}).pop(field, None)


@pytest.fixture
def client():
    app = ui_server.build(lambda *_: None, history=ChatHistory(FakeRedis()))
    with TestClient(app) as c:
        yield c


def conversation(cid, title="Capex"):
    return {"id": cid, "title": title, "messages": [{"role": "user", "content": "What was capex?"}]}


def test_each_browser_sees_only_its_own_chats(client):
    assert client.post("/v1/chats/c1", json=conversation("c1"), headers=ALICE).status_code == 200
    client.post("/v1/chats/c2", json=conversation("c2", "Revenue"), headers=BOB)
    assert client.get("/v1/chats", headers=ALICE).json() == {
        "state": None,
        "conversations": [conversation("c1")],
    }
    assert [c["id"] for c in client.get("/v1/chats", headers=BOB).json()["conversations"]] == ["c2"]


def test_a_saved_conversation_is_replaced_then_deleted(client):
    client.post("/v1/chats/c1", json=conversation("c1"), headers=ALICE)
    client.post("/v1/chats/c1", json=conversation("c1", "Capex, 2019"), headers=ALICE)
    [saved] = client.get("/v1/chats", headers=ALICE).json()["conversations"]
    assert saved["title"] == "Capex, 2019"
    client.delete("/v1/chats/c1", headers=ALICE)
    assert client.get("/v1/chats", headers=ALICE).json()["conversations"] == []


def test_the_state_document_round_trips(client):
    state = {"currentUserId": "anon", "currentConversation": "c1", "pendingInteraction": None}
    client.post("/v1/chats", json=state, headers=ALICE)
    assert client.get("/v1/chats", headers=ALICE).json()["state"] == state


@pytest.mark.parametrize("headers", [{}, {OWNER: "short"}, {OWNER: "../../etc/passwd"}])
def test_requests_without_a_valid_owner_are_refused(client, headers):
    assert client.get("/v1/chats", headers=headers).status_code == 400


def test_a_conversation_must_match_its_path(client):
    r = client.post("/v1/chats/c1", json=conversation("c2"), headers=ALICE)
    assert r.status_code == 400


def test_without_history_there_are_no_chat_routes():
    with TestClient(ui_server.build(lambda *_: None)) as c:
        assert c.get("/v1/chats", headers=ALICE).status_code == 404
