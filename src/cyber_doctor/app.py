import base64
from cyber_doctor.env import get_app_root
from cyber_doctor.qa.answer import get_answer
from cyber_doctor.qa.question_parser import parse_question
from cyber_doctor.qa.function_tool import process_image_describe_tool
from cyber_doctor.qa.purpose_type import userPurposeType

import PyPDF2
import chardet
import mimetypes
import gradio as gr
from icecream import ic
from docx import Document
import os
from pathlib import Path
from uuid import uuid4
from cyber_doctor.reporting.mngs_report import export_latest_chat_pdf
from cyber_doctor.reporting.mngs_report import export_report_pdf, _parse_json_response, _judgement_items
from cyber_doctor.mngs.rag_judge import parse_mngs_cases, revise_judgements_with_doctor_feedback


RESOURCE_DIR = Path(__file__).resolve().parent / "resources"
AVATAR = (str(RESOURCE_DIR / "user.png"), str(RESOURCE_DIR / "bot.jpg"))
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


def export_current_chat_pdf(chatbot):
    """Create a downloadable PDF from the latest structured mNGS answer."""
    output_dir = Path(get_app_root()) / "outputs"
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"mNGS_可解释性诊断报告_{uuid4().hex[:8]}.pdf"
    return export_latest_chat_pdf(str(output_path), chatbot)


def export_review_report(report_state):
    if not report_state or not report_state.get("cases"):
        raise ValueError("当前没有可导出的 mNGS 报告")
    output_dir = Path(get_app_root()) / "outputs"
    output_dir.mkdir(parents=True, exist_ok=True)
    version = int(report_state.get("version") or 1)
    output_path = output_dir / f"mNGS_可解释性诊断报告_v{version}_{uuid4().hex[:8]}.pdf"
    return export_report_pdf(str(output_path), report_state["cases"], version=version)


def render_report_state(report_state):
    if not report_state or not report_state.get("cases"):
        return "尚无可审阅的 mNGS 报告。"
    version = int(report_state.get("version") or 1)
    sections = [f"### 当前报告：第 {version} 版"]
    for index, case in enumerate(report_state["cases"], 1):
        name = case.get("Chinese") or case.get("Latin") or f"病例 {index}"
        sections.append(
            f"#### {index}. {name}｜既有判定：**{case.get('label', '未提供')}**\n\n"
            f"**置信度：** {case.get('confidence', '未提供')}\n\n"
            f"**解释：** {case.get('explanation', '未提供')}\n\n"
            f"**临床匹配：** {case.get('clinical_match', '未提供')}\n\n"
            f"**证据局限：** {case.get('limitations', [])}\n\n"
            f"**建议复核：** {case.get('review_items', [])}"
        )
        if case.get("doctor_feedback"):
            sections.append("**已纳入的医生意见：**\n\n" + "\n".join(f"- {item}" for item in case["doctor_feedback"]))
    return "\n\n".join(sections)


def review_report(feedback, report_state):
    if not report_state or not report_state.get("cases"):
        raise ValueError("请先完成一次 mNGS 判别")
    revised_cases = revise_judgements_with_doctor_feedback(report_state["cases"], feedback)
    state = {**report_state, "cases": revised_cases, "version": int(report_state.get("version") or 1) + 1}
    return state, render_report_state(state), export_review_report(state), ""


