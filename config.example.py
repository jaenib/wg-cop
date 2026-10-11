"""Example configuration for the Telegram bot.

Copy this file to ``.secrets/config.py`` (or a root ``config.py``, or any path
named by ``WG_COP_CONFIG_FILE``) and fill in your real values. Both locations
are excluded from version control.

Only ``TOKEN`` is required. Homes, their members, administrators and other
roles are created and managed from inside Telegram (``/newhome``, ``/join``,
Settings -> Admin Panel), not here.
"""

# Telegram bot token provided by BotFather (required)
TOKEN = "your-bot-token"

# OpenAI API key for receipt scanning (optional; manual entry works without it)
OPENAI_API_KEY = ""

# --- Legacy single-household values -----------------------------------------
# Only consulted once, when an old ``wg_data_alpha.json`` (without a
# schema_version) is migrated on first start. They seed the first home:
# the group, its first administrator/handler, its chronicler, and the links
# between existing member profiles and Telegram accounts. Leave them empty
# for a fresh installation.
GROUP_CHAT_ID = ""      # numeric id of the household's Telegram group
BOT_HANDLER_ID = ""     # Telegram user id that becomes the first administrator
CHRONICLER_ID = ""      # Telegram user id that receives archive backups
# Telegram user ids of the historical member profiles (optional)
NI_ID = ""
GI_ID = ""
GY_ID = ""
TO_ID = ""
JA_ID = ""
# Any other legacy profile: {"Member name as stored": telegram_user_id}
LEGACY_MEMBER_IDS = {}
