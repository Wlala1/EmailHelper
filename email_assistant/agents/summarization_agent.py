"""
Summarization Agent — 邮件摘要
使用 GPT-4o 生成中文摘要和关键行动点
"""

import json
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from openai import OpenAI

from config import OPENAI_API_KEY, OPENAI_MODEL
from schemas import EmailMessage, UserPreferences
from agents.monitoring_agent import monitor

_client = OpenAI(api_key=OPENAI_API_KEY)

SYSTEM_PROMPT = """你是一个专业的邮件摘要助手。请分析邮件内容并返回 JSON 格式的结果：
{
  "summary": "<简洁摘要，{length}>",
  "action_items": ["<行动点1>", "<行动点2>", ...]
}

摘要要求：准确、简洁，突出重点信息（发件人意图、关键数字/日期、需要做的事）。
行动点：列出所有需要采取的具体行动，没有则返回空数组。
只返回 JSON，不要有其他文字。"""

LENGTH_MAP = {
    "short": "1-2句话",
    "medium": "3-4句话",
    "long": "5-6句话",
}


@monitor("SummarizationAgent")
def summarize_email(msg: EmailMessage, prefs: UserPreferences = None) -> EmailMessage:
    """
    Generate a summary and action items for an email.
    Respects user preference for summary length.
    """
    length = LENGTH_MAP.get(
        prefs.preferred_summary_length if prefs else "short",
        "1-2句话"
    )
    system = SYSTEM_PROMPT.format(length=length)
    text = f"主题: {msg.subject}\n发件人: {msg.from_addr}\n\n正文:\n{msg.body[:3000]}"

    response = _client.chat.completions.create(
        model=OPENAI_MODEL,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": text},
        ],
        temperature=0.3,
        response_format={"type": "json_object"},
    )

    data = json.loads(response.choices[0].message.content)
    msg.summary = data.get("summary", "")
    msg.action_items = data.get("action_items", [])
    return msg
