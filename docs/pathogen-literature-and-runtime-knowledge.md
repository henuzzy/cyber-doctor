# 病原文献抽取与运行时知识检索

项目把病原原始文献和生成的知识档案作为运行时数据管理；仓库只保存处理代码、格式约定、测试和本文档。

## 通用逐篇文献抽取

`scripts/data/extract_first20_structured.py` 保留历史文件名以兼容旧命令，但默认扫描指定目录下的**全部** `.md`/`.markdown` 文件。每个来源 Markdown 单独调用 OpenAI 兼容的 Qwen Chat Completions 服务，并按原相对路径输出一份 JSON。它兼容文章旁的 `metadata.json`、Markdown 头部字段及常见的中英文病原元数据。

在项目根目录设置运行配置：

```bash
export LITERATURE_ROOT=/data/病原文献
export STRUCTURED_ARTICLE_OUTPUT_ROOT=/data/structured-articles
export LLM_ENDPOINT=http://127.0.0.1:8000/v1/chat/completions
export LLM_API_KEY=your-key
export MODEL_NAME=qwen3.8-27b
```

先查看将要处理的文件，不调用模型：

```bash
python scripts/data/extract_first20_structured.py --literature-root "$LITERATURE_ROOT" --dry-run
```

随后抽取全部文章：

```bash
python scripts/data/extract_first20_structured.py \
  --literature-root "$LITERATURE_ROOT" \
  --output-dir "$STRUCTURED_ARTICLE_OUTPUT_ROOT"
```

脚本默认不限制文章数。`--limit N` 只用于显式的小批量试跑。已有结果在原文 SHA-256 与 schema 版本一致时会跳过；每完成一篇都会原子更新 `checkpoint.json`。失败文章会记录在 checkpoint 和 `summary.json`，其他文章继续处理。模型提供的每条原文摘录必须逐字出现在输入文章中，否则该文章失败，不保存不受原文支持的引用。

文章 JSON 保存来源类型、标题、相对文件名、PMID/PMCID/DOI/URL、文章类型、病原名称与 NameID、证据维度、证据范围、原文摘录及原文 SHA-256。文章及每条事实的 `scope` 使用 `species_direct`、`genus_or_group`、`background_group` 或 `unknown`，分别表示目标种、同属或相关群、更大病原群背景、未确认。它记录模型对证据范围的整理，不能替代医生审核。

名称和文献元数据来自文章同名 JSON、邻近 `metadata.json` 或 Markdown 头部，无法从资料确定的字段留空。每条事实必须包含可定位的非空原文摘录及字符偏移。默认读取每篇前 120000 个字符，截断情况写入 `extractor.source_truncated`；可用 `--max-source-chars` 调大。默认每篇失败后重试一次，`--retries` 可调整。续跑会检查原文、元数据、提示词版本、模型及输入长度配置；修改后会重新抽取。输出目录必须在原始文献目录之外。

## 运行时目录配置

将逐篇 JSON 和五维档案放在项目目录之外，分别配置：

```env
STRUCTURED_KNOWLEDGE_ROOT=/data/structured-articles
FIVE_DIMENSION_KNOWLEDGE_ROOT=/data/pathogen_knowledge_all_simple.json
```

`STRUCTURED_KNOWLEDGE_ROOT` 可以是逐篇 JSON 的目录，也可以是单个 JSON 文件。`FIVE_DIMENSION_KNOWLEDGE_ROOT` 可以是约定格式的五维 JSON 文件，或含有五维 JSON 的目录。检索器支持中文种名、拉丁种名（带/不带下划线）、中文/拉丁属名、NameID 和档案中列出的别名。五维档案每一维均展示概述和来源；种级档案及属级匹配会注明匹配范围。

也可直接使用命令行入口：

```bash
python -m cyber_doctor.model.rag.five_dimension_retriever 'Mycobacterium_paragordonae' \
  --knowledge-root "$FIVE_DIMENSION_KNOWLEDGE_ROOT"
```

运行时校验五维字段，兼容现有档案的单个来源 URL 和空来源，以及规范的来源列表。档案名称命中并不证明其中每条摘要都是目标物种直接证据；缺少单独范围时保留“未确认”，并结合概述限定语和逐篇文献核查。

在聊天中可以直接输入病原名称，或询问“查询该病原五维档案”，系统会走五维档案查询入口。mNGS 病例判断会同时检索五维摘要和逐篇文献 JSON：摘要用于快速概览，文章 JSON 提供具体来源和原文摘录；提示词明确区分目标物种直接证据与同属/相关病原群证据。任何未配置或未检索到的数据都不会伪造来源。

## Git 中的数据边界

不要将原始 `病原文献`、逐篇结构化 JSON、五维知识 JSON、检查点、缓存、患者数据或运行输出提交到仓库。将这些数据保存在部署数据目录，并通过命令行参数或环境变量指定路径。抽取代码本身已由 `.gitignore` 显式放行。
