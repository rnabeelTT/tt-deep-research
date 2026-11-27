import asyncio
from types import SimpleNamespace

import pytest

from ttdr import chat
from ttdr.chat import NEEDS_RESEARCH, Chat, Conversations, Turn, parse_route

REPORT = "# Capex\nCapex was $1.5B [W1a2b3c4d.1]."


class FakeRedis:
    def __init__(self):
        self.lists, self.ttl = {}, {}

    async def lrange(self, key, start, end):
        return list(self.lists.get(key, []))

    async def rpush(self, key, value):
        self.lists.setdefault(key, []).append(value)

    async def ltrim(self, key, start, end):
        self.lists[key] = self.lists[key][start:]

    async def expire(self, key, seconds):
        self.ttl[key] = seconds


class FakeModel:
    """Replies in order; records each request."""

    def __init__(self, *replies):
        self.replies, self.requests = list(replies), []

    def bind(self, **_):
        return self

    async def ainvoke(self, messages):
        self.requests.append(messages)
        return SimpleNamespace(content=self.replies.pop(0))


class FakeHarness:
    def __init__(self):
        self.questions, self.finished = [], []

    async def run(self, question, log=None, events=None):
        self.questions.append(question)
        if events:
            events("report", {"report": REPORT})
        return SimpleNamespace(report=REPORT)

    async def finish(self, text):
        self.finished.append(text)
        return text + "\n\n## Sources"


def turn(chat_, conversation, message, harness=None):
    harness = harness or FakeHarness()
    events = []
    answer = asyncio.run(
        chat_.session(conversation, harness).run(message, events=lambda *e: events.append(e))
    )
    return answer, harness, events


@pytest.fixture
def store():
    return Conversations(FakeRedis(), keep=2)


@pytest.mark.parametrize(
    "reply, route, question",
    [
        ("ROUTE: report\nQUESTION: Shorten the capex answer", "report", "Shorten the capex answer"),
        ("  route: Chat\n", "chat", "hi"),
        ("ROUTE: banana\nQUESTION: x", "research", "x"),
        ("no idea", "research", "hi"),
    ],
)
def test_parse_route(reply, route, question):
    assert parse_route(reply, "hi") == (route, question)


def test_the_first_message_is_researched_as_sent_without_a_routing_call(store):
    model = FakeModel()
    answer, harness, _ = turn(Chat(model, store), "c1", "What was capex?")
    assert harness.questions == ["What was capex?"] and answer == REPORT
    assert not model.requests
    assert asyncio.run(store.turns("c1")) == [Turn("What was capex?", REPORT)]


def test_a_follow_up_about_the_answer_is_answered_from_it_and_cited(store):
    asyncio.run(store.add("c1", Turn("What was capex?", REPORT)))
    model = FakeModel("ROUTE: report\nQUESTION: Shorter capex answer", "$1.5B [W1a2b3c4d.1].")
    answer, harness, events = turn(Chat(model, store), "c1", "shorter")
    assert not harness.questions and harness.finished == ["$1.5B [W1a2b3c4d.1]."]
    assert REPORT in model.requests[1][0]["content"]
    assert events == [("report", {"report": answer})]


def test_a_follow_up_the_answer_lacks_escalates_to_research_on_the_standalone_question(store):
    asyncio.run(store.add("c1", Turn("What was capex?", REPORT)))
    model = FakeModel("ROUTE: report\nQUESTION: What was 3M's 2019 capex?", NEEDS_RESEARCH)
    answer, harness, _ = turn(Chat(model, store), "c1", "and in 2019?")
    assert harness.questions == ["What was 3M's 2019 capex?"] and answer == REPORT


def test_chat_replies_without_research(store):
    asyncio.run(store.add("c1", Turn("What was capex?", REPORT)))
    model = FakeModel("ROUTE: chat\nQUESTION: thanks", "You're welcome.")
    answer, harness, _ = turn(Chat(model, store), "c1", "thanks")
    assert answer == "You're welcome." and not harness.questions and not harness.finished
    assert model.requests[1][0]["content"] == chat.ABOUT


def test_conversations_keep_their_last_turns_as_long_as_the_evidence(store):
    for i in range(3):
        asyncio.run(store.add("c1", Turn(f"q{i}", f"a{i}")))
    assert [t.question for t in asyncio.run(store.turns("c1"))] == ["q1", "q2"]
    assert store.redis.ttl["chat:c1"] == chat.TTL_S


def test_without_a_conversation_nothing_is_stored(store):
    turn(Chat(FakeModel(), store), None, "What was capex?")
    assert not store.redis.lists
