from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description="Convert case report PDFs to Markdown with unstructured.")
    parser.add_argument("--input-dir", required=True, help="Directory containing PDF case reports.")
    parser.add_argument("--output-dir", required=True, help="Directory for converted Markdown files.")
    parser.add_argument(
        "--strategy",
        default="fast",
        choices=["fast", "hi_res", "ocr_only"],
        help="unstructured PDF parsing strategy.",
    )
    parser.add_argument("--max-files", type=int, default=0, help="Convert only the first N PDFs for quick checks.")
    args = parser.parse_args()

    input_dir = Path(args.input_dir)
    output_dir = Path(args.output_dir)
    pdf_paths = sorted(input_dir.rglob("*.pdf"))
    if args.max_files > 0:
        pdf_paths = pdf_paths[: args.max_files]
    if not pdf_paths:
        raise SystemExit(f"No PDF files found under {input_dir}")

    output_dir.mkdir(parents=True, exist_ok=True)
    print(f"[convert] input={input_dir}", flush=True)
    print(f"[convert] output={output_dir}", flush=True)
    print(f"[convert] pdf_count={len(pdf_paths)} strategy={args.strategy}", flush=True)

    for index, pdf_path in enumerate(pdf_paths, start=1):
        output_path = output_dir / f"{safe_stem(pdf_path.stem)}.md"
        print(f"[convert] {index}/{len(pdf_paths)} {pdf_path.name} -> {output_path.name}", flush=True)
        try:
            markdown, stats = pdf_to_markdown(pdf_path, strategy=args.strategy)
        except Exception as exc:
            print(f"[convert] failed: {pdf_path.name}: {exc}", file=sys.stderr, flush=True)
            continue
        output_path.write_text(markdown, encoding="utf-8", newline="\n")
        # These counters make bad PDF parses obvious: zero elements or very low text chars
        # means the selected unstructured strategy did not actually extract the article body.
        print(
            f"[convert] ok elements={stats['elements']} text_chars={stats['text_chars']} "
            f"categories={stats['categories']}",
            flush=True,
        )


def pdf_to_markdown(pdf_path: Path, strategy: str) -> tuple[str, dict[str, object]]:
    from unstructured.partition.pdf import partition_pdf

    # Keep this converter intentionally thin. PDF parsing quality depends on the
    # selected unstructured strategy and host dependencies such as poppler/tesseract.
    elements = partition_pdf(filename=str(pdf_path), strategy=strategy, infer_table_structure=False)
    chunks: list[str] = [f"# {pdf_path.stem}", ""]
    categories: dict[str, int] = {}
    text_chars = 0
    last_category = ""

    for element in elements:
        text = normalize_text(str(element))
        if not text:
            continue
        category = element.__class__.__name__
        categories[category] = categories.get(category, 0) + 1
        text_chars += len(text)

        if category in {"Title", "Header"}:
            chunks.append(f"## {text}")
        elif category == "ListItem":
            chunks.append(f"- {text}")
        elif category == "Table":
            chunks.append(text)
        else:
            if last_category and category != last_category:
                chunks.append("")
            chunks.append(text)
        last_category = category

    stats = {"elements": len(elements), "text_chars": text_chars, "categories": categories}
    return normalize_markdown("\n".join(chunks)), stats


def normalize_text(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def normalize_markdown(markdown: str) -> str:
    markdown = re.sub(r"\n{3,}", "\n\n", markdown)
    return markdown.strip() + "\n"


def safe_stem(stem: str) -> str:
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", stem).strip(" .")
    return cleaned or "converted_pdf"


if __name__ == "__main__":
    main()
