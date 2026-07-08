from __future__ import annotations

import argparse
import csv
import json
import re
from pathlib import Path
from typing import Any


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Summarize whether recalled mNGS chunks contain the pathogen species/genus names."
    )
    parser.add_argument("--input-jsonl", required=True, help="JSONL produced by batch_debug_mngs_retrieval.py.")
    parser.add_argument("--output-jsonl", required=True, help="Per-case detailed hit statistics JSONL.")
    parser.add_argument("--output-summary", required=True, help="Overall summary JSON.")
    parser.add_argument("--output-csv", help="Optional per-case CSV for quick spreadsheet review.")
    parser.add_argument("--top-k", type=int, default=5, help="Only inspect first K recalled chunks per case.")
    args = parser.parse_args()

    input_path = Path(args.input_jsonl)
    output_jsonl_path = Path(args.output_jsonl)
    output_summary_path = Path(args.output_summary)
    output_csv_path = Path(args.output_csv) if args.output_csv else None

    output_jsonl_path.parent.mkdir(parents=True, exist_ok=True)
    output_summary_path.parent.mkdir(parents=True, exist_ok=True)
    if output_csv_path:
        output_csv_path.parent.mkdir(parents=True, exist_ok=True)

    rows: list[dict[str, Any]] = []
    summary = empty_summary(args.top_k)

    with input_path.open("r", encoding="utf-8") as reader, output_jsonl_path.open(
        "w", encoding="utf-8", newline="\n"
    ) as writer:
        for line_no, line in enumerate(reader, start=1):
            line = line.strip()
            if not line:
                continue
            record = parse_json_line(line, input_path, line_no)
            row = summarize_case(record, top_k=args.top_k)
            rows.append(row)
            update_summary(summary, row)
            writer.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")

    finalize_summary(summary)
    output_summary_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )

    if output_csv_path:
        write_csv(output_csv_path, rows)

    print(
        json.dumps(
            {
                "input": str(input_path),
                "output_jsonl": str(output_jsonl_path),
                "output_summary": str(output_summary_path),
                "output_csv": str(output_csv_path) if output_csv_path else "",
                "case_count": summary["case_count"],
                "case_any_name_hit_rate": summary["case_any_name_hit_rate"],
                "recall_name_hit_rate": summary["recall_name_hit_rate"],
            },
            ensure_ascii=False,
        )
    )


def empty_summary(top_k: int) -> dict[str, Any]:
    return {
        "top_k": top_k,
        "case_count": 0,
        "case_with_any_name_hit": 0,
        "case_with_species_hit": 0,
        "case_with_genus_hit": 0,
        "total_recalls_checked": 0,
        "total_recalls_with_any_name_hit": 0,
        "total_recalls_with_species_hit": 0,
        "total_recalls_with_genus_hit": 0,
        "matched_recall_count_distribution": {},
        "case_any_name_hit_rate": 0.0,
        "case_species_hit_rate": 0.0,
        "case_genus_hit_rate": 0.0,
        "recall_name_hit_rate": 0.0,
        "recall_species_hit_rate": 0.0,
        "recall_genus_hit_rate": 0.0,
    }


def parse_json_line(line: str, input_path: Path, line_no: int) -> dict[str, Any]:
    try:
        payload = json.loads(line)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"Invalid JSON at {input_path}:{line_no}: {exc}") from exc
    if not isinstance(payload, dict):
        raise SystemExit(f"Invalid record at {input_path}:{line_no}: expected object")
    return payload


def summarize_case(record: dict[str, Any], top_k: int) -> dict[str, Any]:
    case = record.get("parsed_case") or {}
    terms = build_name_terms(case)
    recalls = list(record.get("recalls") or [])[:top_k]
    recall_hits = [summarize_recall(recall, terms) for recall in recalls]

    matched_recalls = [item for item in recall_hits if item["has_any_name"]]
    species_matched_recalls = [item for item in recall_hits if item["has_species_name"]]
    genus_matched_recalls = [item for item in recall_hits if item["has_genus_name"]]

    return {
        "case_index": record.get("case_index"),
        "line_no": record.get("line_no"),
        "species_latin": case.get("species_latin") or "",
        "species_chinese": case.get("species_chinese") or "",
        "genus_latin": case.get("genus_latin") or "",
        "genus_chinese": case.get("genus_chinese") or "",
        "matched_terms": terms,
        "recall_count_checked": len(recall_hits),
        "matched_recall_count": len(matched_recalls),
        "species_matched_recall_count": len(species_matched_recalls),
        "genus_matched_recall_count": len(genus_matched_recalls),
        "has_any_name_hit": bool(matched_recalls),
        "has_species_hit": bool(species_matched_recalls),
        "has_genus_hit": bool(genus_matched_recalls),
        "recall_hits": recall_hits,
    }