# 核心函数
def grodio_view(chatbot, chat_input, previous_report_state=None):
    empty_input = {"text": "", "files": []}
    report_state = previous_report_state

    # 用户消息立即显示
    user_message = chat_input["text"]
    bot_response = "loading..."
    chatbot.append([user_message, bot_response])
    yield chatbot, empty_input, None, report_state, render_report_state(report_state)

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
            yield chatbot, empty_input, None
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
    report_state = None

    # 处理文本生成/其他/文档检索/知识图谱检索
    if (
        answer[1] == userPurposeType.text
        or answer[1] == userPurposeType.RAG
        or answer[1] == userPurposeType.MNGSJudge
        or answer[1] == userPurposeType.KnowledgeGraph
    ):
        # 流式输出
        for chunk in answer[0]:
            if isinstance(chunk, str):
                bot_response += chunk
            else:
                bot_response += chunk.choices[0].delta.content or ""
            chatbot[-1][1] = bot_response
            yield chatbot, empty_input, None

    if answer[1] == userPurposeType.MNGSJudge and bot_response.strip():
        try:
            payload = _parse_json_response(bot_response)
            judged_cases = _judgement_items(payload)
            parsed_cases = parse_mngs_cases(user_message)
            if len(judged_cases) == len(parsed_cases):
                for judgement, case in zip(judged_cases, parsed_cases):
                    judgement["raw_case"] = {
                        "case_id": case.case_id,
                        "pathogen": case.species_chinese or case.species_latin,
                        "sample_type": case.sample_type,
                        "phenotype": case.phenotype,
                        "diagnosis": case.diagnosis,
                        "immune_status": case.immune_status,
                        "reads": case.reads,
                        "coverage": case.coverage,
                        "abundance": case.abundance,
                    }
                report_state = {"cases": judged_cases, "version": 1}
                generated_pdf = export_review_report(report_state)
            else:
                generated_pdf = export_current_chat_pdf(chatbot)
        except Exception as exc:
            generated_pdf = None
            print(f"自动生成 mNGS PDF 失败: {exc}")
        # Keep generated reports together while Gradio serves them for download.
        yield chatbot, empty_input, generated_pdf, report_state, render_report_state(report_state)

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
        yield chatbot, empty_input, None

    # 处理图片描述
    if answer[1] == userPurposeType.ImageDescribe:
        for i in range(0, len(answer[0]), 1):
            bot_response += answer[0][i : i + 1]  # 累加当前chunk到combined_message
            chatbot[-1][1] = bot_response  # 更新chatbot对话中的最后一条消息
            yield chatbot, empty_input, None  # 实时输出当前累积的对话内容

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
            yield chatbot, empty_input, None
        for chunk in answer[0]:
            bot_response = bot_response + (chunk.choices[0].delta.content or "")
            chatbot[-1][1] = bot_response
            yield chatbot, empty_input, None


# 构建 Gradio 界面
with gr.Blocks() as demo:
    report_state = gr.State(None)
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

    export_pdf = gr.Button("重新生成 PDF", variant="secondary")
    export_file = gr.DownloadButton(label="下载 PDF 报告", variant="primary")

    with gr.Accordion("医生审阅与报告修订", open=True):
        report_preview = gr.Markdown("尚无可审阅的 mNGS 报告。")
        doctor_feedback = gr.Textbox(
            label="医生审阅意见",
            placeholder="可补充病史/检查结果，指出证据解释需要调整之处，或列出希望报告回答的问题。",
            lines=5,
        )
        revise_button = gr.Button("根据意见生成修订版", variant="primary")

    export_pdf.click(
        fn=export_current_chat_pdf,
        inputs=[chatbot],
        outputs=[export_file],
    )

    chat_input.submit(
        fn=grodio_view,
        inputs=[chatbot, chat_input],
        outputs=[chatbot, chat_input, export_file, report_state, report_preview],
    )

    export_pdf.click(fn=export_review_report, inputs=[report_state], outputs=[export_file])
    revise_button.click(
        fn=review_report,
        inputs=[doctor_feedback, report_state],
        outputs=[report_state, report_preview, export_file, doctor_feedback],
    )


# 启动应用
def start_gradio():
    server_port = int(os.getenv("GRADIO_SERVER_PORT", "10032"))
    server_name = os.getenv("GRADIO_SERVER_NAME", "127.0.0.1")
    demo.launch(server_name=server_name, server_port=server_port, share=False)


if __name__ == "__main__":
    start_gradio()
