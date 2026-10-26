# 前50个病原的五维知识试采

输入为 `/home/zhangyue/outputs/uptodate_positive_batch/positive.jsonl` 的前50个不同 NameID，保留原始顺序及输入快照。默认输出目录为 `/home/zhangyue/outputs/pathogen_knowledge_first50_20261020`。

本轮采集入口为 PubMed/PMC 与 NCBI Taxonomy，并复用已有 PubMed 全文缓存。此版本不是所有规划来源的完整实现。五维为物种描述、致病性、毒力因子、耐药性、治疗与预后；每维仅检查有限候选，未找到不能解释为不存在。

程序位于 `scripts/data/`：

- `build_pathogen_knowledge_first50.py`：名称规范化、下划线/空格变体、Taxonomy 名称/同义名、按维度检索、种级无结果时属级回退、原始响应缓存、正文获取、模型抽取和引用定位。
- `review_pathogen_knowledge_first50.py`：对已抽取事实进行第二轮机器语义复核，区分种/属与研究类型，导出初始覆盖及来源表。
- `finalize_pathogen_knowledge_first50.py`：确定性校验、来源哈希、药物类别纠错、属级标注、最终汇总与可读索引。结果是临床知识概览，保留的毒力/耐药事实避免遗传或实验操作细节。
- `pathogen_pilot_audit.py`：本轮按PMID和引句匹配的定向核查，记录校正、移维和隔离理由，不覆盖来源原文。
- `validate_pathogen_knowledge_first50.py`：验证输入确为原文件前50行、档案及CSV一致、引用定位、来源哈希、PMID与种属层级标记。

统一 HTTP 锁覆盖多个工作线程，请求完成后等待2.5秒才发下一次实际外网请求。缓存命中不请求网络。遇429和服务端错误退避，保存请求日志。输入文件不修改，输出与既有两维结果分开。

运行示例（在 `/home/zhangyue/cyber-doctor`）：

```bash
python3 scripts/data/build_pathogen_knowledge_first50.py --limit 50 --per-dimension 3 --pause 2.5 --workers 3 --resume
python3 scripts/data/review_pathogen_knowledge_first50.py --workers 2
python3 scripts/data/finalize_pathogen_knowledge_first50.py
python3 scripts/data/validate_pathogen_knowledge_first50.py
python3 tests/test_pathogen_knowledge_pilot.py
```

不要同时启动多个采集进程指向同一个输出目录；单进程内部共享限速。`--resume` 跳过同版本已完成条目，但不等同于全文或五维内容完备。

原始数据分层：

```text
input_snapshot.jsonl        前50条输入快照
manifest.json              输入哈希、维度与请求配置
raw/                       按请求缓存的原始API响应和对应请求元数据
sources/                   每个来源的正文/摘要、标识、原始资料路径
states/                    任务状态和检索式
profiles/                  初次模型抽取草稿
review_logs/               第二轮机器复核判定
reviewed/                  最终使用的JSON和Markdown
facts_with_sources.csv     逐条信息及来源
coverage.csv               五维与种级直接信息的条数
sources_index.csv          来源索引
README.md                  结果说明和病原文档导航
final_summary.json         最终统计与输入对应校验
```

每条保留事实带原文连续引句、来源标识/URL、全文或摘要层级、种/属层级、研究类型及来源文件哈希。引句可定位只是基础校验；机器语义复核不能取代医学专家审核，导出档案明确标为未专家审核。临床建议、个案治疗、体外研究和属级资料不得相互替代。

实现记录：首批联调修正了PMC正文以PMID匹配、参考文献排除、模型顶层字段封装、无效引句隔离、分类多命中消歧、全局限速与缓存复用；6项针对这些问题的回归检查通过。初始及续跑日志保留，用于追溯本轮代码迭代。

后续核查新增第7项回归检查：名称只出现在引言时，也应保留未重复物种名的病例治疗/随访段落。采集运行主要使用20261020.2版；采集后对片段选择做了20261020.3版修正并通过检查，并未据此声称重新穷尽抽取全部来源。输入完整性验证确认本轮快照也等于原JSONL原始前50行，而不只是前50个去重ID。

`genus_context`在此试采导出中表示属级或同群体间接背景，中文明确标注，混合物种总体统计不计入目标种直接证据。单病例、动物/体外、环境研究另有`研究类型`与`证据局限`。本轮发现并隔离了摘要剂量单位异常、旧兽医名称歧义和无法可靠恢复列边界的药敏表格；这些不能因引句存在就视为正确医学结论。

本轮使用22个病原的属名补充检索，并对13个低覆盖档案定向补检。最终覆盖与条数以`final_summary.json`为准；不能把检索到的676篇候选来源当成676篇有效临床证据，也不能把五个JSON字段齐全说成五维证据齐全。50份档案的原文与引用校验结果见`integrity_check.json`，不是医学准确率评估。

最终代码共8项回归检查通过，其中新增检查保证同一文献的疾病描述不被其治疗段的定向规则覆盖。若PMC正文未包含PubMed摘要，本轮定向核查另存`_ABSTRACT.json`来源版本，保留原正文，事实明确标作摘要来源。

研究来源核验流程使用 citation-management 技能。方法参考：Kassis T, Agarwal V, He Y, Patel D, Brueckner AM. *Scientific Agent Skills: A Library of Procedural Knowledge for Research Agents*. 2026. https://doi.org/10.48550/arXiv.2609.00065 。该方法引用不是医学证据来源；医学出处逐条记录于数据文件。
