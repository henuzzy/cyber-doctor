# positive.jsonl 全量病原五维概览

用户确认的交付结构是一个 JSON 文件，根对象只有 `病原列表`。
每条记录包含 `病原信息` 和 `五维知识`；每个维度只有 `概述`、`来源`。
种、属、科、目、纲、门同级排列，每级的中文名和拉丁名单独占一行。
交付文件不带 SHA256、数据说明、内部工作路径或运行日志。

DGX 入口：`/home/zhangyue/cyber-doctor/scripts/data/build_pathogen_knowledge_all.py`。

- 输入：`/home/zhangyue/outputs/uptodate_positive_batch/positive.jsonl`。
- 已完成的50条种子：`/home/zhangyue/outputs/pathogen_knowledge_all_20261022/seed_first50.json`。
- 单文件输出：`/home/zhangyue/outputs/pathogen_knowledge_all_20261022/pathogen_knowledge_all_simple.json`。
- 内部进度：同目录 `summary.json`；个体检查点：`jobs/`；来源与原文：`sources/`。

输入共1,760个不同NameID。前50条沿用用户审阅后的简版，整体保持不变，
不重复联网采集。脚本复用既有试采采集器与机器复核器，以及已下载的
3,774篇去重PubMed/PMC全文。对外请求统一由一个锁串行发送；请求完成后
至少暂停2.5秒，429/服务端错误另有退避。Qwen调用为DGX本机服务。

种名使用下划线、空格、分类同义名变体检索；种级无结果时回退属级。
属级或混合群体信息在概述里标明，病例、动物和体外研究保留必要范围。
无法检索的条目保留原NameID，缺少证据的维度写“本轮未采到信息”，来源为null。
不会把运行失败、模型失败直接记成已完成的空白结果。

每维抽取至多一条概览，再通过原文引句定位及第二轮机器复核。
有来源、范围通过校验的记录才进入总文件；每完成一条即原子更新输出。
源信息与机器核验不等于专家审核或已穷尽相关文献。
原始名称和分类沿用输入，不在本次任务中猜测修正分类等级。

仅当记录的类别、NameID类型标记、中英文种名及完整分类字段一致时，
才复用其他NameID的已完成结果。不同病毒型别、亚种或菌株不会仅因种名
相同被合并；没有种名的记录也不做这种自动复用。

```bash
cd /home/zhangyue/cyber-doctor
python3 scripts/data/build_pathogen_knowledge_all.py --plan-only
python3 scripts/data/build_pathogen_knowledge_all.py --max-new 1
python3 scripts/data/build_pathogen_knowledge_all.py
```

同一个输出目录通过文件锁阻止重复启动。正常续跑沿用完全相同的输入、
种子和参数。启动时跳过已成功完成的个体，异常最多额外重试两轮；失败
列表保存到 `failures.json`，不伪报为全部完成。`summary.json` 的
`all_done` 只有在全部输入记录均被处理后才为true。进程收到TERM/INT后
完成当前步骤并保存检查点。后台进程由运行时启动器脱离SSH会话运行。

验证：新增6项回归检查覆盖输出格式、类型不同不复用、无种名不去重、
属级/动物范围保留、引句不可定位不发布、属级TaxID不充当目标种TaxID；
既有8项试采回归检查保持通过。

```bash
python3 tests/test_pathogen_knowledge_all.py
python3 tests/test_pathogen_knowledge_pilot.py
```
