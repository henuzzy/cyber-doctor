from __future__ import annotations

import json

from cyber_doctor.mngs.rag_judge import parse_mngs_cases


def test_parses_labeled_patient_and_mngs_fields_from_messages_export() -> None:
    user_content = """【患者信息】
年龄：52岁
性别：男
取样部位：痰液
免疫状态：抑制
临床表现：咳嗽发热
临床诊断：肝硬化

【病原基本信息】
病原类型：细菌
种-拉丁名：Haemophilus_parahaemolyticus
种-中文名：副溶血嗜血杆菌
属-拉丁名：Haemophilus
属-中文名：嗜血杆菌属
病原描述：可引起少数严重感染。

【mNGS 检出指标】
种-检出特异序列数（SDLatin_smrn）：2560
属-检出特异序列数（SDG_SMRN）：4440
种-检出排名（在本样本所有种中）：第 1 名
属-检出排名（在本样本所有属中）：第 2 名
基因组覆盖率（CovRate）：13.98
种-相对丰度（%）：46.57
属-相对丰度（%）：56.46
"""
    payload = {
        "id": "sample-001",
        "messages": [
            {"role": "user", "content": user_content},
            {"role": "assistant", "content": "有害"},
        ],
    }

    [case] = parse_mngs_cases(json.dumps(payload, ensure_ascii=False))

    assert case.case_id == "sample-001"
    assert case.species_latin == "Haemophilus_parahaemolyticus"
    assert case.species_chinese == "副溶血嗜血杆菌"
    assert case.sample_type == "痰液"
    assert case.age == "52岁"
    assert case.sex == "男"
    assert case.immune_status == "抑制"
    assert case.phenotype == "咳嗽发热"
    assert case.diagnosis == "肝硬化"
    assert case.reads == "2560"
    assert case.genus_reads == "4440"
    assert case.species_rank == "第 1 名"
    assert case.genus_rank == "第 2 名"
    assert case.coverage == "13.98"
    assert case.abundance == "46.57"
    assert case.genus_abundance == "56.46"
    assert case.pathogenicity_text == "可引起少数严重感染。"
    assert case.existing_label == "有害"


def test_uses_export_metadata_for_case_id_sample_and_immune_status() -> None:
    payload = {
        "id": "outer-id",
        "messages": [
            {"role": "user", "content": "【病原基本信息】\n种-拉丁名：Kluyvera_ascorbata"},
            {"role": "assistant", "content": "无害"},
        ],
        "metadata": {"sample_id": "sample-002", "stype": "脑脊液", "mianyi": 1},
    }

    [case] = parse_mngs_cases(json.dumps(payload, ensure_ascii=False))

    assert case.case_id == "sample-002"
    assert case.sample_type == "脑脊液"
    assert case.immune_status == "正常"
