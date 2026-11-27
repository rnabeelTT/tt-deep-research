"""Follow-up turns for the Deep Agents harness, routed the way AI-Q's chat researcher routes them.

Each message is routed by one short model call: `chat` (greetings, questions about the assistant)
gets a brief reply, `report` (explain, shorten, reformat or revise the last answer) is answered
from that answer and its citations, and `research` runs the harness on the message rewritten to
stand on its own. A `report` answer that lacks what was asked escalates to research. Turns live
in Redis as long as the evidence they cite.
"""

import json
import re
from dataclasses import asdict, dataclass

from ttdr.engine.evidence import TTL_S

MAX_TURNS = 5
"""Turns kept per conversation (AI-Q keeps the last 5 by default)."""
HISTORY_CHARS = 1500
"""Each earlier answer as the router sees it."""
NEEDS_RESEARCH = "NEEDS_RESEARCH"
ROUTES = ("chat", "report", "research")

ROUTE = """You route the next message in a research conversation.

Conversation so far:
{history}

Next message: {message}

Reply with exactly two lines:
ROUTE: chat, report or research
QUESTION: the next message rewritten to stand on its own (spell out what "it", "that" or "the
second point" refer to)

chat: greetings, thanks, or questions about you or this conversation.
report: a question about, or a change to, the last answer (explain, expand from what it says,
shorten, reformat, translate, compare points already in it).
research: a new topic, or anything that needs facts the last answer doesn't contain."""

ABOUT = """You are the research assistant on a Tenstorrent QB2. You research questions over the
web and the user's private document collections and write reports in which every fact cites its
evidence. Reply briefly and plainly to the user's message; don't state facts from memory."""

FOLLOW_UP = """Reply to the user's message from your last answer below, and only from it.
Every factual sentence you keep ends with its evidence IDs in square brackets, copied exactly
as they appear in the answer; add no facts, figures or IDs it doesn't contain. For a change
(shorter, another format, another language), reply with the whole revised answer. If the answer
doesn't contain what the user asks for, reply with exactly {needs}.

Your last answer:
{answer}"""


@dataclass
class Turn:
    question: str
    answer: str


class Conversations:
    """Each conversation's last MAX_TURNS turns, in Redis for as long as their evidence lives."""

    def __init__(self, redis, keep: int = MAX_TURNS):
        self.redis, self.keep = redis, keep

    async def turns(self, conversation: str) -> list[Turn]:
        rows = await self.redis.lrange(f"chat:{conversation}", 0, -1)
        return [Turn(**json.loads(r)) for r in rows]

    async def add(self, conversation: str, turn: Turn) -> None:
        key = f"chat:{conversation}"
        await self.redis.rpush(key, json.dumps(asdict(turn)))
        await self.redis.ltrim(key, -self.keep, -1)
        await self.redis.expire(key, TTL_S)


def parse_route(reply: str, message: str) -> tuple[str, str]:
    """(route, standalone question); anything unclear is research on the message as sent."""
    route = re.search(r"^\s*ROUTE:\s*(\w+)", reply, re.MULTILINE | re.IGNORECASE)
    question = re.search(r"^\s*QUESTION:\s*(.+)", reply, re.MULTILINE | re.IGNORECASE)
    name = route[1].lower() if route else "research"
    return (name if name in ROUTES else "research"), (question[1].strip() if question else message)


def _history(turns: list[Turn]) -> str:
    return (
        "\n\n".join(f"User: {t.question}\nAssistant: {t.answer[:HISTORY_CHARS]}" for t in turns)
        or "(none)"
    )


def _messages(turns: list[Turn]) -> list[dict]:
    return [
        m
        for t in turns
        for m in (
            {"role": "user", "content": t.question},
            {"role": "assistant", "content": t.answer},
        )
    ]


def _text(reply) -> str:
    return reply.content if isinstance(reply.content, str) else ""


@dataclass
class Chat:
    model: object
    """A chat model (thinking off): routes and answers follow-ups."""
    conversations: Conversations

    @classmethod
    def from_recipe(cls, recipe) -> "Chat":
        from redis.asyncio import Redis

        from ttdr.harness import chat_model

        redis = Redis.from_url(recipe.services.redis, decode_responses=True)
        return cls(chat_model(recipe, False, 16384), Conversations(redis))

    def session(self, conversation: str | None, harness) -> "Session":
        return Session(self, conversation, harness)

    async def route(self, turns: list[Turn], message: str) -> tuple[str, str]:
        if not turns:
            return "research", message
        prompt = ROUTE.format(history=_history(turns), message=message)
        reply = await self.model.bind(temperature=0).ainvoke([{"role": "user", "content": prompt}])
        return parse_route(_text(reply), message)

    async def ask(self, system: str, turns: list[Turn], message: str) -> str:
        messages = [{"role": "system", "content": system}, *_messages(turns)]
        reply = await self.model.ainvoke([*messages, {"role": "user", "content": message}])
        return _text(reply).strip()


@dataclass
class Session:
    """One conversation's next turn on one harness; run() has the harness's run signature."""

    chat: Chat
    conversation: str | None
    harness: object

    async def run(self, message: str, events=None, log=lambda _: None) -> str:
        emit = events or (lambda *_: None)
        store = self.chat.conversations
        turns = await store.turns(self.conversation) if self.conversation else []
        route, question = await self.chat.route(turns, message)
        log(f"route: {route}")
        answer = None
        if route == "chat":
            answer = await self.chat.ask(ABOUT, turns, message)
            emit("report", {"report": answer})
        elif route == "report":
            prompt = FOLLOW_UP.format(needs=NEEDS_RESEARCH, answer=turns[-1].answer)
            reply = await self.chat.ask(prompt, turns[:-1], message)
            if NEEDS_RESEARCH not in reply:
                answer = await self.harness.finish(reply)
                emit("report", {"report": answer})
        if answer is None:
            answer = (await self.harness.run(question, log=log, events=events)).report
        if self.conversation:
            await store.add(self.conversation, Turn(message, answer))
        return answer
