## 项目背景

医疗资源不平衡一直以来是社会关注的重点问题，它导致众多医疗不公平事件发生。在相对落后地区的人们想要获得优秀的医疗资源往往需要前往一线城市，这不仅费时费力费钱，而且极大的影响了他们的接受医疗救助的基本权利。当前多模态大语言模型不断发展，在许多领域都有了不错的应用。我们小组基于东南大学暑期实训课程，开发了一个医疗健康领域的多模态大模型，这个大模型的目标用户是所有对自己健康关心的人，帮助进行基本的疾病诊断，病历分析，专业知识答疑等功能。本项目狭义上可以作为一个多功能的健康小助手，帮助管理个人健康，提供基础的医疗建议；广义上可以配置在任何领域，通过微调的大模型和RAG技术让大模型掌握目标领域的专业知识，成为任意专业的专家。

## 界面展示



## 功能特色

- **多功能多模态整合，借助AI智能体判断任务的种类，将多个模型整合工作，解决复杂问题。**

## 功能介绍

| 功能         | 功能介绍                                                                                                 |
| ------------ | -------------------------------------------------------------------------------------------------------- |
| 图片识别     | 借助多模态大模型的能力，识别图片中的图像和文字。可用于识别病历，识别药品说明书等                         |
| 图片生成     | 借助多模态大模型的能力，生成图片                                                                         |
| 多轮对话     | 具有记忆功能，对话界面的所有内容会作为历史记录一同输入大模型                                             |
| 检索增强对话 | 多模态输入框不只能输入文本，还能上传文件。大模型会根据文件内容调整输出                                   |
| 知识图谱增强 | 支持配置相关领域的neo4j知识图谱，用专业知识改善大模型输出                                                |
| 知识库增强   | 支持利用多种格式的文件作为专属知识库，大模型会结合知识库中的文件进行输出                                 |
| 联网检索增强 | 通过自动化爬虫检索网络上的相关信息，利用网络增强大模型知识的时效性                                       |
| mNGS病原判别 | 面向单个mNGS检出病原，先检索医学知识库，再结合患者信息、检出可信度、样本部位和免疫状态判断“有害/无害” |

## mNGS病原判别流程

当前项目已经适配 mNGS 单病原有害性判别任务。对于包含 `mNGS`、`病原基本信息`、`患者信息`、`Label`、`Latin`、`Chinese` 等字段的输入，系统会自动进入 mNGS 判别链路，不再需要用户显式输入“根据知识库”。

处理流程如下：

```text
病例/病原输入
  -> 识别为 mNGS 判别任务
  -> 解析病原名、属名、样本类型、临床表型、诊断、免疫状态、reads、coverage、丰度、排名
  -> 生成多条医学检索 query
  -> 先检索 Milvus 知识库
  -> 将检索证据 + 原始病例信息一起送给 LLM
  -> 输出 label、解释、证据引用和局限性
```

LLM 输出格式要求为严格 JSON：

```json
{
  "label": "有害或无害",
  "explanation": "判断原因",
  "evidence": [
    {
      "source": "证据编号或来源名称",
      "support_type": "支持有害/支持无害/背景知识/同属类比/证据不足",
      "summary": "该证据如何影响判断"
    }
  ],
  "limitations": "缺少哪些关键证据或需要临床补充验证的信息"
}
```

如果后续做评测，只需要读取 `label` 字段即可；如果用于医生辅助解读，可以展示完整解释和证据链。

建议知识库按来源类型组织为三个 collection：

```text
pathogen_knowledge       # 病原基础信息、别名、分类、常见感染部位、致病性、定植/背景可能性
medical_textbooks        # 医学书籍、教材、指南、专家共识
medical_case_reports     # PDF案例报告转换后的结构化病例证据
```

当前运行时代码默认使用 `medical_textbooks` collection，并通过 `model/RAG/medical_retriever.py` 执行 BGE-M3 dense/sparse 混合检索。旧的 `cyber_doctor_knowledge`、FAISS、ModelScope/LangChain 知识库检索链路已经移除。

## 技术栈

- **Python**
- **PyTorch**
- **Transformers**
- **Gradio**：简易的UI和交互生成工具
- **Milvus**：离线持久化医学知识库向量和稀疏向量
- **BGE-M3 / FlagEmbedding**：生成 dense/sparse 混合检索向量
- **RAG**：结合检索与生成的技术，用于增强生成式模型的回答质量
- **Knowledge Graph (Neo4j)**：Neo4j图数据库的配置和Cypher语句操作
- **OpenAi & zhipuai**：相关大模型sdk调用方法

