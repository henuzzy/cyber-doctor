'''封装调用大模型代理的API接口的函数'''
from typing import List, Dict
import os

from openai.types.chat import ChatCompletion, ChatCompletionChunk
from openai import Stream

from client.LLMclientbase import LLMclientbase
from overrides import override


# 实例化函数
class LLMclientgeneric(LLMclientbase):

    def __init__(self, *args, **krgs):
        super().__init__()

    def _completion_options(self, *, stream: bool = False) -> dict:
        options = {
            "top_p": float(os.getenv("LLM_TOP_P", "0.7")),
            "temperature": float(os.getenv("LLM_TEMPERATURE", "0.2")),
            "max_tokens": int(os.getenv("LLM_MAX_TOKENS", "4096")),
            "stream": stream,
        }
        model = (self.model_name or "").lower()
        if model.startswith("qwen") or os.getenv("LLM_PROVIDER", "").lower() == "qwen":
            # vLLM/Qwen OpenAI-compatible servers accept this in extra_body.
            # It prevents reasoning tokens from being mixed into JSON output.
            options["extra_body"] = {
                "chat_template_kwargs": {
                    "enable_thinking": os.getenv("QWEN_ENABLE_THINKING", "0") == "1"
                }
            }
        return options

    # 该函数只负责单论对话交流，不支持流式输出，无历史输入
    @override
    def chat_with_ai(self, prompt: str) -> str | None:
        response = self.client.chat.completions.create(
            model=self.model_name,
            messages=[
                {"role": "user", "content": prompt},
            ],
            **self._completion_options(),
        )
        return response.choices[0].message.content

    # 该函数支持流式输出并且可以输入历史，是主要功能函数
    @override
    def chat_with_ai_stream(
        self, prompt: str, history: List[List[str]] | None = None
    ) -> ChatCompletion | Stream[ChatCompletionChunk]:
        response = self.client.chat.completions.create(
            model=self.model_name,
            messages=self.construct_message(prompt, history if history else []),
            **self._completion_options(stream=True),
        )
        return response

    # 该函数用于构造消息，进行提示词工程
    @override
    def construct_message(
        self, prompt: str, history: List[List[str]] | None = None
    ) -> List[Dict[str, str]] | str | None:
        messages = [
            {
                "role": "system",
                "content": "你是一个乐于解答各种问题的助手，你的任务是为用户提供专业、准确、有见地的回答。",
            }
        ]

        for user_input, ai_response in history:
            messages.append({"role": "user", "content": user_input})
            messages.append({"role": "assistant", "content": ai_response.__repr__()})

        messages.append({"role": "user", "content": prompt})
        return messages

    # 该函数用于直接输入消息进行对话
    @override
    def chat_using_messages(self, messages: List[Dict]) -> str | None:
        response = self.client.chat.completions.create(
            model=self.model_name,
            messages=messages,
            **self._completion_options(),
        )

        return response.choices[0].message.content
