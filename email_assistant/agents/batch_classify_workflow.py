import os
import sys
from langgraph.types import Send
from langgraph.graph import StateGraph, START, END

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from batch_classify_schema import BatchState, SubgraphState
from batch_classify_nodes import read_and_sample_emails, define_topics_from_summaries
from batch_classify_nodes import finalize_email_processing


def continue_to_subgraph(state: BatchState):
    # 为列表中的每一封邮件创建一个 Send 指令
    # 参数1: 子图中的目标节点名
    # 参数2: 传给子图的特定 state 
    return [
        Send("finalize", {
            "email_id": item["email_id"],
            "email_content": item["email_content"],
            "available_topics": state["defined_topics"]
        }) for item in state["subgraph_inputs"]
    ]

# Flow chart:
# START --edge--> read_sample --edge--> define_topics
# define_topics --conditional_edge(continue_to_subgraph)--> finalize x N (one Send per email)
# finalize --edge--> END

batch_workflow = StateGraph(BatchState)

# 节点只保留主图自己的
batch_workflow.add_node("read_sample", read_and_sample_emails)
batch_workflow.add_node("define_topics", define_topics_from_summaries)

# 连线
batch_workflow.add_edge(START, "read_sample")
batch_workflow.add_edge("read_sample", "define_topics")

# 核心修改：使用条件边分发到子图节点
batch_workflow.add_conditional_edges(
    "define_topics",
    continue_to_subgraph,
    ["finalize"] # 告诉主图，这些任务要去 finalize 节点
)

# 确保 finalize 节点被添加到主图中（或者定义为一个子图）
batch_workflow.add_node("finalize", finalize_email_processing)
batch_workflow.add_edge("finalize", END)

app = batch_workflow.compile()

if __name__ == "__main__":
    import time
    print("🚀 启动全自动动态分类消化器...")
    
    batch_size = 50
    total_processed = 0
    
    while True:
        try:
            print(f"\n🔄 正在读取下一批 {batch_size} 封邮件...")
            # 运行主图
            result = app.invoke({}) # 初始 state 为空
            
            if "error" in result:
                print("🏁 数据库已清空，任务完成！")
                break
                
            processed_this_round = len(result["raw_emails"])
            total_processed += processed_this_round
            
            print(f"✅ 本轮成功处理 {processed_this_round} 封邮件。")
            print(f"📊 累计处理进度: {total_processed}/1000...")
            
            # AI 生成的主题列表也可以存起来供以后分析
            # print(f"当前批次定义的 Topics: {[t['id'] for t in result['defined_topics']]}")
            
            # 稍微休息一下，避免触发 Rate Limit
            time.sleep(2) 
            
        except Exception as e:
            print(f"❌ 运行崩溃: {e}")
            break