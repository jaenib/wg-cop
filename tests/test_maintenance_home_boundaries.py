"""Fail-closed checks independent of home-resolution implementation tests."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from wgcop.homes import is_group_member, owns_keyboard


@pytest.mark.parametrize("is_member", [False, None])
def test_restricted_nonmember_cannot_join_home(is_member):
    bot = SimpleNamespace(get_chat_member=AsyncMock(return_value=SimpleNamespace(
        status="restricted", is_member=is_member)))
    assert not asyncio.run(is_group_member(bot, -100, 10))


def test_restricted_current_member_can_join_home():
    bot = SimpleNamespace(get_chat_member=AsyncMock(return_value=SimpleNamespace(
        status="restricted", is_member=True)))
    assert asyncio.run(is_group_member(bot, -100, 10))


def test_unbound_keyboard_cannot_operate_a_draft():
    query = SimpleNamespace(message=SimpleNamespace(message_id=10))
    assert not owns_keyboard(query, {})
    assert not owns_keyboard(SimpleNamespace(message=None), {"kb_msg": 10})
    assert not owns_keyboard(query, {"kb_msg": 11})
    assert owns_keyboard(query, {"kb_msg": 10})
