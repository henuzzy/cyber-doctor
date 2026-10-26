# Cyber Doctor：mNGS 报告解读与审阅工作台

Cyber Doctor 是一个面向 mNGS 病原检出结果的研究型报告解读系统。它把病例资料、mNGS 检测指标和病原知识库证据交给大模型，生成可追溯的病原有害性解释，并提供医生审阅、报告修订、确认和 PDF 导出流程。

系统用于辅助临床复核和科研评估，不替代培养、靶向 PCR、临床诊断或治疗决定。

## 当前项目范围

当前交付的核心流程是：

1. 接收一份包含一个或多个病原的 mNGS 病例资料。
2. 解析病原名称、NameID、样本部位、临床表型、患者信息、免疫状态和 reads、覆盖率、丰度、排名等检测指标。
3. 按种名、属名、中文名或 NameID 匹配本地结构化病原文献。
4. 将病例事实和文献证据提交给 Qwen，生成有害/无害判定解释、证据引用、临床匹配说明、局限性和复核项。
5. 在工作台中由医生提出意见，生成新的报告版本，确认或重新打开审阅。
6. 导出当前报告版本的可解释性 PDF。

端到端流程如下：

```text
病例文本或 Markdown/TXT/PDF/Word
        ↓
病例与 mNGS 字段解析
        ↓
病原名称匹配结构化 PubMed/UpToDate 文献
        ↓
Qwen 生成结构化 mNGS 解读
        ↓
证据阅读与医生审阅
        ↓
报告版本确认与 PDF 导出
```

## 工作台功能

### 病例输入

界面支持粘贴病例资料，也支持上传 Markdown、TXT、PDF 和 Word 文件。一份输入可以包含多个病原，系统会按病例逐例生成解读。可识别的字段包括：

- 病原名称：`NameID`、`Latin`、`Chinese`、种名、属名和别名
- 判定信息：`Label`、`OriginalFlag` 或已有的有害/无害结论
- mNGS 指标：种/属 reads、覆盖率、丰度、种/属排名
- 临床信息：样本或检测组织、年龄、性别、诊断、临床表型和免疫状态

如果输入已经包含既有判定，系统的任务是解释该判定，不会用背景文献自动推翻它。

### 证据驱动的 mNGS 解读

默认从 `STRUCTURED_KNOWLEDGE_ROOT` 指定的结构化病原文献目录读取证据。检索优先使用目标物种；没有目标物种文献时，可以回退到属或相关病原群，但结果会标注证据范围，避免把属级信息写成目标物种事实。

模型输出的核心字段为：

```json
{
  "label": "有害或无害",
  "confidence": "高/中/低",
  "patient_summary": "患者和临床表现摘要",
  "mngs_evidence": ["检测组织、reads、覆盖率、丰度和排名"],
  "clinical_match": "病例表现与文献证据的匹配说明",
  "explanation": "判定或既有判定的解释",
  "evidence": [
    {
      "source": "真实文献标题、来源类型和文件",
      "support_type": "目标物种直接匹配/同属或相关病原群参考",
      "summary": "该证据如何影响当前解读"
    }
  ],
  "limitations": ["证据缺口和需要补充验证的内容"],
  "review_items": ["建议医生复核的事项"]
}
```

模型只能引用检索结果中真实出现的来源。病例自身的 reads、覆盖率和样本信息可以用于解释，但不能伪装成文献来源。

### 医生审阅与报告版本

每次 mNGS 分析会创建一个浏览器会话内的报告会话。医生可以：

- 选择全部病例或指定病例审阅；
- 选择补充事实、纠正事实、质疑证据、解释问题或要求改写；
- 将意见关联到病例摘要、mNGS 检出证据、临床匹配、知识库证据、证据局限或复核项；
- 根据意见生成完整的新报告版本；
- 确认当前版本，或重新打开已确认的报告继续审阅。

报告会话和版本快照保存在当前浏览器会话状态中，服务重启或会话过期后不会恢复。当前版本没有身份认证、数据库持久化和跨设备审计能力。

### PDF 报告

报告确认或审阅过程中均可导出 PDF。PDF 使用当前已有的结构化结果和审阅版本，不会因为导出再次调用模型，也不会修改原始文献或病例输入。实现位于：

- `src/cyber_doctor/reporting/mngs_report.py`
- `src/cyber_doctor/reporting/explainability_pdf.py`

生成的文件默认写入项目根目录的 `outputs/`，该目录不提交到 Git。

## 病原知识库与离线数据准备

病原知识库不是运行时临时联网搜索得到的。运行时读取已经整理好的结构化 JSON；文献采集、原文保存和五维知识整理属于离线数据准备。

病原记录以一个 JSON 文件或病原目录中的多个 JSON 文件保存，包含病原名称与分类、物种描述、致病性、毒力因子、耐药性、治疗与预后等知识，并保留每条信息的来源。原始 PubMed/PMC 文献与结构化结果分开保存，便于追溯。

当前数据准备脚本包括：

