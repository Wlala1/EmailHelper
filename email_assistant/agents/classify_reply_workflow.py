import os
import sys
from langgraph.graph import StateGraph, START, END
from langgraph.checkpoint.memory import MemorySaver

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from schemas import EmailMessage
from classify_reply_nodes import read_email, classify_intent, draft_response, human_review, send_reply, update_db


workflow = StateGraph(EmailMessage)

# Workflow 流程说明：
# START -> read_email
#   ├─ 若 read_email 返回 error = NO_NEW_EMAILS：直接 END
#   └─ 否则进入 classify_intent
#        ├─ need_reply = True : classify_intent -> draft_response -> human_review
#        │                      ├─ approved: send_reply -> update_db -> END
#        │                      └─ rejected: update_db -> END
#        └─ need_reply = False: classify_intent -> update_db -> END

# 添加节点
workflow.add_node("read_email", read_email)
workflow.add_node("classify_intent", classify_intent)
workflow.add_node("draft_response", draft_response)
workflow.add_node("human_review", human_review)
workflow.add_node("send_reply", send_reply)
workflow.add_node("update_db", update_db)

# 构建边
workflow.add_edge(START, "read_email")

# 增加一个条件分支：如果没有新邮件，直接结束
def check_for_emails(state: EmailMessage):
    if state.get("error") == "NO_NEW_EMAILS":
        return END
    return "classify_intent"

workflow.add_conditional_edges("read_email", check_for_emails)

# send_reply 之后也要去更新数据库
workflow.add_edge("send_reply", "update_db")
workflow.add_edge("update_db", END)

memory = MemorySaver()
app = workflow.compile(checkpointer=memory)