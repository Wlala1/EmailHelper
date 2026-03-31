import os
import sys
import json
import psycopg2
from datetime import datetime
from typing import Literal, List, Optional
from psycopg2.extras import RealDictCursor

from langgraph.graph import StateGraph, START, END
from langgraph.types import interrupt, Command
from langchain_openai import ChatOpenAI
from langchain.messages import HumanMessage, BaseMessage

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from schemas import EmailMessage, EmailClassification
from config import OPENAI_API_KEY

llm = ChatOpenAI(model="gpt-4o")

# --- 数据库工具函数 ---
def get_db_connection(dbname="fetched_data"):
    return psycopg2.connect(
        dbname=dbname,
        user="ouma_user",
        password="ouma_password", 
        host="localhost", 
        port="5432"
    )

# --- 节点函数 ---

def read_email(state: EmailMessage) -> dict:
    """从数据库捞取一封未处理邮件"""
    conn = None
    try:
        conn = get_db_connection()
        cur = conn.cursor(cursor_factory=RealDictCursor)
        
        query = "SELECT * FROM emails WHERE classification IS NULL ORDER BY received_at ASC LIMIT 1;"
        cur.execute(query)
        email_data = cur.fetchone()
        
        if not email_data:
            # 返回一个标记位，让 Graph 知道该结束了
            return {"error": "NO_NEW_EMAILS"}

        # 映射数据库字段到 EmailMessage TypedDict
        return {
            "id": email_data['id'],
            "from_addr": email_data['from_addr'],
            "to": email_data['to'] or [],
            "subject": email_data['subject'] or "",
            "body": email_data['body'] or "",
            "received_at": str(email_data['received_at']),
            "attachments": email_data['attachments'] or [],
            "labels": email_data['labels'] or [],
            "classification": None
        }
    finally:
        if conn: conn.close()

def classify_intent(state: EmailMessage) -> Command[Literal["draft_response", "update_db"]]:
    """分类邮件，决定是回复还是直接存入 DB"""
    
    # 强制 LLM 遵循 EmailClassification 的 TypedDict 结构 (Pydantic 转换)
    structured_llm = llm.with_structured_output(EmailClassification)

    prompt = f"""
    分析以下邮件并分类：
    主题: {state['subject']}
    正文: {state['body'][:2000]}
    来自: {state['from_addr']}
    """

    classification = structured_llm.invoke(prompt)

    # 路由逻辑：需要回复则去草拟，不需要则直接去更新 DB（标记为已读）
    goto = "draft_response" if classification.get("need_reply") else "update_db"

    return Command(
        update={"classification": classification},
        goto=goto
    )

def draft_response(state: EmailMessage) -> Command[Literal["human_review"]]:
    """根据分类生成回复草稿"""
    cls = state['classification']
    
    prompt = f"""
    请为以下邮件撰写回复：
    原文主题: {state['subject']}
    原文内容: {state['body']}
    
    分类: {cls['topic']}
    摘要: {cls['summary']}
    紧急程度: {cls['urgency']}
    
    要求：专业、简洁、针对性解决问题。
    """

    response = llm.invoke(prompt)

    return Command(
        update={"suggested_reply": response.content},
        goto="human_review"
    )

def human_review(state: EmailMessage) -> Command[Literal["send_reply", "update_db"]]:
    """人工审核中断点"""
    
    # interrupt 会暂停执行，并向 UI/客户端发送这些信息
    human_input = interrupt({
        "question": "请审核 AI 生成的回复建议",
        "email_id": state['id'],
        "suggested_reply": state['suggested_reply']
    })

    # 假设 human_input 包含 {"approved": bool, "edited_reply": str}
    if human_input.get("approved"):
        return Command(
            update={"suggested_reply": human_input.get("edited_reply", state['suggested_reply'])},
            goto="send_reply"
        )
    
    # 如果不通过，直接结束处理（标记为完成）
    return Command(goto="update_db")

def send_reply(state: EmailMessage) -> dict:
    """模拟发送邮件"""
    print(f"🚀 发送邮件给 {state['from_addr']}...")
    return {"reply_sent": True}

def update_db(state: EmailMessage) -> dict:
    """终点节点：将所有 State 回写数据库"""
    conn = None
    try:
        conn = get_db_connection()
        cur = conn.cursor()

        # 将 classification 字典转为 JSON 字符串存储
        cls_json = json.dumps(state['classification']) if state['classification'] else None
        
        query = """
            UPDATE emails 
            SET classification = %s, suggested_reply = %s, 
                reply_sent = %s, processed_at = %s 
            WHERE id = %s;
        """
        cur.execute(query, (
            cls_json, 
            state.get('suggested_reply'), 
            state.get('reply_sent', False),
            datetime.now(), 
            state['id']
        ))
        conn.commit()
        print(f"✅ 邮件 {state['id']} 已保存并标记为已处理")
    finally:
        if conn: conn.close()
    
    return {"processed_at": str(datetime.now())}