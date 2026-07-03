from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from mngs.rag_judge import build_mngs_queries, parse_mngs_case, retrieve_mngs_evidence


def main() -> None:
    parser = argparse.ArgumentParser(description="Batch inspect fused mNGS RAG retrieval results.")
    parser.add_argument("--input-file", required=True, help="JSONL file containing messages records.")
    parser.add_argument("--output-jsonl", required=True, help="Structured retrieval inspection JSONL.")
    parser.add_argument("--output-md", help="Human-readable Markdown report.")
    parser.add_argument("--limit", type=int, default=0, help="Only process the first N records.")
    parser.add_argument("--top-docs", type=int, default=12, help="Keep first N fused documents per case.")
    parser.add_argument("--max-chars", type=int, default=900, help="Max content chars per recalled chunk.")
    parser.add_argument("--log-every", type=int, default=10, help="Print progress every N cases.")
    args = parser.parse_args()

    input_path = Path(args.input_file)
    output_jsonl_path = Path(args.output_jsonl)
    output_jsonl_path.parent.mkdir(parents=True, exist_ok=True)
    output_md_path = Path(args.output_md) if args.output_md else None
    if output_md_path:
        output_md_path.parent.mkdir(parents=True, exist_ok=True)

    processed = 0
    with input_path.open("r", encoding="utf-8") as reader, output_jsonl_path.open("w", encoding="utf-8", newline="\n") as writer:
        md_parts: list[str] = []
        if output_md_path:
            md_parts.append("# mNGS RAG Batch Retrieval Review\n")

        for line_no, line in enumerate(reader, start=1):
            line = line.strip()
            if not line:
                continue
            if args.limit and processed >= args.limit:
                break

            record = parse_json_line(line, input_path, line_no)
            raw_question = record_to_raw_question(record)
            case = parse_mngs_case(raw_question)
            queries = build_mngs_queries(case)
            evidence = retrieve_mngs_evidence(case)
            docs = evidence.docs[: args.top_docs]

            result = {
                "case_index": processed + 1,
                "line_no": line_no,
                "parsed_case": {
                    "species_latin": case.species_latin,
                    "species_chinese": case.species_chinese,
                    "genus_latin": case.genus_latin,
                    "genus_chinese": case.genus_chinese,
                    "sample_type": case.sample_type,
                    "phenotype": case.phenotype,
                    "diagnosis": case.diagnosis,
                    "immune_status": case.immune_status,
                    "reads": case.reads,
                    "coverage": case.coverage,
                    "abundance": case.abundance,
                    "genus_rank": case.genus_rank,
                    "species_rank": case.species_rank,
                },
                "queries": queries,
                "recall_count": len(evidence.docs),
                "recalls": [doc_to_record(doc, rank, args.max_chars) for rank, doc in enumerate(docs, start=1)],
            }
            writer.write(json.dumps(result, ensure_ascii=False, separators=(",", ":")) + "\n")

            if output_md_path:
                md_parts.append(result_to_markdown(result))

            processed += 1
            if processed == 1 or processed % max(1, args.log_every) == 0:
                print(f"[batch_retrieve] processed={processed} line={line_no} recalls={len(evidence.docs)}", flush=True)

        if output_md_path:
            output_md_path.write_text("\n\n".join(md_parts).rstrip() + "\n", encoding="utf-8", newline="\n")

    print(json.dumps({"input": str(input_path), "output_jsonl": str(output_jsonl_path), "output_md": str(output_md_path) if output_md_path else "", "processed": processed}, ensure_ascii=False))


def parse_json_line(line: str, input_path: Path, line_no: int) -> dict[str, Any]:
    try:
        payload = json.loads(line)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"Invalid JSON at {input_path}:{line_no}: {exc}") from exc
    if not isinstance(payload, dict):
        raise SystemExit(f"Invalid record at {input_path}:{line_no}: expected object")
    return payload


def record_to_raw_question(record: dict[str, Any]) -> str:
    # Keep the same shape users paste into the app so the existing mNGS parser
    # exercises the same JSON/messages path during batch inspection.
    return json.dumps(record, ensure_ascii=False, separators=(",", ":"))


def doc_to_record(doc: Any, rank: int, max_chars: int) -> dict[str, Any]:
    metadata = doc.metadata or {}
    return {
        "rank": rank,
        "source_file": metadata.get("source_file") or metadata.get("title") or metadata.get("source"),
        "section_path": metadata.get("section_path") or metadata.get("section") or "",
        "chunk_index": metadata.get("chunk_index"),
        "score": metadata.get("score"),
        "weighted_score": metadata.get("weighted_score"),
        "content": squash(doc.page_content or "")[:max_chars],
    }


def result_to_markdown(result: dict[str, Any]) -> str:
    case = result["parsed_case"]
    lines = [
        f"## Case {result['case_index']} | line {result['line_no']}",
        "",
        (
            f"- 病原: {case.get('species_chinese') or '未解析'} / {case.get('species_latin') or '未解析'}; "
            f"属: {case.get('genus_chinese') or '未解析'} / {case.get('genus_latin') or '未解析'}"
        ),
        f"- 样本: {case.get('sample_type') or '未解析'}; 表型: {case.get('phenotype') or '未解析'}; 诊断: {case.get('diagnosis') or '未解析'}",
        f"- 召回数: {result['recall_count']}",
        "",
        "### Queries",
    ]
    lines.extend(f"- {query}" for query in result["queries"])
    lines.append("")
    lines.append("### Fused Recalls")
    for recall in result["recalls"]:
        lines.extend(
            [
                "",
                f"#### Rank {recall['rank']}",
                f"- source_file: {recall.get('source_file') or ''}",
                f"- section_path: {recall.get('section_path') or ''}",
                f"- chunk_index: {recall.get('chunk_index')}",
                f"- score: {recall.get('score')}; weighted_score: {recall.get('weighted_score')}",
                "",
                recall.get("content") or "",
            ]
        )
    return "\n".join(lines)


def squash(text: str) -> str:
    return " ".join(str(text).split())


if __name__ == "__main__":
    main()
