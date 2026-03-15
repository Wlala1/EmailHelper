"""
Orchestrator — ReAct 多步推理主循环
Thought → Action → Observation 循环处理每封邮件
自动读取并更新用户偏好
"""

import json
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from datetime import datetime
from typing import Optional

from config import PREFERENCES_FILE, MAX_REACT_ITERATIONS, OPENAI_API_KEY, OPENAI_MODEL
from schemas import EmailMessage, UserPreferences
from agents.monitoring_agent import logger

# Lazy imports to avoid circular dependencies
def _get_agents():
    from agents.classification_agent import classify_email
    from agents.summarization_agent import summarize_email
    from agents.reply_agent import generate_reply
    from agents.task_agent import create_task
    return classify_email, summarize_email, generate_reply, create_task


# ── Preference helpers ────────────────────────────────────────────────────────

def load_preferences() -> UserPreferences:
    if PREFERENCES_FILE.exists():
        with open(PREFERENCES_FILE, "r", encoding="utf-8") as f:
            return UserPreferences(**json.load(f))
    return UserPreferences()


def save_preferences(prefs: UserPreferences) -> None:
    prefs.last_updated = datetime.now().isoformat()
    PREFERENCES_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(PREFERENCES_FILE, "w", encoding="utf-8") as f:
        json.dump(prefs.model_dump(), f, ensure_ascii=False, indent=2)


def update_preferences(updates: dict) -> UserPreferences:
    """Apply a dict of updates to user preferences and persist."""
    prefs = load_preferences()
    for key, value in updates.items():
        if hasattr(prefs, key):
            setattr(prefs, key, value)
    save_preferences(prefs)
    return prefs


# ── ReAct step definitions ────────────────────────────────────────────────────

class ReActStep:
    def __init__(self, thought: str, action: str, observation: str = ""):
        self.thought = thought
        self.action = action
        self.observation = observation

    def __repr__(self):
        return f"[Thought] {self.thought}\n[Action] {self.action}\n[Observation] {self.observation}"


def _decide_next_action(msg: EmailMessage) -> Optional[str]:
    """
    Determine which action to take next based on the current state of the email.
    Returns action name or None if done.
    """
    if msg.classification is None:
        return "classify"
    if msg.summary is None:
        return "summarize"
    if msg.suggested_reply is None and msg.classification in ["fundraiser", "customer"]:
        return "generate_reply"
    if msg.task_status is None and msg.classification in ["fundraiser", "customer"]:
        return "create_task"
    return None  # All steps complete


# ── Main ReAct loop ───────────────────────────────────────────────────────────

def react_process_email(msg: EmailMessage, prefs: Optional[UserPreferences] = None) -> tuple[EmailMessage, list[ReActStep]]:
    """
    Run the ReAct loop on a single email.
    Returns (processed EmailMessage, list of ReActStep trace).
    """
    if prefs is None:
        prefs = load_preferences()

    classify_email, summarize_email, generate_reply, create_task = _get_agents()

    ACTION_MAP = {
        "classify": lambda m: classify_email(m),
        "summarize": lambda m: summarize_email(m, prefs),
        "generate_reply": lambda m: generate_reply(m, prefs),
        "create_task": lambda m: create_task(m, prefs),
    }

    ACTION_THOUGHTS = {
        "classify": "邮件尚未分类，需要先确定邮件类型以便后续处理。",
        "summarize": "邮件已分类，现在生成摘要以提取关键信息和行动点。",
        "generate_reply": f"邮件类型为 {{cls}}，需要生成回复草稿（风格：{prefs.preferred_style}）。",
        "create_task": "邮件需要跟进，创建任务记录到 Google Sheets。",
    }

    steps: list[ReActStep] = []
    iteration = 0

    logger.info(f"[ReAct] Starting processing for email {msg.id} | subject: {msg.subject}")

    while iteration < MAX_REACT_ITERATIONS:
        iteration += 1
        action = _decide_next_action(msg)

        if action is None:
            logger.info(f"[ReAct] email {msg.id} fully processed in {iteration - 1} steps")
            break

        thought_template = ACTION_THOUGHTS.get(action, f"执行 {action}。")
        thought = thought_template.format(cls=msg.classification or "unknown")

        step = ReActStep(thought=thought, action=action)
        logger.info(f"[ReAct] iter={iteration} | {step.thought} → action={action}")

        try:
            msg = ACTION_MAP[action](msg)
            step.observation = f"成功：{action} 完成"
            if action == "classify":
                step.observation = f"分类结果：{msg.classification}（置信度：{msg.confidence:.2f}）"
            elif action == "summarize":
                step.observation = f"摘要已生成，行动点数量：{len(msg.action_items)}"
            elif action == "generate_reply":
                step.observation = f"回复草稿已生成（{len(msg.suggested_reply or '')} 字符）"
            elif action == "create_task":
                step.observation = f"任务已写入 Google Sheets，状态：{msg.task_status}，截止：{msg.task_due_date}"
        except Exception as e:
            step.observation = f"错误：{str(e)}"
            msg.error = str(e)
            logger.error(f"[ReAct] action={action} failed for {msg.id}: {e}")
            break

        steps.append(step)

    msg.processed_at = datetime.now().isoformat()
    return msg, steps


def process_email_with_feedback(
    msg: EmailMessage,
    user_feedback: Optional[dict] = None
) -> tuple[EmailMessage, list[ReActStep]]:
    """
    Process email and optionally update preferences from user feedback.
    user_feedback example: {"preferred_style": "concise", "preferred_summary_length": "medium"}
    """
    if user_feedback:
        prefs = update_preferences(user_feedback)
    else:
        prefs = load_preferences()

    return react_process_email(msg, prefs)
