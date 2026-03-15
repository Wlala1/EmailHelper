"""
Classification Agent — 邮件分类
使用 GPT-4o 将邮件分类为 fundraiser | customer | internal | spam
"""

import json
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from openai import OpenAI

from config import OPENAI_API_KEY, OPENAI_MODEL
from schemas import EmailMessage
from agents.monitoring_agent import monitor

_client = OpenAI(api_key=OPENAI_API_KEY)

CATEGORIES = ["fundraiser", "customer", "internal", "spam"]

SYSTEM_PROMPT = """你是一个邮件分类助手。请将给定邮件分类为以下类别之一：
- fundraiser：融资、投资、募资相关邮件
- customer：客户咨询、支持、反馈邮件
- internal：内部团队、同事之间的邮件
- spam：垃圾邮件、广告、无关邮件

请以 JSON 格式返回结果，格式如下：
{"classification": "<类别>", "confidence": <0.0到1.0的置信度>, "reason": "<简短理由>"}

只返回 JSON，不要有其他文字。"""


@monitor("ClassificationAgent")
def classify_email(msg: EmailMessage) -> EmailMessage:
    """
    Classify an email using GPT-4o.
    Updates msg.classification and msg.confidence in-place and returns the msg.
    """
    text = f"主题: {msg.subject}\n\n发件人: {msg.from_addr}\n\n正文:\n{msg.body[:2000]}"

    response = _client.chat.completions.create(
        model=OPENAI_MODEL,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": text},
        ],
        temperature=0.1,
        response_format={"type": "json_object"},
    )

    raw = response.choices[0].message.content
    data = json.loads(raw)

    classification = data.get("classification", "spam").lower()
    if classification not in CATEGORIES:
        classification = "spam"

    msg.classification = classification
    msg.confidence = float(data.get("confidence", 0.5))
    return msg


def classify_email_batch(msgs: list[EmailMessage]) -> list[EmailMessage]:
    """Classify a list of emails (sequentially)."""
    return [classify_email(msg) for msg in msgs]
