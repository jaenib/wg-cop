"""Minimal stand-ins for python-telegram-bot objects.

They record every outgoing call so tests can assert on what the bot said and
which keyboard it attached, without a network or a real Application.
"""
import asyncio
import itertools
from types import SimpleNamespace

from telegram.error import BadRequest

_MESSAGE_IDS = itertools.count(1000)


def run(coro):
    """Run one handler coroutine to completion on a fresh event loop."""
    return asyncio.run(coro)


class FakeBot:
    def __init__(self, username="wgcop_test_bot", chat_members=None, chats=None):
        self.username = username
        self.sent = []          # (chat_id, text, kwargs)
        self.documents = []     # (chat_id, filename, caption)
        self.chat_members = chat_members or {}  # (chat_id, uid) -> status
        self.chats = chats or {}                # chat_id -> title
        self.fail_send = None   # exception to raise from send_message

    async def send_message(self, chat_id, text, **kwargs):
        if self.fail_send is not None:
            raise self.fail_send
        self.sent.append((chat_id, text, kwargs))
        return FakeMessage(text=text, chat=FakeChat(chat_id))

    async def send_document(self, chat_id, document, filename=None, caption=None, **kwargs):
        payload = document.read() if hasattr(document, "read") else document
        self.documents.append((chat_id, filename, caption, payload))

    async def get_chat(self, chat_id):
        return SimpleNamespace(id=chat_id, title=self.chats.get(chat_id, str(chat_id)))

    async def get_chat_member(self, chat_id, user_id):
        status = self.chat_members.get((chat_id, user_id), "left")
        return SimpleNamespace(status=status, user=SimpleNamespace(id=user_id))

    async def get_me(self):
        return SimpleNamespace(username=self.username, id=999)


class FakeUser:
    def __init__(self, id, first_name="Test", last_name=None, username=None, is_bot=False):
        self.id = id
        self.first_name = first_name
        self.last_name = last_name
        self.username = username
        self.is_bot = is_bot

    @property
    def full_name(self):
        return f"{self.first_name} {self.last_name}" if self.last_name else self.first_name


class FakeChat:
    def __init__(self, id, type="private", title=None):
        self.id = id
        self.type = type
        self.title = title


class FakeMessage:
    def __init__(self, text=None, chat=None, from_user=None, photo=None, document=None):
        self.text = text
        self.chat = chat or FakeChat(0)
        self.from_user = from_user
        self.photo = photo or []
        self.document = document
        self.replies = []       # (text, reply_markup, parse_mode)
        self.sent_messages = [] # the FakeMessage objects returned by reply_*
        self.deleted = False
        self.sender_chat = None
        self.message_id = next(_MESSAGE_IDS)

    async def reply_text(self, text, reply_markup=None, parse_mode=None, **kwargs):
        self.replies.append((text, reply_markup, parse_mode))
        sent = FakeMessage(text=text, chat=self.chat)
        self.sent_messages.append(sent)
        return sent

    async def reply_html(self, text, reply_markup=None, **kwargs):
        self.replies.append((text, reply_markup, "HTML"))
        sent = FakeMessage(text=text, chat=self.chat)
        self.sent_messages.append(sent)
        return sent

    async def delete(self):
        self.deleted = True

    @property
    def last_reply(self):
        return self.replies[-1][0] if self.replies else None

    @property
    def last_markup(self):
        return self.replies[-1][1] if self.replies else None


class FakeCallbackQuery:
    def __init__(self, data, message, from_user):
        self.data = data
        self.message = message
        self.from_user = from_user
        self.answers = []       # (text, show_alert)
        self.edits = []         # (text, reply_markup)
        self.markup_edits = []

    async def answer(self, text=None, show_alert=False, **kwargs):
        if self.answers:  # Telegram accepts exactly one answer per callback query
            raise BadRequest("Query is too old and response timeout expired or query id is invalid")
        self.answers.append((text, show_alert))

    async def edit_message_text(self, text, reply_markup=None, **kwargs):
        self.edits.append((text, reply_markup))

    async def edit_message_reply_markup(self, reply_markup=None, **kwargs):
        self.markup_edits.append(reply_markup)


class FakeUpdate:
    def __init__(self, message=None, callback_query=None, user=None, chat=None):
        self.message = message
        self.callback_query = callback_query
        self.effective_user = user
        if chat is None:
            chat = message.chat if message else (callback_query.message.chat if callback_query else None)
        self.effective_chat = chat
        self.effective_message = message or (callback_query.message if callback_query else None)


class FakeJobQueue:
    def __init__(self):
        self.jobs = []

    def get_jobs_by_name(self, name):
        return [j for j in self.jobs if j.name == name]

    def _add(self, callback, name, **kw):
        job = SimpleNamespace(callback=callback, name=name, removed=False, kw=kw)
        job.schedule_removal = lambda j=job: setattr(j, "removed", True)
        self.jobs.append(job)
        return job

    def run_repeating(self, callback, interval, first=None, name=None, **kw):
        return self._add(callback, name, interval=interval, first=first, **kw)

    def run_once(self, callback, when, name=None, **kw):
        return self._add(callback, name, when=when, **kw)

    def run_daily(self, callback, time, name=None, **kw):
        return self._add(callback, name, time=time, **kw)


class FakeContext:
    def __init__(self, bot=None, user_data=None, chat_data=None, args=None, job_queue=None):
        self.bot = bot or FakeBot()
        self.user_data = user_data if user_data is not None else {}
        self.chat_data = chat_data if chat_data is not None else {}
        self.args = args or []
        self.job_queue = job_queue or FakeJobQueue()
        self.application = SimpleNamespace(job_queue=self.job_queue, bot=self.bot)
        self.job = None


def text_update(text, user, chat=None):
    """Build a text-message update from `user` in `chat` (private DM by default)."""
    chat = chat or FakeChat(user.id, "private")
    msg = FakeMessage(text=text, chat=chat, from_user=user)
    return FakeUpdate(message=msg, user=user, chat=chat)


def callback_update(data, user, chat=None):
    chat = chat or FakeChat(user.id, "private")
    msg = FakeMessage(text="", chat=chat, from_user=user)
    query = FakeCallbackQuery(data, msg, user)
    return FakeUpdate(callback_query=query, user=user, chat=chat)


def keyboard_labels(markup):
    """Flatten a Reply/Inline keyboard markup into a list of button labels."""
    if markup is None or not hasattr(markup, "keyboard") and not hasattr(markup, "inline_keyboard"):
        return []
    rows = getattr(markup, "inline_keyboard", None) or getattr(markup, "keyboard", [])
    return [getattr(b, "text", str(b)) for row in rows for b in row]
