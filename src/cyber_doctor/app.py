import base64
import html
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
from cyber_doctor.reporting.mngs_report import export_latest_chat_pdf, export_report_pdf
from cyber_doctor.reporting.mngs_report import _parse_json_response, _judgement_items
from cyber_doctor.mngs.rag_judge import parse_mngs_cases, revise_judgements_with_doctor_feedback
from cyber_doctor.mngs.session import (
    active_report_session,
    add_report_session,
    append_report_revision,
    confirm_report_session,
    create_report_session,
    create_report_workspace,
    reopen_report_session,
    select_report_session,
    update_report_session,
)


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
    session = active_report_session(report_state)
    if not session or not session.get("cases"):
        raise ValueError("当前没有可导出的 mNGS 报告")
    output_dir = Path(get_app_root()) / "outputs"
    output_dir.mkdir(parents=True, exist_ok=True)
    version = int(session.get("version") or 1)
    output_path = output_dir / f"mNGS_可解释性诊断报告_v{version}_{uuid4().hex[:8]}.pdf"
    return export_report_pdf(
        str(output_path),
        session["cases"],
        version=version,
        status=str(session.get("status") or "in_review"),
    )


def render_report_state(report_state):
    session = active_report_session(report_state)
    if not session or not session.get("cases"):
        return "尚无可审阅的 mNGS 报告。"
    version = int(session.get("version") or 1)
    status_label = "医生已确认" if session.get("status") == "confirmed" else "待医生审阅"
    sections = [f"### {html.escape(str(session.get('title') or 'mNGS报告'))} · 第 {version} 版 · {status_label}"]
    for index, case in enumerate(session["cases"], 1):
        name = html.escape(str(case.get("Chinese") or case.get("Latin") or f"病例 {index}"))
        sections.append(
            f"#### {index}. {name}｜既有判定：**{html.escape(str(case.get('label', '未提供')))}**\n\n"
            f"**置信度：** {html.escape(str(case.get('confidence', '未提供')))}\n\n"
            f"**解释：** {html.escape(str(case.get('explanation', '未提供')))}\n\n"
            f"**临床匹配：** {html.escape(str(case.get('clinical_match', '未提供')))}\n\n"
            f"**证据局限：** {html.escape(str(case.get('limitations', [])))}\n\n"
            f"**建议复核：** {html.escape(str(case.get('review_items', [])))}"
        )
        if case.get("doctor_feedback"):
            sections.append("**已纳入的医生意见：**\n\n" + "\n".join(f"- {html.escape(str(item))}" for item in case["doctor_feedback"]))
    versions = session.get("versions", [])
    if versions:
        sections.append("### 版本历史\n\n" + "\n".join(
            f"- 第 {item.get('version')} 版 · {html.escape(str(item.get('summary', '报告快照')))} · {item.get('status', 'in_review')}"
            for item in versions
        ))
    return "\n\n".join(sections)


def report_scope_choices(report_state):
    choices = [("全部病例", "全部病例")]
    session = active_report_session(report_state)
    if session:
        for index, case in enumerate(session.get("cases", []), 1):
            case_id = str(case.get("session_case_id") or case.get("UUID") or case.get("NameID") or f"case-{index}")
            name = case.get("Chinese") or case.get("Latin") or f"病例 {index}"
            choices.append((f"{name}（{case_id}）", case_id))
    return gr.update(choices=choices, value="全部病例")


def report_session_choices(report_state):
    sessions = (report_state or {}).get("sessions", [])
    choices = [
        (
            f"{item.get('title') or 'mNGS报告'} · {str(item.get('created_at') or '')[:16].replace('T', ' ')} · {item.get('session_id', '')[:6]}",
            item.get("session_id"),
        )
        for item in sessions
    ]
    return gr.update(choices=choices, value=(report_state or {}).get("active_session_id"))


