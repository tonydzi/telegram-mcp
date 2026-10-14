"""Forward must prove WHAT arrived, not echo len(request).

Incidents it pins down:
- 14.10.2026: basic-group ids are per account. Pack sent by account A
  (ids 256843..), forwarded by account B with the same numbers -> B forwarded
  a foreign 2021 message to a lead, API said "6 messages forwarded".
- 15.09.2026: id list included a MessageActionPinMessage; content message was
  silently not delivered, API said "3 messages forwarded".
"""

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from telegram_mcp.tools import messages


class MessageActionPinMessage:  # name is what the receipt prints
    pass


def _msg(mid, text, year=2026, action=None):
    return SimpleNamespace(
        id=mid,
        message=text,
        date=datetime(year, 10, 14, tzinfo=timezone.utc),
        action=action,
        media=None,
        grouped_id=None,
    )


class _FakeAccount:
    """One Telegram account: its own id -> message map per chat."""

    def __init__(self, chats, deliver=None):
        self.chats = chats
        self.deliver = deliver  # how many forwarded messages "land"
        self.forwarded = []
        self._next = 9000

    async def get_messages(self, entity, ids):
        chat = self.chats.get(entity, {})
        if isinstance(ids, list):
            return [chat.get(i) for i in ids]
        return chat.get(ids)

    async def forward_messages(self, to_entity, ids, from_entity):
        ids = ids if isinstance(ids, list) else [ids]
        self.forwarded.append((to_entity, list(ids), from_entity))
        out = []
        for i in ids[: self.deliver if self.deliver is not None else len(ids)]:
            src = self.chats[from_entity].get(i)
            self._next += 1
            out.append(_msg(self._next, getattr(src, "message", "")))
        return out


ROOM = "room"
DM = "dm"
PACK = {256843 + k: _msg(256843 + k, f"Привет, Катя, шаг {k}") for k in range(6)}
FOREIGN = {256843 + k: _msg(256843 + k, "Ждем контента для IDO", year=2021) for k in range(6)}


@pytest.fixture
def accounts(monkeypatch):
    accs = {
        "t0x0aa": _FakeAccount({ROOM: dict(PACK)}),
        "tonyssd": _FakeAccount({ROOM: dict(FOREIGN)}),
    }
    monkeypatch.setattr(messages, "clients", accs)
    monkeypatch.setattr(messages, "get_client", lambda account=None: accs[account])

    async def _resolve(chat_id, cl):
        return chat_id

    monkeypatch.setattr(messages, "resolve_entity", _resolve)
    return accs


@pytest.mark.asyncio
async def test_wrong_account_ids_refused_before_sending(accounts):
    out = await messages.forward_messages(
        ROOM, list(PACK), DM, account="tonyssd", expect_texts=["Привет, Катя"]
    )
    assert out.startswith("Error: forward refused")
    assert "2021-10-14 · Ждем контента для IDO" in out
    assert accounts["tonyssd"].forwarded == []


@pytest.mark.asyncio
async def test_receipt_shows_what_the_forwarding_account_sent(accounts):
    # Even without expect_texts the receipt must show the 2021 foreign text,
    # so the caller can see the miss in the tool answer itself.
    out = await messages.forward_messages(ROOM, list(PACK)[:1], DM, account="tonyssd")
    assert "2021-10-14 · Ждем контента для IDO" in out


@pytest.mark.asyncio
async def test_service_action_refused(accounts):
    accounts["t0x0aa"].chats[ROOM][256850] = _msg(
        256850, None, action=MessageActionPinMessage()
    )
    out = await messages.forward_messages(
        ROOM, [256843, 256850, 256844], DM, account="t0x0aa"
    )
    assert out.startswith("Error: forward refused")
    assert "service action MessageActionPinMessage" in out
    assert accounts["t0x0aa"].forwarded == []


@pytest.mark.asyncio
async def test_missing_id_refused(accounts):
    out = await messages.forward_messages(ROOM, [1], DM, account="t0x0aa")
    assert out.startswith("Error: forward refused")
    assert "id 1: no such message" in out


@pytest.mark.asyncio
async def test_partial_delivery_is_not_reported_as_full(accounts):
    accounts["t0x0aa"].deliver = 2
    out = await messages.forward_messages(ROOM, list(PACK)[:3], DM, account="t0x0aa")
    assert out.startswith("Warning: 2 of 3 messages forwarded")
    assert "MISMATCH" in out


@pytest.mark.asyncio
async def test_happy_path_lists_src_dst_and_text(accounts):
    out = await messages.forward_messages(
        ROOM, list(PACK), DM, account="t0x0aa", expect_texts=["Привет, Катя, шаг 5"]
    )
    assert out.startswith("6 of 6 messages forwarded")
    assert "destination check: OK" in out
    assert "src 256843 -> dst 9001 · 2026-10-14 · Привет, Катя, шаг 0" in out


@pytest.mark.asyncio
async def test_single_forward_message_uses_same_guard(accounts):
    out = await messages.forward_message(
        ROOM, 256843, DM, account="tonyssd", expect_texts=["Привет, Катя"]
    )
    assert out.startswith("Error: forward refused")
    assert accounts["tonyssd"].forwarded == []
