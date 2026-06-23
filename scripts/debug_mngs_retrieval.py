from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from mngs.rag_judge import build_mngs_queries, parse_mngs_case, retrieve_mngs_evidence


def main() -> None:
    parser = argparse.ArgumentParser(description="Show mNGS parsed fields, retrieval queries, and recalled chunks.")
    parser.add_argument("--input-file", help="Text file containing the raw mNGS request.")
    parser.add_argument("--question", help="Raw mNGS request text. If omitted, stdin is used.")
    parser.add_argument("--show-context", action="store_true", help="Print the final evidence context sent to the LLM.")
    args = parser.parse_args()

    question = read_question(args)
    case = parse_mngs_case(question)
    queries = build_mngs_queries(case)
    evidence = retrieve_mngs_evidence(case)

    print("== Parsed Case ==")
    for name in (
        "pathogen_type",
        "species_latin",
        "species_chinese",
        "genus_latin",
        "genus_chinese",
        "sample_type",
        "phenotype",
        "diagnosis",
        "immune_status",
        "reads",
        "coverage",
        "abundance",
        "genus_rank",
        "species_rank",
    ):
        print(f"{name}: {getattr(case, name)}")

    print("\n== Queries ==")
    for query in queries:
        print(f"- {query}")

    print(f"\n== Recalled Chunks: {len(evidence.docs)} ==")
    for index, doc in enumerate(evidence.docs, start=1):
        metadata = doc.metadata or {}
        print(f"\n[证据{index}]")
        print(f"source_file: {metadata.get('source_file')}")
        print(f"section_path: {metadata.get('section_path') or metadata.get('section')}")
        print(f"score: {metadata.get('score')}")
        print(f"weighted_score: {metadata.get('weighted_score')}")
        print((doc.page_content or "")[:800].replace("\n", " "))

    if args.show_context:
        print("\n== Final Evidence Context ==")
        print(evidence.context)


def read_question(args: argparse.Namespace) -> str:
    if args.input_file:
        return Path(args.input_file).read_text(encoding="utf-8")
    if args.question:
        return args.question
    return sys.stdin.read()


if __name__ == "__main__":
    main()
