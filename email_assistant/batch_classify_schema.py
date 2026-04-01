from typing import TypedDict, List, Optional
from pydantic import BaseModel, Field

# 1. AI 动态生成的 Topic 定义
class TopicDefinition(BaseModel):
    id: str = Field(description="唯一的 Topic ID，如 'tech_support'")
    name: str = Field(description="人类可读的名称，如 '技术支持'")
    description: str = Field(description="该类别的详细描述和判定标准")

class TopicList(BaseModel):
    topics: List[TopicDefinition]

# 2. 单封邮件的处理结果 (不定义 Literal topic)
class EmailFinalAnalysis(BaseModel):
    topic: str = Field(description="最终匹配到的 Topic ID")
    summary: str

# 3. LangGraph 主图 State
class BatchState(TypedDict):
    # 输入
    raw_emails: List[dict] # 从 DB 捞出的原始数据
    
    # 中间产物
    email_summaries: List[str] # 用于聚类的临时摘要列表
    
    # 核心：AI 定义的分类字典
    defined_topics: List[dict] # 存储 TopicDefinition 的字典列表
    
    # 广播给子图的输入
    subgraph_inputs: List[dict] # [{email_id: x, topics: [...]}, ...]

# 4. 子图 State
class SubgraphState(TypedDict):
    email_id: str
    email_content: str
    available_topics: List[dict] # 主图广播过来的 defined_topics
    
    # 输出
    final_analysis: EmailFinalAnalysis