def build_name_terms(case: dict[str, Any]) -> dict[str, list[str]]:
    species_latin = normalize_text(case.get("species_latin") or "")
    genus_latin = normalize_text(case.get("genus_latin") or "")
    species_chinese = normalize_text(case.get("species_chinese") or "")
    genus_chinese = normalize_text(case.get("genus_chinese") or "")

    species_terms = expand_latin_terms(species_latin) + expand_chinese_terms(species_chinese)
    genus_terms = expand_latin_terms(genus_latin) + expand_chinese_terms(genus_chinese)

    return {
        "species": unique_nonempty(species_terms),
        "genus": unique_nonempty(genus_terms),
        "all": unique_nonempty(species_terms + genus_terms),
    }


def expand_latin_terms(value: str) -> list[str]:
    if not value:
        return []
    terms = [value]
    if "_" in value:
        terms.append(value.replace("_", " "))
    if " " in value:
        terms.append(value.replace(" ", "_"))
    return terms


def expand_chinese_terms(value: str) -> list[str]:
    if not value:
        return []
    terms = [value]
    if value.endswith("属"):
        terms.append(value[:-1])
    return terms


def unique_nonempty(values: list[str]) -> list[str]:
    seen: set[str] = set()
    output: list[str] = []
    for value in values:
        cleaned = normalize_text(value)
        if not cleaned:
            continue
        key = cleaned.lower()
        if key in seen:
            continue
        seen.add(key)
        output.append(cleaned)
    return output


def summarize_recall(recall: dict[str, Any], terms: dict[str, list[str]]) -> dict[str, Any]:
    content = normalize_text(recall.get("content") or "")
    species_hits = find_hits(content, terms["species"])
    genus_hits = find_hits(content, terms["genus"])
    any_hits = unique_nonempty(species_hits + genus_hits)
    return {
        "rank": recall.get("rank"),
        "source_file": recall.get("source_file") or "",
        "section_path": recall.get("section_path") or "",
        "chunk_index": recall.get("chunk_index"),
        "has_any_name": bool(any_hits),
        "has_species_name": bool(species_hits),
        "has_genus_name": bool(genus_hits),
        "hit_terms": any_hits,
        "species_hit_terms": species_hits,
        "genus_hit_terms": genus_hits,
    }


def find_hits(content: str, terms: list[str]) -> list[str]:
    if not content or not terms:
        return []
    hits: list[str] = []
    content_lower = content.lower()
    for term in terms:
        if is_latin_like(term):
            if latin_term_in_text(term, content_lower):
                hits.append(term)
        elif term in content:
            hits.append(term)
    return unique_nonempty(hits)


def latin_term_in_text(term: str, content_lower: str) -> bool:
    escaped = re.escape(term.lower())
    # Latin names should not match inside a longer Latin token.
    pattern = rf"(?<![a-z0-9_]){escaped}(?![a-z0-9_])"
    return re.search(pattern, content_lower) is not None


def is_latin_like(term: str) -> bool:
    return bool(re.search(r"[A-Za-z]", term))


def normalize_text(value: Any) -> str:
    return " ".join(str(value or "").split())


def update_summary(summary: dict[str, Any], row: dict[str, Any]) -> None:
    summary["case_count"] += 1
    summary["case_with_any_name_hit"] += int(row["has_any_name_hit"])
    summary["case_with_species_hit"] += int(row["has_species_hit"])
    summary["case_with_genus_hit"] += int(row["has_genus_hit"])
    summary["total_recalls_checked"] += int(row["recall_count_checked"])
    summary["total_recalls_with_any_name_hit"] += int(row["matched_recall_count"])
    summary["total_recalls_with_species_hit"] += int(row["species_matched_recall_count"])
    summary["total_recalls_with_genus_hit"] += int(row["genus_matched_recall_count"])
    key = str(row["matched_recall_count"])
    distribution = summary["matched_recall_count_distribution"]
    distribution[key] = distribution.get(key, 0) + 1


def finalize_summary(summary: dict[str, Any]) -> None:
    case_count = max(1, int(summary["case_count"]))
    recall_count = max(1, int(summary["total_recalls_checked"]))
    summary["case_any_name_hit_rate"] = round(summary["case_with_any_name_hit"] / case_count, 6)
    summary["case_species_hit_rate"] = round(summary["case_with_species_hit"] / case_count, 6)
    summary["case_genus_hit_rate"] = round(summary["case_with_genus_hit"] / case_count, 6)
    summary["recall_name_hit_rate"] = round(summary["total_recalls_with_any_name_hit"] / recall_count, 6)
    summary["recall_species_hit_rate"] = round(summary["total_recalls_with_species_hit"] / recall_count, 6)
    summary["recall_genus_hit_rate"] = round(summary["total_recalls_with_genus_hit"] / recall_count, 6)


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    fields = [
        "case_index",
        "line_no",
        "species_latin",
        "species_chinese",
        "genus_latin",
        "genus_chinese",
        "recall_count_checked",
        "matched_recall_count",
        "species_matched_recall_count",
        "genus_matched_recall_count",
        "has_any_name_hit",
        "has_species_hit",
        "has_genus_hit",
    ]
    with path.open("w", encoding="utf-8-sig", newline="") as writer_file:
        writer = csv.DictWriter(writer_file, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field) for field in fields})


if __name__ == "__main__":
    main()
