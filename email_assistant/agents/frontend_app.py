import streamlit as st
import pandas as pd
import plotly.express as px
import requests

st.set_page_config(page_title="Email Agent Insights", layout="wide")

st.title("📧 Email Classification Distribution")
st.write("基于 AI 动态聚类生成的邮件分布报告")

# 1. 获取数据
try:
    # 假设你的 API 跑在 8000 端口
    response = requests.get("http://localhost:8000/api/stats")
    data = response.json()
    df = pd.DataFrame(data)

    if not df.empty:
        # 2. 布局：左边饼图，右边柱状图
        col1, col2 = st.columns(2)

        with col1:
            st.subheader("📊 占比分布 (Pie Chart)")
            fig_pie = px.pie(df, names='label', values='value', hole=0.4,
                             color_discrete_sequence=px.colors.sequential.RdBu)
            st.plotly_chart(fig_pie, use_container_width=True)

        with col2:
            st.subheader("📈 数量排名 (Bar Chart)")
            fig_bar = px.bar(df, x='label', y='value', color='value',
                             labels={'label': 'Topic', 'value': 'Count'})
            st.plotly_chart(fig_bar, use_container_width=True)

        # 3. 详细表格
        st.subheader("📋 详细分类数据")
        st.dataframe(df, use_container_width=True)
    else:
        st.warning("数据库中暂无已分类数据，请先运行 batch_classify_workflow.py")

except Exception as e:
    st.error(f"无法连接到 API 服务: {e}")