import base64
from qa.answer import get_answer
from qa.question_parser import parse_question
from qa.function_tool import process_image_describe_tool
from qa.purpose_type import userPurposeType

import PyPDF2
import chardet
import mimetypes
import gradio as gr
from icecream import ic
from docx import Document
import os


AVATAR = ("resource/user.png", "resource/bot.jpg")
os.environ["KMP_DUPLICATE_LIB_OK"] = "TRUE"

# pip install PyPDF2
def pdf_to_str(pdf_file):
    reader = PyPDF2.PdfReader(pdf_file)
    text = ""
    for page in reader.pages:
        text += page.extract_text()
    return text


def docx_to_str(file_path):
    doc = Document(file_path)
    text = []
    for paragraph in doc.paragraphs:
        text.append(paragraph.text)
    return "\n".join(text)


# pip install chardet
def text_file_to_str(text_file):
    with open(text_file, "rb") as file:
        raw_data = file.read()
        result = chardet.detect(raw_data)
        encoding = result["encoding"]

    # 使用检测到的编码来读取文件
    with open(text_file, "r", encoding=encoding) as file:
        return file.read()


def image_to_base64(image_path):
    with open(image_path, "rb") as image_file:
        encoded_string = base64.b64encode(image_file.read()).decode("utf-8")
        return encoded_string


# 核心函数
def grodio_view(chatbot, chat_input):
    empty_input = {"text": "", "files": []}

    # 用户消息立即显示
    user_message = chat_input["text"]
    bot_response = "loading..."
    chatbot.append([user_message, bot_response])
    yield chatbot, empty_input

    # 处理用户上传的文件
    files = chat_input["files"]
    images = []
    pdfs = []
    docxs = []
    texts = []

    for file in files:
        file_type, _ = mimetypes.guess_type(file)
        if file_type and file_type.startswith("image/"):
            images.append(file)
        elif file_type and file_type.startswith("application/pdf"):
            pdfs.append(file)
        elif file_type and file_type.startswith(
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        ):
            docxs.append(file)
        elif file_type and file_type.startswith("text/"):
            texts.append(file)
        else:
            user_message += "请你将下面的句子修饰后输出，不要包含额外的文字，句子:'该文件为不支持的文件类型'"
            print(f"Unknown file type: {file_type}")

    # 图片文件解析
    if images != []:
        image_url = images
        image_base64 = [image_to_base64(image) for image in image_url]

        for i, image in enumerate(image_base64):
            chatbot[-1][
                0
            ] += f"""
                <div>
                    <img src="data:image/png;base64,{image}" alt="Generated Image" style="max-width: 100%; height: auto; cursor: pointer;" />
                </div>
                """
            yield chatbot, empty_input
    else:
        image_url = None

    question_type = parse_question(user_message, image_url)
    ic(question_type)

    if pdfs != []:
        for i, pdf in enumerate(pdfs):
            pdf_text = pdf_to_str(pdf)
            user_message += f"PDF{i+1}内容：{pdf_text}"

    if docxs != []:
        for i, docx in enumerate(docxs):
            docx_text = docx_to_str(docx)
            user_message += f"DOCX{i+1}内容：{docx_text}"

    if texts != []:
        for i, text in enumerate(texts):
            text_string = text_file_to_str(text)
            user_message += f"文本{i+1}内容：{text_string}"

    if user_message == "":
        user_message = "请你将下面的句子修饰后输出，不要包含额外的文字，句子:'请问您有什么想了解的，我将尽力为您服务'"
    answer = get_answer(user_message, chatbot, question_type, image_url)
    bot_response = ""

    # 处理文本生成/其他/文档检索/知识图谱检索
    if (
        answer[1] == userPurposeType.text
        or answer[1] == userPurposeType.RAG
        or answer[1] == userPurposeType.MNGSJudge
        or answer[1] == userPurposeType.KnowledgeGraph
    ):
        # 流式输出
        for chunk in answer[0]:
            bot_response = bot_response + (chunk.choices[0].delta.content or "")
            chatbot[-1][1] = bot_response
            yield chatbot, empty_input

    # 处理图片生成
    if answer[1] == userPurposeType.ImageGeneration:
        image_url = answer[0]
        describe = process_image_describe_tool(
            question_type=userPurposeType.ImageDescribe,
            question="描述这个图片，不要识别‘AI生成’",
            history="",
            image_url=[image_url],
        )
        combined_message = f"""
            **生成的图片:**
            ![Generated Image]({image_url})
            {describe[0]}
            """
        chatbot[-1][1] = combined_message
        yield chatbot, empty_input

    # 处理图片描述
    if answer[1] == userPurposeType.ImageDescribe:
        for i in range(0, len(answer[0]), 1):
            bot_response += answer[0][i : i + 1]  # 累加当前chunk到combined_message
            chatbot[-1][1] = bot_response  # 更新chatbot对话中的最后一条消息
            yield chatbot, empty_input  # 实时输出当前累积的对话内容

    # 处理联网搜索
    if answer[1] == userPurposeType.InternetSearch:
        if answer[3] == False:
            output_message = (
                "由于网络问题，访问互联网失败，下面由我根据现有知识给出回答："
            )
        else:
            # 将字典中的内容转换为 Markdown 格式的链接
            links = "\n".join(f"[{title}]({link})" for link, title in answer[2].items())
            links += "\n"
            output_message = f"参考资料：{links}"
        for i in range(0, len(output_message)):
            bot_response = output_message[: i + 1]
            chatbot[-1][1] = bot_response
            yield chatbot, empty_input
        for chunk in answer[0]:
            bot_response = bot_response + (chunk.choices[0].delta.content or "")
            chatbot[-1][1] = bot_response
            yield chatbot, empty_input


# 构建 Gradio 界面
with gr.Blocks() as demo:
    # 创建聊天布局
    with gr.Row():
        with gr.Column(scale=10):
            chatbot = gr.Chatbot(
                height=600,
                avatar_images=AVATAR,
                show_copy_button=True,
                show_label=False,
                latex_delimiters=[
                    {"left": "\\(", "right": "\\)", "display": True},
                    {"left": "\\[", "right": "\\]", "display": True},
                    {"left": "$$", "right": "$$", "display": True},
                    {"left": "$", "right": "$", "display": True},
                ],
                placeholder="\n## 欢迎与我对话",
            )

    with gr.Row():
        with gr.Column(scale=9):
            chat_input = gr.MultimodalTextbox(
                interactive=True,
                file_count="multiple",
                placeholder="输入消息或上传文件...",
                show_label=False,
            )

    chat_input.submit(
        fn=grodio_view,
        inputs=[chatbot, chat_input],
        outputs=[chatbot, chat_input],
    )


# 启动应用
def start_gradio():
    demo.launch(server_port=10032, share=False)


if __name__ == "__main__":
    start_gradio()
