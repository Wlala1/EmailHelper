import os
import sys
import json
from langchain_openai import ChatOpenAI
from langchain_core.prompts import ChatPromptTemplate
from psycopg2.extras import RealDictCursor
import psycopg2

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from batch_classify_schema import BatchState, SubgraphState, TopicList, EmailFinalAnalysis
from config import OPENAI_API_KEY
from classify_reply_nodes import get_db_connection

llm = ChatOpenAI(model="gpt-4o")

def read_and_sample_emails(state: BatchState) -> dict:
    """从 DB 捞取一批未处理邮件，并行生成临时摘要用于聚类"""
    conn = None
    try:
        conn = get_db_connection()
        cur = conn.cursor(cursor_factory=RealDictCursor)
        
        # 捞取 50 封作为样本
        cur.execute("SELECT * FROM emails WHERE classification IS NULL LIMIT 50;")
        emails = cur.fetchall()
        if not emails:
            return {"error": "NO_EMAILS_TO_PROCESS"}
    except Exception as e:
        print(f"❌ 读取邮件时出错: {e}")
        return {"error": "DATABASE_ERROR"}
    finally:
        if conn:
            conn.close()

    # 并行生成摘要 (这里可以用 asyncio.gather 加速，为了展示核心逻辑用同步)
    summaries = []
    prompt = ChatPromptTemplate.from_messages([
        ("system", "简要用英文总结以下邮件内容（15字以内）。"),
        ("human", "{content}")
    ])
    chain = prompt | llm
    
    for e in emails:
        # 只取正文前 500 字生成临时摘要
        res = chain.invoke({"content": e['body'][:500]})
        summaries.append(f"ID: {e['id']} | {res.content}")

    return {
        "raw_emails": emails,
        "email_summaries": summaries
    }

def define_topics_from_summaries(state: BatchState) -> dict:
    """让 LLM 观察 50 个摘要，自己生成分类标准"""
    
    all_summaries_text = "\n".join(state["email_summaries"])
    
    structured_llm = llm.with_structured_output(TopicList)
    
    prompt = f"""
    你是受过训练的数据科学家。以下是从 50 封邮件中提取的简要摘要：
    
    {all_summaries_text}
    
    任务：
    1. 观察这些摘要，归纳出 5 到 10 个互斥且完备的分类主题（Topics）。
    2. 为每个主题生成一个唯一的 ID、名称和判定标准描述。
    3. 如果有些邮件过于杂乱，可以定义一个 'other' 类别。
    
    请输出结构化的主题列表。
    """
    
    # LLM 进行“无监督”聚类并定义 Schema
    topic_list = structured_llm.invoke(prompt)
    defined_topics_dicts = [t.dict() for t in topic_list.topics]
    
    print(f"✅ AI 成功定义了 {len(defined_topics_dicts)} 个分类主题。")
    for t in defined_topics_dicts:
        print(f"  - [{t['id']}] {t['name']}: {t['description'][:30]}...")

    # 准备并行映射的输入数据
    subgraph_inputs = []
    for e in state["raw_emails"]:
        subgraph_inputs.append({
            "email_id": e['id'],
            "email_content": e['body'],
            "available_topics": defined_topics_dicts # 广播全局 Topics
        })

    return {
        "defined_topics": defined_topics_dicts,
        "subgraph_inputs": subgraph_inputs
    }

def finalize_email_processing(state: SubgraphState) -> dict:
    """子图节点：根据主图定义的 Topics，对单封邮件进行最终分类和回写"""
    
    topics_context = "\n".join([
        f"ID: {t['id']} | 名称: {t['name']} | 描述: {t['description']}" 
        for t in state['available_topics']
    ])
    
    structured_llm = llm.with_structured_output(EmailFinalAnalysis)
    
    prompt = f"""
    请分析以下邮件，并从提供的候选类别中选择一个最合适的匹配。
    
    [邮件正文]
    {state['email_content'][:2000]}
    
    [候选类别 (Topics)]
    {topics_context}
    
    任务：输出该邮件的匹配 ID、摘要和紧急程度。
    """
    
    analysis = structured_llm.invoke(prompt)
    
    # --- 核心：这里直接执行 UPDATE DB ---
    conn = None
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        # 存入 classification 列 (JSONB)
        cur.execute(
            "UPDATE emails SET classification = %s WHERE id = %s",
            (json.dumps(analysis.dict()), state['email_id'])
        )
        conn.commit()
    finally:
        if conn: cur.close()
    
    print(f"  - [子图已回写] {state['email_id']} -> {analysis.topic}")
    
    return {"final_analysis": analysis}