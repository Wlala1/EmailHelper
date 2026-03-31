import os
import sys
import uuid
import json
import psycopg2
from typing import List
from psycopg2.extras import execute_values
from O365 import Account, FileSystemTokenBackend

# 确保路径正确以导入 schemas
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from schemas import EmailMessage
from config import AZURE_CLIENT_ID, TOKEN_FILE, EMAIL_FETCH_LIMIT, ATTACHMENTS_DIR

def _get_account() -> Account:
    """认证并返回 O365 账户对象"""
    credentials = (AZURE_CLIENT_ID, None)
    token_backend = FileSystemTokenBackend(
        token_path=str(TOKEN_FILE.parent),
        token_filename=TOKEN_FILE.name,
    )
    account = Account(credentials, token_backend=token_backend)
    if not account.is_authenticated:
        account.authenticate(scopes=['https://graph.microsoft.com/Mail.Read', 'offline_access'])
    return account

def _parse_message(msg) -> EmailMessage:
    """将 O365 消息对象转换为符合 Schema 的字典"""
    attachment_names = []
    if msg.has_attachments:
        try:
            msg.attachments.download_attachments()
            for att in msg.attachments:
                att.save(location=str(ATTACHMENTS_DIR))
                attachment_names.append(att.name)
        except Exception as e:
            print(f"附件下载失败: {e}")

    # 严格匹配 EmailMessage TypedDict 结构
    return {
        "id": str(msg.object_id) if msg.object_id else str(uuid.uuid4()),
        "from_addr": str(msg.sender.address) if msg.sender else "",
        "to": [r.address for r in msg.to._recipients] if msg.to else [],
        "subject": msg.subject or "",
        "body": msg.body or "",
        "received_at": msg.received.strftime("%Y-%m-%dT%H:%M:%S") if msg.received else None,
        "attachments": attachment_names,
        "labels": [],
        "classification": None,  # 初始为 None
        "suggested_reply": None,
        "reply_sent": False,
        "task_status": "Pending",
        "task_due_date": None,
        "task_row": None,
        "processed_at": None,
        "error": None
    }

def init_db():
    """初始化数据库表，确保字段与 EmailMessage 一致"""
    conn = None
    try:
        conn = psycopg2.connect(
            dbname="fetched_data", user="ouma_user", 
            password="ouma_password", host="localhost", port="5432"
        )
        cur = conn.cursor()
        # 使用 JSONB 存储 classification 字典以获得更好的灵活性
        cur.execute("""
            CREATE TABLE IF NOT EXISTS emails (
                id TEXT PRIMARY KEY,
                from_addr TEXT,
                to_addrs TEXT[],
                subject TEXT,
                body TEXT,
                received_at TIMESTAMP,
                attachments TEXT[],
                labels TEXT[],
                classification JSONB, 
                suggested_reply TEXT,
                reply_sent BOOLEAN DEFAULT FALSE,
                task_status TEXT DEFAULT 'Pending',
                task_due_date TIMESTAMP,
                task_row INTEGER,
                processed_at TIMESTAMP,
                error TEXT
            );
        """)
        conn.commit()
    finally:
        if conn: conn.close()

def save_emails_to_postgres(emails: List[EmailMessage]):
    """批量存入或更新邮件数据"""
    conn = None
    try:
        conn = psycopg2.connect(
            dbname="fetched_data", user="ouma_user", 
            password="ouma_password", host="localhost", port="5432"
        )
        cur = conn.cursor()

        query = """
        INSERT INTO emails (
            id, from_addr, to_addrs, subject, body, received_at, attachments, labels,
            classification, suggested_reply, reply_sent, task_status, 
            task_due_date, task_row, error
        ) VALUES %s
        ON CONFLICT (id) DO UPDATE SET
            body = EXCLUDED.body,
            processed_at = CURRENT_TIMESTAMP;
        """

        # 转换数据，注意 classification 需要转为 JSON 字符串
        data = [(
            e['id'], e['from_addr'], e['to'], e['subject'], e['body'], e['received_at'],
            e['attachments'], e['labels'], 
            json.dumps(e['classification']) if e['classification'] else None,
            e['suggested_reply'], e['reply_sent'], e['task_status'],
            e['task_due_date'], e['task_row'], e['error']
        ) for e in emails]

        execute_values(cur, query, data)
        conn.commit()
        print(f"--- 数据库同步完成：处理了 {len(emails)} 封邮件 ---")
    except Exception as e:
        print(f"数据库写入失败: {e}")
    finally:
        if conn: conn.close()

def fetch_emails(limit: int = EMAIL_FETCH_LIMIT, unread_only: bool = True) -> List[EmailMessage]:
    """抓取邮件并实时分批存入数据库"""
    ATTACHMENTS_DIR.mkdir(parents=True, exist_ok=True)
    account = _get_account()
    inbox = account.mailbox().inbox_folder()
        
    # 修改 fetch_emails 内部：
    if unread_only:
        # 直接在获取时过滤，不使用 new_query()
        raw_messages = inbox.get_messages(limit=limit, query="isRead eq false")
    else:
        raw_messages = inbox.get_messages(limit=limit)

    processed_emails: List[EmailMessage] = []
    for msg in raw_messages:
        processed_emails.append(_parse_message(msg))
        # 分批写入防止内存占用过高
        if len(processed_emails) >= 50:
            save_emails_to_postgres(processed_emails)
            processed_emails = []

    if processed_emails:
        save_emails_to_postgres(processed_emails)

    return processed_emails