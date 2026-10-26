# 从已有 Description 整理病原五维知识

本步骤只处理用户指定的 `positive.jsonl`，调用 DGX 本机的 `qwen3.8-27b`。
不启动或恢复 PubMed、UpToDate 及其他外网采集。

完整读取每条 Description，将其中有明确依据的事实归入物种描述、致病性，
以及原描述确实涉及的其他三个维度。保留否定、不确定、宿主、个案和物种范围，
不将“从患者样本分离”提升为“证实引起感染”。模型不能依靠记忆补写。
每条模型事实必须附有本条 Description 中可连续定位的原文依据；这只是
输入支持检查，不代表已经回查 Description 中的参考文献或完成专家核验。

交付仍为一个 JSON，根对象只有 `病原列表`。各记录包括病原信息和五维知识，
种、属、科、目、纲、门各占一行，保留中英文名。五维中每维包含概述与来源，
一个维度可以包含从原描述提取的多项事实，没有固定一条或100字的抽取上限。
未涉及的维度明确写 `Description未提供相关信息`、来源为 null。
来源标识 `positive.jsonl 的 Description（NameID：…）`，不编造文献链接。
不在知识库中另存整段 Description、原始描述字段或输入校验值。

运行目录的 `jobs/` 是内部检查点，记录事实对应的输入引句、模型标识、版本
和调用信息。总结果在每个批次完成后原子更新，可断点续跑；独立结果不会
覆盖之前暂停的采集结果。外网爬虫需要用户另行决定恢复。

```bash
python3 scripts/data/extract_pathogen_descriptions_qwen.py \
  --input /home/zhangyue/outputs/pathogen_description_20261022/input/positive.jsonl \
  --output /home/zhangyue/outputs/pathogen_description_20261022/pilot16 \
  --limit 16 --workers 1
```

通过抽查后，以同一输入另开全量输出目录运行，省略 `--limit`，按需配置
`--workers` 与 `--batch-size`。同一目录通过文件锁防止重复启动；输入、模型
或抽取规则变化时拒绝直接复用旧检查点。

验证：`python -m unittest discover -s tests -p test_pathogen_description_extraction.py -v`。