```text
scripts/data/collect_pubmed_fulltext.py
    PubMed/PMC 全文采集，保留 source.xml、article.md 和元数据

scripts/data/extract_pathogen_descriptions_qwen.py
    从 positive.jsonl 的 Description 提取有来源支持的病原知识

scripts/data/build_pathogen_knowledge_all.py
    依据已有结果和文献构建全量病原知识文件

scripts/ops/uptodate_browser_search.py
    在人工登录的 UpToDate 浏览器会话中采集文章 Markdown
```

这些脚本的输出、原始文献、模型检查点和患者数据应放在 `outputs/` 或 DGX 的独立数据目录，不要提交到 Git。

## 配置

复制配置模板：

```bash
cp .env.example .env
```

Windows PowerShell 可以使用：

```powershell
Copy-Item .env.example .env
```

DGX 上使用 Qwen OpenAI 兼容服务时，至少配置：

```env
LLM_BASE_URL=http://127.0.0.1:8000/v1
LLM_API_KEY=服务要求的值
MODEL_NAME=qwen3.8-27b
LLM_PROVIDER=qwen
QWEN_ENABLE_THINKING=0
STRUCTURED_KNOWLEDGE_ROOT=/home/zhangyue/outputs/病原结构化信息
GRADIO_SERVER_PORT=10032
```

可选的知识源设置：

```env
# 默认值为 structured
MNGS_KNOWLEDGE_SOURCE=structured

# 只有在明确准备 GraphML 数据时才使用 graphml
MNGS_GRAPHML_PATH=/path/to/pathogen.graphml
```

`structured` 是当前默认且推荐的 mNGS 文献证据路径。`graphml` 是兼容入口，不是当前病原五维知识库的主要存储方式。

## 启动

项目要求 Python 3.11 或更高版本：

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python app.py
```

Windows PowerShell：

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python app.py
```

默认访问地址为：

```text
http://127.0.0.1:10032
```

如果 Qwen 和 Gradio 都运行在 DGX，可通过 SSH 隧道访问：

```powershell
ssh -L 10032:127.0.0.1:10032 zhangyue@10.8.0.22
```

然后在本机浏览器打开 `http://127.0.0.1:10032`。

## 验证

运行全部测试：

```bash
python -m unittest discover -s tests -v
```

重点测试包括：

- mNGS 病例字段解析与多病例输入；
- mNGS 报告工作台和报告会话；
- 医生意见、报告版本、确认和重新打开；
- PDF 报告渲染；
- Description 知识提取；
- PubMed/PMC 全文采集；
- 全量病原知识库格式和证据范围校验。

## 项目结构

```text
cyber-doctor/
├── app.py                         # 启动 Gradio mNGS 工作台
├── src/cyber_doctor/
│   ├── app.py                     # UI、输入处理、报告工作流
│   ├── mngs/                      # mNGS 病例解析、证据检索、判定提示词
│   ├── model/rag/                 # 结构化病原文献和其他检索适配器
│   ├── reporting/                 # mNGS 结果适配与 PDF 报告
│   ├── ui/                        # 报告工作台 HTML 渲染
│   ├── resources/                 # 运行时头像和工作台样式
│   ├── client/                    # OpenAI 兼容模型客户端
│   └── qa/                        # 输入类型路由和兼容工具入口
├── scripts/data/                  # 病原知识和文献离线处理
├── scripts/diagnostics/           # mNGS 检索和输入诊断
├── scripts/ops/                   # UpToDate 等数据采集辅助工具
├── scripts/reports/               # 独立报告处理脚本
├── tests/                         # 回归测试
├── docs/                          # 开发、布局和数据流程说明
└── outputs/                       # 本地运行输出，Git 忽略
```

## 不属于当前交付范围的代码

仓库中仍保留一部分早期通用医疗助手的兼容代码，但它们不是当前 mNGS 报告解读项目的交付功能：

- 通用文本聊天；
- 图片生成和图片描述；
- 通用联网搜索问答；
- 通用知识图谱问答；
- 与 mNGS 报告无关的通用教材 RAG 入库和检索。

这些入口仍可在 `src/cyber_doctor/qa/`、`src/cyber_doctor/internet/`、`src/cyber_doctor/kg/` 和部分 `src/cyber_doctor/model/` 文件中看到，主要用于兼容旧代码和已有实验。后续若要收敛成纯 mNGS 产品，可以将这些路由、客户端和对应配置从应用入口中移除；本 README 不再把它们描述为项目功能。

## 当前限制

- 解读结果依赖病例输入质量和结构化文献覆盖，不能替代临床验证。
- 没有匹配到目标物种时，系统可能使用属级或相关病原群证据，并在结果中标注范围。
- 当前报告会话只保存在浏览器会话状态中，尚未接入用户身份、数据库和长期审计。
- 结构化知识库和原始文献不随源代码发布，需要单独配置 `STRUCTURED_KNOWLEDGE_ROOT`。
- 运行时默认不主动联网采集文献；文献采集和五维知识整理需要单独运行数据准备脚本。

