# API请求封装模块，统一DashScope LLM调用接口
import os
import json
from typing import Union, List, Dict, Type, Optional, Literal
from pydantic import BaseModel
from json_repair import repair_json
import dashscope

import src.prompts as prompts


class BaseDashscopeProcessor:
    """DashScope（通义千问）LLM处理器"""

    def __init__(self):
        dashscope.api_key = os.getenv("DASHSCOPE_API_KEY")
        self.default_model = 'qwen-plus'

    def send_message(
        self,
        model="qwen-plus",
        temperature=0.1,
        seed=None,
        system_content='You are a helpful assistant.',
        human_content='Hello!',
        is_structured=False,
        response_format=None,
        **kwargs
    ):
        """
        发送消息到DashScope Qwen大模型。
        支持结构化JSON输出解析。
        """
        if model is None:
            model = self.default_model
        messages = []
        if system_content:
            messages.append({"role": "system", "content": system_content})
        if human_content:
            messages.append({"role": "user", "content": human_content})

        response = dashscope.Generation.call(
            model=model,
            messages=messages,
            temperature=temperature,
            result_format='message'
        )

        # 提取内容
        if hasattr(response, 'output') and hasattr(response.output, 'choices'):
            content = response.output.choices[0].message.content
        else:
            content = str(response)

        # 记录用量
        self.response_data = {
            "model": model,
            "input_tokens": response.usage.input_tokens if hasattr(response, 'usage') and hasattr(response.usage, 'input_tokens') else None,
            "output_tokens": response.usage.output_tokens if hasattr(response, 'usage') and hasattr(response.usage, 'output_tokens') else None
        }

        # 尝试解析JSON
        try:
            content_str = content.strip()
            if content_str.startswith('```') and '```' in content_str[3:]:
                first_backtick = content_str.find('```') + 3
                next_newline = content_str.find('\n', first_backtick)
                if next_newline > 0:
                    first_backtick = next_newline + 1
                last_backtick = content_str.rfind('```')
                if last_backtick > first_backtick:
                    json_str = content_str[first_backtick:last_backtick].strip()
                else:
                    json_str = content_str
            else:
                json_str = content_str

            parsed_content = json.loads(json_str)
            return parsed_content
        except (json.JSONDecodeError, TypeError):
            # 尝试 json_repair
            try:
                repaired = repair_json(content)
                return json.loads(repaired)
            except Exception:
                return {
                    "final_answer": content,
                    "step_by_step_analysis": "",
                    "reasoning_summary": "",
                    "relevant_pages": []
                }