def review_report(feedback, feedback_type, target_section, case_scope, report_state):
    session = active_report_session(report_state)
    if not session or not session.get("cases"):
        raise ValueError("请先完成一次 mNGS 判别")
    if not str(feedback or "").strip():
        raise ValueError("请填写医生审阅意见")
    if session.get("status") == "confirmed":
        raise ValueError("当前报告已确认。如需继续修改，请先重新打开审阅状态。")
    cases = session["cases"]
    selected = [case for case in cases if case_scope in ("", "全部病例", case.get("session_case_id"))]
    if not selected:
        raise ValueError("没有找到要审阅的病例，请重新选择病例范围")
    revisions = revise_judgements_with_doctor_feedback(
        selected,
        feedback,
        feedback_type=feedback_type,
        target_section=target_section,
    )
    by_id = {case.get("session_case_id"): revised for case, revised in zip(selected, revisions)}
    revised_cases = [by_id.get(case.get("session_case_id"), case) for case in cases]
    target_case_ids = [str(case.get("session_case_id") or "") for case in selected]
    revised_session = append_report_revision(
        session,
        revised_cases,
        feedback=feedback,
        feedback_type=feedback_type,
        target_section=target_section,
        target_case_ids=target_case_ids,
    )
    state = update_report_session(report_state, revised_session)
    return state, render_report_state(state), export_review_report(state), ""


def change_report_session(session_id, report_state):
    if not report_state or not report_state.get("sessions"):
        raise ValueError("当前会话还没有可切换的报告")
    state = select_report_session(report_state, session_id)
    return state, render_report_state(state), export_review_report(state), report_scope_choices(state), ""


def confirm_current_report(report_state):
    session = active_report_session(report_state)
    if not session or not session.get("cases"):
        raise ValueError("请先完成一次 mNGS 判别")
    state = update_report_session(report_state, confirm_report_session(session))
    return state, render_report_state(state), export_review_report(state)


def reopen_report_review(report_state):
    session = active_report_session(report_state)
    if not session or not session.get("cases"):
        raise ValueError("当前没有可重新打开的报告")
    state = update_report_session(report_state, reopen_report_session(session))
    return state, render_report_state(state)


