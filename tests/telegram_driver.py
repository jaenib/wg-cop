"""Offline Telegram transport for testing real PTB update dispatch.

Unlike direct handler fakes, this exercises handler ordering and conversation
state transitions through Application.process_update, with zero network IO.
"""
import json
from datetime import datetime, timezone

from telegram import Update
from telegram.request import BaseRequest


class TelegramDriver(BaseRequest):
    def __init__(self):
        super().__init__()
        self.calls = []
        self.answered = set()
        self.members = {}
        self.next_message = 1000
        self.next_update = 1

    @property
    def read_timeout(self):
        return 5

    async def initialize(self):
        pass

    async def shutdown(self):
        pass

    async def do_request(self, url, method, request_data=None, **kwargs):
        endpoint = url.rsplit("/", 1)[-1]
        params = request_data.parameters if request_data else {}
        self.calls.append((endpoint, params))
        if endpoint == "getMe":
            result = {"id": 999, "is_bot": True, "first_name": "Test Bot", "username": "wg_test_bot"}
        elif endpoint == "getChatMember":
            uid = int(params["user_id"])
            status = self.members.get((int(params["chat_id"]), uid), "left")
            result = {"status": status, "user": self.user(uid)}
            if status == "administrator":
                result.update(can_be_edited=False, is_anonymous=False, can_manage_chat=True,
                              can_delete_messages=True, can_manage_video_chats=False,
                              can_restrict_members=True, can_promote_members=True,
                              can_change_info=True, can_invite_users=True,
                              can_post_stories=False, can_edit_stories=False, can_delete_stories=False)
            elif status == "creator":
                result["is_anonymous"] = False
        elif endpoint in ("sendMessage", "editMessageText", "editMessageReplyMarkup"):
            self.next_message += 1
            cid = int(params["chat_id"])
            result = {"message_id": params.get("message_id", self.next_message),
                      "date": int(datetime.now(timezone.utc).timestamp()),
                      "chat": self.chat(cid), "from": {"id": 999, "is_bot": True, "first_name": "Test Bot"},
                      "text": params.get("text", "keyboard")}
            markup = params.get("reply_markup")
            if markup:
                markup = json.loads(markup) if isinstance(markup, str) else markup
                if "inline_keyboard" in markup:
                    result["reply_markup"] = markup
        elif endpoint == "answerCallbackQuery":
            query_id = str(params.get("callback_query_id"))
            if query_id in self.answered:  # Telegram accepts exactly one answer per query
                error = {"ok": False, "error_code": 400,
                         "description": "Bad Request: query is too old and response timeout expired or query id is invalid"}
                return 400, json.dumps(error).encode()
            self.answered.add(query_id)
            result = True
        elif endpoint == "deleteMessage":
            result = True
        elif endpoint == "getChat":
            result = self.chat(int(params["chat_id"]))
        else:
            raise AssertionError(f"Unexpected Telegram method: {endpoint}")
        return 200, json.dumps({"ok": True, "result": result}).encode()

    @staticmethod
    def user(uid):
        return {"id": uid, "is_bot": False, "first_name": f"User {uid}"}

    @staticmethod
    def chat(cid):
        return {"id": cid, "type": "supergroup" if cid < 0 else "private", "title": f"Home {cid}"}

    def message_update(self, bot, uid, cid, text):
        self.next_update += 1
        message = {"message_id": self.next_update, "date": 1750000000,
                   "chat": self.chat(cid), "from": self.user(uid), "text": text}
        if text.startswith("/"):
            message["entities"] = [{"type": "bot_command", "offset": 0, "length": len(text.split()[0])}]
        return Update.de_json({"update_id": self.next_update, "message": message}, bot)

    def callback_update(self, bot, uid, cid, message_id, data):
        self.next_update += 1
        query = {"id": str(self.next_update), "from": self.user(uid), "chat_instance": str(cid), "data": data,
                 "message": {"message_id": message_id, "date": 1750000000, "chat": self.chat(cid),
                             "from": {"id": 999, "is_bot": True, "first_name": "Test Bot"}, "text": "keyboard"}}
        return Update.de_json({"update_id": self.next_update, "callback_query": query}, bot)

    def messages(self, cid):
        return [params.get("text", "") for method, params in self.calls
                if method == "sendMessage" and int(params["chat_id"]) == cid]
