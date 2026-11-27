"""The web UI's chat history on the server, per owner, so chats outlive the browser's storage
quota and follow it across ports.

The UI's proxy names the owner in OWNER: an anonymous ID it keeps in a cookie, one per browser.
Each owner's conversations are one Redis hash (conversation ID -> the UI's conversation JSON),
beside a small state document (current user and conversation, a pending interaction).
"""

import json
import re

OWNER = "X-TTDR-Owner"
VALID_OWNER = re.compile(r"[A-Za-z0-9_-]{8,64}")


class ChatHistory:
    def __init__(self, redis):
        self.redis = redis

    @staticmethod
    def _key(owner: str) -> str:
        if not VALID_OWNER.fullmatch(owner):
            raise ValueError(f"not an owner ID: {owner!r}")
        return f"history:{owner}"

    async def load(self, owner: str) -> dict:
        key = self._key(owner)
        state = await self.redis.get(f"{key}:state")
        conversations = await self.redis.hvals(key)
        return {
            "state": json.loads(state) if state else None,
            "conversations": [json.loads(c) for c in conversations],
        }

    async def save(self, owner: str, conversation: dict) -> None:
        await self.redis.hset(self._key(owner), conversation["id"], json.dumps(conversation))

    async def save_state(self, owner: str, state: dict) -> None:
        await self.redis.set(f"{self._key(owner)}:state", json.dumps(state))

    async def delete(self, owner: str, conversation_id: str) -> None:
        await self.redis.hdel(self._key(owner), conversation_id)