class APIProcessor:
    """统一API处理器，路由到具体的LLM提供商"""

    def __init__(self, provider: Literal["dashscope"] = "dashscope"):
        self.provider = provider.lower()
        if self.provider == "dashscope":
            self.processor = BaseDashscopeProcessor()
        else:
            raise ValueError(f"不支持的provider: {provider}")

    def send_message(
        self,
        model=None,
        temperature=0.5,
        seed=None,
        system_content="You are a helpful assistant.",
        human_content="Hello!",
        is_structured=False,
        response_format=None,
        **kwargs
    ):
        """路由send_message到具体处理器"""
        if model is None:
            model = self.processor.default_model
        return self.processor.send_message(
            model=model,
            temperature=temperature,
            seed=seed,
            system_content=system_content,
            human_content=human_content,
            is_structured=is_structured,
            response_format=response_format,
            **kwargs
        )

    def get_answer_from_rag_context(self, question: str, rag_context: str,
                                     schema: str, model: str) -> dict:
        """从RAG上下文中获取结构化答案"""
        system_prompt, response_format, user_prompt = self._build_rag_context_prompts(schema)

        answer_dict = self.processor.send_message(
            model=model,
            system_content=system_prompt,
            human_content=user_prompt.format(context=rag_context, question=question),
            is_structured=True,
            response_format=response_format
        )
        self.response_data = self.processor.response_data

        # 兜底：确保返回标准结构
        if not isinstance(answer_dict, dict) or 'step_by_step_analysis' not in answer_dict:
            if isinstance(answer_dict, dict) and 'final_answer' in answer_dict:
                final_answer_content = answer_dict.get("final_answer", "N/A")
                if isinstance(final_answer_content, str) and final_answer_content.strip().startswith('{'):
                    try:
                        structured_data = json.loads(final_answer_content)
                        answer_dict = structured_data
                    except json.JSONDecodeError:
                        answer_dict = {
                            "step_by_step_analysis": answer_dict.get("step_by_step_analysis", ""),
                            "reasoning_summary": answer_dict.get("reasoning_summary", ""),
                            "relevant_pages": answer_dict.get("relevant_pages", []),
                            "final_answer": answer_dict.get("final_answer", "N/A")
                        }
                else:
                    answer_dict = {
                        "step_by_step_analysis": answer_dict.get("step_by_step_analysis", ""),
                        "reasoning_summary": answer_dict.get("reasoning_summary", ""),
                        "relevant_pages": answer_dict.get("relevant_pages", []),
                        "final_answer": answer_dict.get("final_answer", "N/A")
                    }
            else:
                answer_dict = {
                    "step_by_step_analysis": "",
                    "reasoning_summary": "",
                    "relevant_pages": [],
                    "final_answer": "N/A"
                }
        return answer_dict

    def _build_rag_context_prompts(self, schema: str):
        """根据schema类型构建对应的提示词"""
        if schema == "name":
            system_prompt = prompts.AnswerWithRAGContextNamePrompt.system_prompt
            response_format = prompts.AnswerWithRAGContextNamePrompt.AnswerSchema
            user_prompt = prompts.AnswerWithRAGContextNamePrompt.user_prompt
        elif schema == "number":
            system_prompt = prompts.AnswerWithRAGContextNumberPrompt.system_prompt
            response_format = prompts.AnswerWithRAGContextNumberPrompt.AnswerSchema
            user_prompt = prompts.AnswerWithRAGContextNumberPrompt.user_prompt
        elif schema == "boolean":
            system_prompt = prompts.AnswerWithRAGContextBooleanPrompt.system_prompt
            response_format = prompts.AnswerWithRAGContextBooleanPrompt.AnswerSchema
            user_prompt = prompts.AnswerWithRAGContextBooleanPrompt.user_prompt
        elif schema == "names":
            system_prompt = prompts.AnswerWithRAGContextNamesPrompt.system_prompt
            response_format = prompts.AnswerWithRAGContextNamesPrompt.AnswerSchema
            user_prompt = prompts.AnswerWithRAGContextNamesPrompt.user_prompt
        elif schema == "comparative":
            system_prompt = prompts.ComparativeAnswerPrompt.system_prompt
            response_format = prompts.ComparativeAnswerPrompt.AnswerSchema
            user_prompt = prompts.ComparativeAnswerPrompt.user_prompt
        elif schema == "string":
            system_prompt = prompts.AnswerWithRAGContextStringPrompt.system_prompt
            response_format = prompts.AnswerWithRAGContextStringPrompt.AnswerSchema
            user_prompt = prompts.AnswerWithRAGContextStringPrompt.user_prompt
        else:
            raise ValueError(f"不支持的schema类型: {schema}")
        return system_prompt, response_format, user_prompt

    def get_rephrased_questions(self, original_question: str,
                                 companies: List[str]) -> Dict[str, str]:
        """使用LLM将比较类问题拆分为各公司的独立问题"""
        answer_dict = self.processor.send_message(
            system_content=prompts.RephrasedQuestionsPrompt.system_prompt,
            human_content=prompts.RephrasedQuestionsPrompt.user_prompt.format(
                question=original_question,
                companies=", ".join([f'"{company}"' for company in companies])
            ),
            is_structured=True,
            response_format=prompts.RephrasedQuestionsPrompt.RephrasedQuestions
        )

        if isinstance(answer_dict, dict) and "questions" in answer_dict:
            questions_dict = {
                item["company_name"]: item["question"]
                for item in answer_dict["questions"]
            }
        else:
            # 兜底：每个公司用原始问题
            questions_dict = {company: original_question for company in companies}

        return questions_dict