Option：

- **Ollama**：本地大模型api封装

## 如何启动项目

1. **从Github上拉取项目**

   ```bash
   git clone https://github.com/henuzzy/cyber-doctor.git
   或者
   git clone git@github.com:henuzzy/cyber-doctor.git
   ```
   
2. **配置大模型API**

   复制 `.env.example`为 `.env`，填写 `.env`内相关API配置。

   API目前支持：

   1. 所有支持OpenAI SDK接口的API，包括
      - [智谱AI](https://open.bigmodel.cn/)
      - [豆包大模型](https://www.volcengine.com/experience/ark?utm_term=202502dsinvite&ac=DSASUQY5&rc=II9TBGSX)
      - [硅基流动集成平台](https://cloud.siliconflow.cn/i/VWOdVvvM)
      - [deepseek](https://platform.deepseek.com/)
      - [千问Qwen](https://bailian.console.aliyun.com/)
   2. Ollama封装的本地API


3. **填写 `.env` 和 `config/config-web.yaml` 配置**

   RAG 运行时主要读取 `.env` 中的 Milvus 和 BGE-M3 配置：

   ```env
   MILVUS_URI=http://127.0.0.1:19530
   MEDICAL_TEXTBOOK_COLLECTION=medical_textbooks
   BGE_M3_MODEL=D:\models\huggingface\hub\models--BAAI--bge-m3
   BGE_M3_DEVICE=auto
   BGE_M3_USE_FP16=1
   MEDICAL_RETRIEVE_TOP_K=12
   MEDICAL_RETRIEVE_CANDIDATE_K=40
   MEDICAL_DENSE_WEIGHT=0.7
   MEDICAL_SPARSE_WEIGHT=0.3
   ```

   `config/config-web.yaml` 目前主要保留知识图谱等非 RAG 配置；医学知识库检索不再使用旧的 `cyber_doctor_knowledge` 配置。

4. **创建python环境（python>=3.10，建议为3.10）**

   建议使用conda管理环境

   ```bash
   conda create --name myenv python=3.10
   conda activate myenv
   ```

   安装依赖库

   ```bash
   pip install -r requirements.txt
   ```
   
5. **启动项目**

   ```bash
   python app.py
   ```

   启动后访问 http://localhost:7860

## Milvus知识库与离线入库

本项目的知识库 RAG 已经切换为 Milvus。文档不应该在用户提问时临时嵌入，而应该提前离线入库。

1. **启动 Milvus**

   本地或服务器上启动 Milvus Standalone，确保端口 `19530` 可访问。DGX Spark/aarch64 环境如果 Docker Hub 拉取慢，可以使用镜像代理。

2. **准备清洗后的医学 chunk 目录**

   当前推荐先清洗 Markdown 医学书籍，再输出 chunk JSONL。示例目录为：

   ```text
   E:\华大医疗agent清洗版chunks
   ```

   原始 PDF/Markdown、清洗后资料和 chunk 文件都不要提交到 Git。当前 `.gitignore` 已忽略 `konwledge-base/`、`data/`、`.env`、`*.chunks.jsonl` 等本地数据。

3. **离线嵌入医学 chunk**

   ```bash
   python scripts/ingest_medical_chunks_milvus.py --input-dir "E:\华大医疗agent清洗版chunks" --collection medical_textbooks --uri http://127.0.0.1:19530 --model "D:\models\huggingface\hub\models--BAAI--bge-m3" --device cuda --use-fp16
   ```

   如需删除旧 collection 并完全重建：

   ```bash
   python scripts/ingest_medical_chunks_milvus.py --input-dir "E:\华大医疗agent清洗版chunks" --collection medical_textbooks --uri http://127.0.0.1:19530 --model "D:\models\huggingface\hub\models--BAAI--bge-m3" --device cuda --use-fp16 --recreate
   ```

   可以加一个检索探针：

   ```bash
   python scripts/ingest_medical_chunks_milvus.py --input-dir "E:\华大医疗agent清洗版chunks" --collection medical_textbooks --probe-query "房颤怎么办" --probe-top-k 12 --candidate-k 40
   ```

   运行时只使用 `model/RAG/medical_retriever.py` 这一套 BGE-M3 混合检索器，不再有旧的 FAISS、ModelScope 或 LangChain 知识库检索入口。

4. **当前推荐的文档流程**

   ```text
   原始 Markdown/PDF
     -> Markdown 清洗
     -> 医学 chunk JSONL
     -> BGE-M3 生成 dense/sparse 向量
     -> 写入 Milvus medical_textbooks collection
   ```

## 医学文档清洗建议

医学书籍和案例报告建议采用不同清洗策略。

案例报告通常较短，适合抽取为病例证据块：

```text
文献基础信息
病原背景
患者基础情况
样本和检测证据
诊断证据
治疗和转归
作者讨论
参考文献
```

医学书籍通常较大，建议先清洗再按章节切分：

```text
删除：封面、版权页、CIP、编委、主编简介、二维码说明、目录、无文字意义图片占位
保留：正文、章节标题、小节标题、列表、表格、药名、剂量、单位、病原名、疾病名、检查指标
```

清洗规则建议：

```text
1. 删除控制字符，例如 \x08、\x07
2. 删除乱码占位符，例如 �
3. 删除 Markdown 图片占位符，例如 ![](images/xxx.jpg)
4. 删除空标题行，例如单独一行 `#`
5. 保留独立图注行和正文中的图号引用，避免误删医学正文
6. 保留表格数据，表格尽量作为完整 chunk 入库
```

正文里的医学内容不能因为包含图号而被删除。当前策略是只删除 `![](images/xxx.jpg)` 这类图片占位符，图注和“图3-4-3”这类文本引用默认保留。

后续如果文档格式问题较多，可以使用本地 7B/8B Instruct 模型辅助处理低质量片段，但建议规则清洗为主，模型只负责标题层级、段落合并、表格整理和 JSON 结构化，不应让模型改写医学事实。

项目提供了一个规则清洗脚本，可用于处理 MinerU 转出的医学书籍 Markdown：

```bash
python scripts/clean_medical_md_book.py "E:\华大医疗agent资料\30《口腔科学》第10版.md" -o "E:\华大医疗agent清洗版资料\30《口腔科学》第10版.md"
```

也可以批量清洗整个目录：

```bash
python scripts/clean_medical_md_book.py --input-dir "E:\华大医疗agent资料" --output-dir "E:\华大医疗agent清洗版资料"
```

默认只生成清洗后的 `.md` 文件，不生成 `*.sections.jsonl`。后续 chunk 入库时会直接从 clean markdown 的标题层级中重新解析章节路径。

如果调试时确实需要导出标题结构，可以额外指定：

```bash
python scripts/clean_medical_md_book.py "E:\华大医疗agent资料\30《口腔科学》第10版.md" -o "E:\华大医疗agent清洗版资料\30《口腔科学》第10版.md" --records-output "E:\华大医疗agent清洗版资料\30《口腔科学》第10版.sections.jsonl"
```

### 医学书籍 Markdown 切分

清洗后的书籍不要直接整本入库，先切成医疗 RAG 专用 chunk：

```bash
python scripts/chunk_medical_md.py --input-dir "E:\华大医疗agent清洗版资料" --output-dir "E:\华大医疗agent清洗版chunks" --max-chars 1200 --overlap-chars 150 --doc-type textbook
```

脚本会输出 `*.chunks.jsonl`，每条记录包含：

```text
chunk_id        稳定 chunk id
answer_text     原文片段，用于回答和引用
search_text     加了书名、文档类型、章节路径的检索文本
metadata        书名、章节路径、行号、block_type 等来源信息
```

切分策略不是简单固定长度切割：

```text
1. 先解析 clean markdown 标题层级，保存章节路径
2. 表格作为独立块保留
3. 说明型编号条目尽量拆成单独 chunk，例如“1. 额肌...”“2. 眼轮匝肌...”
4. 短标签列表保持整体，例如图例编号、神经分支编号
5. 普通段落按字符数切分，默认 1200 字符，150 字符 overlap
6. 长文本优先在句号、分号、冒号等边界截断
```

### 医学 chunk 入 Milvus

生成 chunk 后，用 BGE-M3 写入 Milvus。建议书籍、案例报告、病原信息分别使用不同 collection：

```bash
python scripts/ingest_medical_chunks_milvus.py --input-dir "E:\华大医疗agent清洗版chunks" --collection medical_textbooks --uri http://127.0.0.1:19530 --model "D:\models\huggingface\hub\models--BAAI--bge-m3" --device cuda --use-fp16
```

第一次创建 collection 或需要完全重建时：

```bash
python scripts/ingest_medical_chunks_milvus.py --input-dir "E:\华大医疗agent清洗版chunks" --collection medical_textbooks --uri http://127.0.0.1:19530 --model "D:\models\huggingface\hub\models--BAAI--bge-m3" --device cuda --use-fp16 --recreate
```

可以加一个检索探针：

```bash
python scripts/ingest_medical_chunks_milvus.py --input-dir "E:\华大医疗agent清洗版chunks" --collection medical_textbooks --probe-query "房颤怎么办" --probe-top-k 12 --candidate-k 40
```

医学检索探针默认使用 BGE-M3 混合召回：

```text
dense 召回 candidate_k 条，默认 40
sparse 召回 candidate_k 条，默认 40
按权重融合排序，默认 dense_weight=0.7、sparse_weight=0.3
最终返回 top_k 条，默认 12
```

如果传入的是 HuggingFace cache 根目录，例如 `D:\models\huggingface\hub\models--BAAI--bge-m3`，脚本会自动解析到 `snapshots/<hash>` 下的真实模型目录。GPU embedding 需要 CUDA 版 PyTorch；如果当前环境是 CPU 版 PyTorch，`--device cuda` 会直接报错，避免悄悄退回 CPU 慢跑。

Option：

1. **下载Neo4j图数据库（使用知识图谱检索增强功能的必要条件）**

2. **配置一个专业领域的图数据库**

   推荐开源知识图谱平台：[OpenKG](http://openkg.cn/datasets-type/)

   如果想配置医疗健康领域的数据库，推荐下载如下开源知识图谱

   [面向家庭常见疾病的知识图谱](http://data.openkg.cn/dataset/medicalgraph)（本项目使用了该图谱，使用该图谱可以不更改config/config-web.yaml的相关配置文件）

   1. 下载到本地后，改.dump文件名为你要导入的数据库名称（eg：neo4j.dump）
   2. 关闭neo4j服务

      ```
      Windows: neo4j stop
      Linux: sudo neo4j stop
      ```
   3. 运行导入指令

      ```
      neo4j-admin database load <database-name> --from-path=/path/to/dump-folder/ --overwrite-destination=true
      ```

      `--from-path`：存放对应"database-name".dump文件的文件夹路径

      `--overwrite-destination`：**注意会覆盖你原先数据库中的数据**
   4. 若运行上面的命令后输出

      ```
      The loaded database 'neo4j' is not on a supported version (current format: AF4.3.0 introduced in 4.3.0). Use the 'neo4j-admin database migrate' command
      ```

      还需要运行如下命令

      ```
      neo4j-admin database migrate 
      ```
   5. 启动neo4j服务

      ```
      Windows: neo4j start
      Linux: sudo neo4j start
      ```

## 项目结构

```
cyber-doctor/
├── .env                            # 环境配置文件，存储API密钥、模型配置等敏感信息
├── .env.example                    # 环境配置文件示例，展示需要配置的环境变量
├── .gitignore                      # Git版本控制忽略文件配置
├── LICENSE                         # 项目许可证文件
├── README.md                       # 项目中文说明文档
├── README_en.md                    # 项目英文说明文档
├── __init__.py                     # Python包初始化文件
├── app.py                          # 项目启动文件，构建Gradio界面，处理多模态信息，可自定义ASR模型和界面
├── env.py                          # 封装读取.env文件的接口
├── requirements.txt                # 项目依赖包列表
├── Internet/                       # 联网搜索相关功能模块
│   ├── __init__.py                   # 包初始化文件
│   ├── Internet_chain.py             # 联网搜索链，协调关键词提取、搜索爬取和检索过程
│   ├── Internet_prompt.py            # 大模型特征工程，提取搜索关键词
│   └── retrieve_Internet.py          # 调用model/Internet接口检索搜索结果
├── README/                         # 存放项目文档相关资源
│   ├── __init__.py                   # 包初始化文件
├── client/                         # 大模型客户端模块，作为用户与API的桥梁
│   ├── __init__.py                   # 包初始化文件
│   ├── LLMclientbase.py              # 大模型客户端基类定义
│   ├── LLMclientgeneric.py           # 封装调用大模型API接口进行对话生成的通用函数
│   ├── clientfactory.py              # 封装构建不同大模型客户端的工厂类
│   ├── ourAPI/                       # 自定义API接口实现
│   │   ├── __init__.py                 # 包初始化文件
│   │   └── client.py                   # 自定义API客户端实现
│   └── zhipuAPI/                     # 智谱AI API接口实现
│       ├── __init__.py                 # 包初始化文件
│       └── client.py                   # 智谱AI客户端实现
├── config/                         # 配置文件目录
│   ├── __init__.py                   # 包初始化文件
│   ├── config-web.yaml               # 不同(Web)开发环境下的应用配置文件
│   └── config.py                     # 配置加载和处理模块
├── kg/                             # 知识图谱相关功能模块
│   └── Graph.py                      # 知识图谱对象实现
├── model/                          # 检索功能使用到的模型相关功能模块，包括联网RAG、知识库RAG、知识图谱RAG
│   ├── __init__.py                   # 包初始化文件
│   ├── model_base.py                 # 模型基类定义
│   ├── KG/                           # 知识图谱RAG的匹配自动机实现
│   │   ├── __init__.py                 # 包初始化文件
│   │   ├── data_utils.py               # 知识图谱数据处理工具
│   │   ├── search_model.py             # 构建知识图谱RAG的匹配自动机
│   │   └── search_service.py           # 知识图谱RAG的匹配自动机接口
│   └── RAG/                          # 知识库RAG向量库实现
│       ├── __init__.py                 # 包初始化文件
│       ├── document.py                  # 轻量文档对象
│       ├── medical_retriever.py         # BGE-M3 + Milvus dense/sparse 混合检索器
│       └── retrieve_service.py         # 知识库RAG向量库接口
├── mngs/                           # mNGS单病原有害/无害判别链路
│   ├── __init__.py                   # 包初始化文件
│   └── rag_judge.py                  # mNGS输入解析、RAG检索、判别prompt构造
├── qa/                             # 问答系统核心模块
│   ├── __init__.py                   # 包初始化文件
│   ├── answer.py                     # 根据问题类型选择对应的工具函数生成回答
│   ├── function_tool.py              # 工具函数集合
│   ├── prompt_templates.py           # 提示词模板定义
│   ├── purpose_type.py               # 问题类型定义
│   └── question_parser.py            # 问题类型解析判断
├── rag/                            # 检索增强生成模块
│   ├── __init__.py                   # 包初始化文件
│   ├── rag_chain.py                  # RAG链式调用实现
│   └── retrieve/                     # 检索功能实现
│       ├── __init__.py                 # 包初始化文件
│       └── retrieve_document.py        # 文档检索实现
├── scripts/                        # 离线脚本
│   ├── clean_medical_md_book.py       # 清洗医学书籍 Markdown
│   ├── chunk_medical_md.py            # 将清洗后的 Markdown 切为医学 chunk
│   └── ingest_medical_chunks_milvus.py # 将医学 chunk 离线嵌入 Milvus
└── resource/                       # 资源文件目录，存放图片等静态资源
```

## 常见问题

1. **为什么 mNGS 判别没有检索到知识库？**

   先确认 Milvus 已启动，`.env` 中的 `MILVUS_URI`、`MEDICAL_TEXTBOOK_COLLECTION`、`BGE_M3_MODEL` 正确，并且已经运行过：

   ```bash
   python scripts/ingest_medical_chunks_milvus.py --input-dir "E:\华大医疗agent清洗版chunks" --collection medical_textbooks --uri http://127.0.0.1:19530 --model "D:\models\huggingface\hub\models--BAAI--bge-m3" --device cuda --use-fp16
   ```

2. **为什么运行时没有再从原始 PDF/Markdown 自动嵌入？**

   当前设计是离线入库、在线只检索。原始文档需要先清洗、chunk，再通过 `scripts/ingest_medical_chunks_milvus.py` 写入 Milvus。用户提问时不会临时解析和嵌入文档。

3. **mNGS 判别为什么输出 JSON，而不是只输出“有害/无害”？**

   业务使用需要可解释证据链，所以默认输出 `label + explanation + evidence + limitations`。评测时只读取 `label` 字段即可。

## 项目现状

项目从原本Django+vue框架中分离，在开发时我们是设计了一个简单的前后端框架和界面的。可以提供简单的登录、注册、创建用户自己的知识库和可交互的对知识库进行增删改查的功能。但由于该部分不是本人负责，我对如何教大家如何配置该部分代码还不是很懂，各位如果希望本项目能提供相关功能，欢迎反馈，我再对项目进行更新。

你可能发现了本项目类似一个大杂烩，将众多功能缝合到了一起。但其实在单独的每个功能实现上，还有很大的优化空间。

例如对知识图谱的处理，目前只是匹配所有实体和与该实体直接相连的关系。其实可以增添对关系类型的判断等，优化知识图谱对大模型输出的影响，避免干扰大模型的输出。这些将在我有时间的时候进行更新，也欢迎你的意见与建议，敬请期待吧。

