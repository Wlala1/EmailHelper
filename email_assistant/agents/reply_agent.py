"""
Reply Agent — 回复生成
基于用户偏好（风格/语言）用 GPT-4o 生成回复草稿
用户确认后通过 O365 发送
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from openai import OpenAI

from config import OPENAI_API_KEY, OPENAI_MODEL
from schemas import EmailMessage, UserPreferences
from agents.monitoring_agent import monitor

_client = OpenAI(api_key=OPENAI_API_KEY)

STYLE_DESCRIPTIONS = {
    "formal": "正式、专业、礼貌",
    "warm": "温暖、友好、亲切",
    "concise": "简洁、直接、要点明确",
}

SYSTEM_PROMPT = """你是一个邮件回复助手。请根据以下邮件摘要和用户偏好，生成一封回复草稿。

回复要求：
- 语气：{style_desc}
- 语言：{language}
- 长度：适中，覆盖所有需要回复的要点
- 不要添加"[您的姓名]"等占位符，直接生成可发送的内容

只返回回复正文，不要有解释性文字。"""


@monitor("ReplyAgent")
def generate_reply(msg: EmailMessage, prefs: UserPreferences = None) -> EmailMessage:
    """
    Generate a reply draft for an email based on user preferences.
    Updates msg.suggested_reply and returns the msg.
    """
    if prefs is None:
        prefs = UserPreferences()

    style = prefs.preferred_style
    style_desc = STYLE_DESCRIPTIONS.get(style, STYLE_DESCRIPTIONS["warm"])
    language = "中文" if prefs.reply_language == "zh" else "English"

    system = SYSTEM_PROMPT.format(style_desc=style_desc, language=language)

    # Build context: use summary if available, otherwise body
    context = msg.summary or msg.body[:2000]
    action_items_text = ""
    if msg.action_items:
        action_items_text = "\n\n需要处理的行动点：\n" + "\n".join(f"- {a}" for a in msg.action_items)

    user_content = (
        f"原邮件主题：{msg.subject}\n"
        f"发件人：{msg.from_addr}\n"
        f"邮件类型：{msg.classification or '未分类'}\n\n"
        f"邮件摘要：{context}"
        f"{action_items_text}"
    )

    response = _client.chat.completions.create(
        model=OPENAI_MODEL,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user_content},
        ],
        temperature=0.7,
    )

    msg.suggested_reply = response.choices[0].message.content.strip()
    return msg


def send_reply(msg: EmailMessage, reply_text: str) -> bool:
    """
    Send a reply via Outlook O365.
    Returns True on success.
    """
    try:
        from O365 import Account, FileSystemTokenBackend
        from config import AZURE_CLIENT_ID, AZURE_CLIENT_SECRET, AZURE_TENANT_ID, TOKEN_FILE

        credentials = (AZURE_CLIENT_ID, AZURE_CLIENT_SECRET)
        token_backend = FileSystemTokenBackend(
            token_path=str(TOKEN_FILE.parent),
            token_filename=TOKEN_FILE.name,
        )
        account = Account(
            credentials,
            auth_flow_type="authorization",
            tenant_id=AZURE_TENANT_ID,
            token_backend=token_backend,
        )

        mailbox = account.mailbox()
        inbox = mailbox.inbox_folder()
        query = inbox.new_query().on_attribute("id").equals(msg.id)
        messages = list(inbox.get_messages(limit=1, query=query))
        if not messages:
            return False

        original = messages[0]
        reply = original.reply()
        reply.body = reply_text
        reply.send()
        msg.reply_sent = True
        return True
    except Exception as e:
        from agents.monitoring_agent import logger
        logger.error(f"send_reply failed for {msg.id}: {e}")
        return False