# 核心函数
def grodio_view(chatbot, chat_input, previous_report_state=None):
    empty_input = {"text": "", "files": []}
    report_state = previous_report_state or create_report_workspace()

    # 用户消息立即显示
    user_message = chat_input["text"]
    bot_response = "loading..."
    chatbot = chatbot or []
    chatbot.append([user_message, bot_response])
    yield chatbot, empty_input, None, report_state, render_report_state(report_state), report_scope_choices(report_state), report_session_choices(report_state), gr.skip()

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
            yield chatbot, empty_input, None, report_state, render_report_state(report_state), report_scope_choices(report_state), report_session_choices(report_state), gr.skip()
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
            if isinstance(chunk, str):
                bot_response += chunk
            else:
                bot_response += chunk.choices[0].delta.content or ""
            chatbot[-1][1] = bot_response
            yield chatbot, empty_input, None, report_state, render_report_state(report_state), report_scope_choices(report_state), report_session_choices(report_state), gr.skip()

    if answer[1] == userPurposeType.MNGSJudge and bot_response.strip():
        try:
            payload = _parse_json_response(bot_response)
            judged_cases = _judgement_items(payload)
            parsed_cases = parse_mngs_cases(user_message)
            if len(judged_cases) == len(parsed_cases):
                seen_case_ids = set()
                for index, (judgement, case) in enumerate(zip(judged_cases, parsed_cases), 1):
                    case_session_id = str(case.case_id or case.name_id or f"case-{index}")
                    if case_session_id in seen_case_ids:
                        case_session_id = f"{case_session_id}-{index}"
                    seen_case_ids.add(case_session_id)
                    judgement["session_case_id"] = case_session_id
                    judgement["raw_case"] = {
                        "case_id": case.case_id,
                        "pathogen": case.species_chinese or case.species_latin,
                        "sample_type": case.sample_type,
                        "age": case.age,
                        "sex": case.sex,
                        "phenotype": case.phenotype,
                        "diagnosis": case.diagnosis,
                        "immune_status": case.immune_status,
                        "reads": case.reads,
                        "genus_reads": case.genus_reads,
                        "coverage": case.coverage,
                        "abundance": case.abundance,
                        "genus_abundance": case.genus_abundance,
                        "species_rank": case.species_rank,
                        "genus_rank": case.genus_rank,
                        "pathogenicity_text": case.pathogenicity_text,
                        "existing_label": case.existing_label,
                    }
                report_state = add_report_session(report_state, create_report_session(judged_cases))
                generated_pdf = export_review_report(report_state)
            else:
                generated_pdf = export_current_chat_pdf(chatbot)
        except Exception as exc:
            generated_pdf = None
            print(f"自动生成 mNGS PDF 失败: {exc}")
        # Keep generated reports together while Gradio serves them for download.
        yield chatbot, empty_input, generated_pdf, report_state, render_report_state(report_state), report_scope_choices(report_state), report_session_choices(report_state), ""

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
        yield chatbot, empty_input, None, report_state, render_report_state(report_state), report_scope_choices(report_state), report_session_choices(report_state), gr.skip()

    # 处理图片描述
    if answer[1] == userPurposeType.ImageDescribe:
        for i in range(0, len(answer[0]), 1):
            bot_response += answer[0][i : i + 1]  # 累加当前chunk到combined_message
            chatbot[-1][1] = bot_response  # 更新chatbot对话中的最后一条消息
            yield chatbot, empty_input, None, report_state, render_report_state(report_state), report_scope_choices(report_state), report_session_choices(report_state), gr.skip()

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
            yield chatbot, empty_input, None, report_state, render_report_state(report_state), report_scope_choices(report_state), report_session_choices(report_state), gr.skip()
        for chunk in answer[0]:
            bot_response = bot_response + (chunk.choices[0].delta.content or "")
            chatbot[-1][1] = bot_response
            yield chatbot, empty_input, None, report_state, render_report_state(report_state), report_scope_choices(report_state), report_session_choices(report_state), gr.skip()


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
        report_session_picker = gr.Dropdown(
            choices=[],
            value=None,
            label="病例 / 报告会话",
            info="连续分析的不同病例会保存在当前浏览器会话中，可在此切换。",
        )
        report_preview = gr.Markdown("尚无可审阅的 mNGS 报告。")
        review_scope = gr.Dropdown(
            choices=[("全部病例", "全部病例")],
            value="全部病例",
            label="审阅范围",
        )
        with gr.Row():
            feedback_type = gr.Dropdown(
                choices=["补充事实", "纠正事实", "质疑证据", "解释问题", "要求改写"],
                value="解释问题",
                label="意见类型",
            )
            target_section = gr.Dropdown(
                choices=["整体", "病例摘要", "mNGS 检出证据", "临床匹配", "知识库证据", "证据局限", "建议复核项"],
                value="整体",
                label="关联章节",
            )
        doctor_feedback = gr.Textbox(
            label="医生审阅意见",
            placeholder="写明需要补充或纠正的事实、对证据的疑问，或希望报告进一步解释的内容。补充病例事实时请注明来源。",
            lines=5,
        )
        with gr.Row():
            revise_button = gr.Button("根据意见生成修订版", variant="primary")
            confirm_button = gr.Button("确认当前报告", variant="secondary")
            reopen_button = gr.Button("重新打开审阅", variant="secondary")

    chat_input.submit(
        fn=grodio_view,
        inputs=[chatbot, chat_input, report_state],
        outputs=[chatbot, chat_input, export_file, report_state, report_preview, review_scope, report_session_picker, doctor_feedback],
    )

    report_session_picker.input(
        fn=change_report_session,
        inputs=[report_session_picker, report_state],
        outputs=[report_state, report_preview, export_file, review_scope, doctor_feedback],
    )

    export_pdf.click(fn=export_review_report, inputs=[report_state], outputs=[export_file])
    revise_button.click(
        fn=review_report,
        inputs=[doctor_feedback, feedback_type, target_section, review_scope, report_state],
        outputs=[report_state, report_preview, export_file, doctor_feedback],
    )
    confirm_button.click(
        fn=confirm_current_report,
        inputs=[report_state],
        outputs=[report_state, report_preview, export_file],
    )
    reopen_button.click(
        fn=reopen_report_review,
        inputs=[report_state],
        outputs=[report_state, report_preview],
    )


# 启动应用
def start_gradio():
    server_port = int(os.getenv("GRADIO_SERVER_PORT", "10032"))
    server_name = os.getenv("GRADIO_SERVER_NAME", "127.0.0.1")
    demo.launch(server_name=server_name, server_port=server_port, share=False)


if __name__ == "__main__":
    start_gradio()
