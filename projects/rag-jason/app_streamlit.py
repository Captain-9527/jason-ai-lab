# Streamlit UI 入口
import time
import streamlit as st
from pathlib import Path
from src.runtime import DATA_ROOT
from src.pipeline import Pipeline, default_config
import json

# 数据根目录
root_path = DATA_ROOT
pipeline = Pipeline(root_path, run_config=default_config)

st.set_page_config(page_title="RAG-Jason 企业知识库", layout="wide")

# 页面标题
st.markdown("""
<div style='background: linear-gradient(90deg, #1a73e8 0%, #4fc3f7 100%); padding: 20px 0; border-radius: 12px; text-align: center;'>
    <h2 style='color: white; margin: 0;'>📚 RAG-Jason 企业知识库问答系统</h2>
    <div style='color: #e3f2fd; font-size: 15px;'>多PDF · FAISS+BM25混合检索 · LLM重排 · qwen-plus</div>
</div>
""", unsafe_allow_html=True)

# 侧边栏：配置和输入
with st.sidebar:
    st.header("查询设置")
    user_question = st.text_area(
        "输入问题",
        "请简要总结公司2022年主营业务的主要内容。",
        height=100
    )
    submit_btn = st.button("🔍 生成答案", use_container_width=True)

    st.markdown("---")
    st.markdown("**当前配置**")
    st.markdown(f"- 回答模型: `{default_config.answering_model}`")
    st.markdown(f"- 重排模型: `{default_config.reranking_model}`")
    st.markdown(f"- Embedding: `{default_config.embedding_model}`")
    st.markdown(f"- BM25混合检索: `{'开启' if default_config.use_bm25_db else '关闭'}`")
    st.markdown(f"- LLM重排: `{'开启' if default_config.llm_reranking else '关闭'}`")
    st.markdown(f"- 检索Top-K: `{default_config.top_n_retrieval}`")

# 主内容区
st.markdown("<h3 style='margin-top: 24px;'>检索结果</h3>", unsafe_allow_html=True)

if submit_btn and user_question.strip():
    with st.spinner("正在检索和推理，请稍候..."):
        try:
            t_start = time.time()
            answer = pipeline.answer_single_question(user_question, kind="string")
            t_end = time.time()

            # 解析答案结构
            if isinstance(answer, str):
                try:
                    answer_dict = json.loads(answer)
                except Exception:
                    st.error("返回内容无法解析为结构化答案：" + str(answer))
                    answer_dict = {}
            else:
                answer_dict = answer

            # 兼容嵌套格式
            content = answer_dict.get("content", answer_dict)
            if isinstance(content, dict) and "final_answer" in content:
                inner = content.get("final_answer", "")
                if isinstance(inner, str) and inner.strip().startswith('{'):
                    try:
                        content = json.loads(inner)
                    except Exception:
                        pass

            step_by_step = content.get("step_by_step_analysis", "-") if isinstance(content, dict) else "-"
            reasoning_summary = content.get("reasoning_summary", "-") if isinstance(content, dict) else "-"
            relevant_pages = content.get("relevant_pages", []) if isinstance(content, dict) else []
            final_answer = content.get("final_answer", "-") if isinstance(content, dict) else str(content)

            # 确保 final_answer 是可展示的字符串
            if isinstance(final_answer, dict):
                final_answer = json.dumps(final_answer, ensure_ascii=False, indent=2)
            elif isinstance(final_answer, list):
                final_answer = "\n".join(str(item) for item in final_answer)
            elif not isinstance(final_answer, str):
                final_answer = str(final_answer)

            # 展示结果
            col1, col2 = st.columns([2, 1])

            with col1:
                st.markdown("**📝 分步推理：**")
                if isinstance(step_by_step, list):
                    for i, step in enumerate(step_by_step, 1):
                        st.markdown(f"{i}. {step}")
                else:
                    st.info(str(step_by_step))
                st.markdown("**💡 推理摘要：**")
                st.success(str(reasoning_summary))
                st.markdown("**📄 最终答案：**")
                st.markdown(
                    f"<div style='background:#ffffff;color:#000000;padding:18px 24px;"
                    f"border-radius:8px;font-size:20px;font-weight:bold;"
                    f"border-left:5px solid #1a73e8;box-shadow:0 2px 8px rgba(0,0,0,0.1);'>"
                    f"{final_answer}</div>",
                    unsafe_allow_html=True
                )

            with col2:
                st.markdown("**📑 相关页面/行号：**")
                if relevant_pages:
                    for p in relevant_pages:
                        if isinstance(p, dict):
                            st.markdown(f"- 第 {p.get('page', '?')} 页, 行 {p.get('lines', '')}")
                        else:
                            st.markdown(f"- 第 {p} 页")
                else:
                    st.markdown("无")

                # 展示来源信息
                company = answer_dict.get("company_name", "")
                sha1 = answer_dict.get("sha1", "")
                if company:
                    st.markdown(f"**🏢 公司：** {company}")
                if sha1:
                    st.markdown(f"**🔑 SHA1：** `{sha1}`")

                # 耗时信息
                st.markdown("---")
                st.markdown(f"**⏱ 总耗时：** {t_end - t_start:.1f} 秒")

        except Exception as e:
            st.error(f"生成答案时出错: {e}")
else:
    st.info("请在左侧输入问题并点击【生成答案】")
