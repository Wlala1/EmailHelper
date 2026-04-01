import os
import sys
from langgraph.types import Command

# 确保导入路径正确
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from classify_reply_workflow import app  # 假设你的编译好的 Graph 叫 app
import psycopg2

def check_db_results(email_id):
    """测试完成后，检查数据库中的实际数据"""
    conn = psycopg2.connect(
        dbname="fetched_data", user="ouma_user", 
        password="ouma_password", host="localhost", port="5432"
    )
    cur = conn.cursor()
    cur.execute("SELECT classification, suggested_reply, reply_sent, processed_at FROM emails WHERE id = %s", (email_id,))
    result = cur.fetchone()
    conn.close()
    return result

def run_test():
    print("🚀 开始测试 Email Agent 工作流...")

    # 1. 第一步：运行直到遇到中断 (Human Review)
    # 我们传入空消息，让 read_email 自动从 DB 捞取
    print("\n--- 阶段 1: AI 正在读取并分析邮件 ---")
    initial_input = {"messages": []}
    
    # 使用 thread_id 来保持会话，这对 interrupt 很重要
    config = {"configurable": {"thread_id": "test_run_001"}}
    
    events = app.stream(initial_input, config, stream_mode="values")
    
    last_state = None
    for event in events:
        last_state = event
        if "id" in event and event["id"]:
            print(f"📍 当前处理邮件 ID: {event['id']}")
            print(f"📝 摘要: {event.get('classification', {}).get('summary', '分析中...')}")

    # 2. 检查是否卡在了 human_review 节点
    snapshot = app.get_state(config)
    if snapshot.next:
        print(f"\n✋ 工作流已暂停，等待节点: {snapshot.next}")
        print(f"🤖 AI 建议回复: {last_state.get('suggested_reply')}")

        # 3. 模拟人类操作：批准并修改回复
        print("\n--- 阶段 2: 模拟人工审核 (批准并微调) ---")
        human_feedback = {
            "approved": True,
            "edited_reply": last_state.get('suggested_reply') + "\n\nBest regards,\nOuma Team"
        }
        
        # 使用 Command 继续执行
        app.invoke(Command(resume=human_feedback), config)
        print("✅ 人工审核已提交，正在完成后续流程...")
    else:
        print("💡 工作流直接结束（可能因为不需要回复或没搜到邮件）")

    # 4. 最终验证数据库
    if last_state and last_state.get("id"):
        email_id = last_state["id"]
        final_data = check_db_results(email_id)
        print(f"\n最终数据库状态检查 (ID: {email_id}):")
        print(f" - classification: {final_data[0]}")
        print(f" - suggested_reply: {final_data[1]}")
        print(f" - reply_sent: {final_data[2]}")
        print(f" - processed_at: {final_data[3]}")

if __name__ == "__main__":
    run_test()