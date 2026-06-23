from __future__ import annotations

import argparse
import hashlib
import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable


HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
NUMBERED_ITEM_RE = re.compile(r"(?<![\d.])(\d{1,2})[.．、](?!\d)\s*")
EXPLANATORY_KEYWORDS = (
    "位于",
    "起自",
    "止于",
    "作用",
    "功能",
    "组成",
    "分为",
    "包括",
    "表现",
    "症状",
    "诊断",
    "治疗",
    "适用于",
    "禁忌",
    "原因",
    "机制",
    "导致",
    "引起",
    "可见",
    "常见",
)


@dataclass
class HeadingState:
    part: str = ""
    chapter: str = ""
    section: str = ""
    subsection: str = ""
    subsubsection: str = ""

    def update(self, level: int, title: str) -> None:
        if level == 1:
            self.part = title
            self.chapter = ""
            self.section = ""
            self.subsection = ""
            self.subsubsection = ""
        elif level == 2:
            self.chapter = title
            self.section = ""
            self.subsection = ""
            self.subsubsection = ""
        elif level == 3:
            self.section = title
            self.subsection = ""
            self.subsubsection = ""
        elif level == 4:
            self.subsection = title
            self.subsubsection = ""
        elif level >= 5:
            self.subsubsection = title

    def path_items(self) -> list[str]:
        return [item for item in [self.part, self.chapter, self.section, self.subsection, self.subsubsection] if item]

    def path_text(self) -> str:
        return " > ".join(self.path_items())


@dataclass
class Block:
    block_type: str
    text: str
    state: HeadingState
    line_start: int
    line_end: int


def main() -> None:
    parser = argparse.ArgumentParser(description="Chunk cleaned medical markdown into JSONL records.")
    parser.add_argument("input", nargs="?", help="Clean markdown file.")
    parser.add_argument("-o", "--output", default=None, help="Output JSONL path.")
    parser.add_argument("--input-dir", default=None, help="Chunk every *.md file under this directory.")
    parser.add_argument("--output-dir", default=None, help="Output directory for --input-dir mode.")
    parser.add_argument("--max-chars", type=int, default=1200, help="Max characters per chunk.")
    parser.add_argument("--overlap-chars", type=int, default=150, help="Overlap characters for long text chunks.")
    parser.add_argument("--min-chars", type=int, default=250, help="Try to merge small neighbouring blocks up to this size.")
    parser.add_argument("--max-tokens", type=int, default=None, help=argparse.SUPPRESS)
    parser.add_argument("--overlap-tokens", type=int, default=None, help=argparse.SUPPRESS)
    parser.add_argument("--min-tokens", type=int, default=None, help=argparse.SUPPRESS)
    parser.add_argument("--doc-type", default="textbook", help="Document type metadata.")
    args = parser.parse_args()
    max_chars = args.max_tokens if args.max_tokens is not None else args.max_chars
    overlap_chars = args.overlap_tokens if args.overlap_tokens is not None else args.overlap_chars
    min_chars = args.min_tokens if args.min_tokens is not None else args.min_chars

    if args.input_dir:
        if not args.output_dir:
            parser.error("--output-dir is required when using --input-dir")
        summaries = chunk_directory(
            input_dir=Path(args.input_dir),
            output_dir=Path(args.output_dir),
            max_chars=max_chars,
            overlap_chars=overlap_chars,
            min_chars=min_chars,
            doc_type=args.doc_type,
        )
        print(json.dumps(summaries, ensure_ascii=False, indent=2))
        return

    if not args.input:
        parser.error("input markdown file is required unless --input-dir is used")
    input_path = Path(args.input)
    output_path = Path(args.output) if args.output else input_path.with_suffix(".chunks.jsonl")
    summary = chunk_file(
        input_path=input_path,
        output_path=output_path,
        max_chars=max_chars,
        overlap_chars=overlap_chars,
        min_chars=min_chars,
        doc_type=args.doc_type,
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))


def chunk_directory(
    input_dir: Path,
    output_dir: Path,
    max_chars: int,
    overlap_chars: int,
    min_chars: int,
    doc_type: str,
) -> list[dict]:
    summaries = []
    for input_path in sorted(input_dir.rglob("*.md")):
        rel_path = input_path.relative_to(input_dir)
        output_path = (output_dir / rel_path).with_suffix(".chunks.jsonl")
        summaries.append(
            chunk_file(
                input_path=input_path,
                output_path=output_path,
                max_chars=max_chars,
                overlap_chars=overlap_chars,
                min_chars=min_chars,
                doc_type=doc_type,
                source_id=rel_path.as_posix(),
            )
        )
    return summaries


def chunk_file(
    input_path: Path,
    output_path: Path,
    max_chars: int,
    overlap_chars: int,
    min_chars: int,
    doc_type: str,
    source_id: str | None = None,
) -> dict:
    text = input_path.read_text(encoding="utf-8", errors="replace")
    source_id = source_id or input_path.name
    blocks = parse_blocks(text)
    chunks = build_chunks(
        blocks=blocks,
        source_path=input_path,
        source_id=source_id,
        max_chars=max_chars,
        overlap_chars=overlap_chars,
        min_chars=min_chars,
        doc_type=doc_type,
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        "\n".join(json.dumps(chunk, ensure_ascii=False) for chunk in chunks) + ("\n" if chunks else ""),
        encoding="utf-8",
        newline="\n",
    )
    return {"input": str(input_path), "output": str(output_path), "chunks": len(chunks)}


def parse_blocks(text: str) -> list[Block]:
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    state = HeadingState()
    blocks: list[Block] = []
    buffer: list[str] = []
    buffer_start = 1
    in_table = False
    table_buffer: list[str] = []
    table_start = 1

    def flush_paragraph(end_line: int) -> None:
        nonlocal buffer, buffer_start
        paragraph = "\n".join(buffer).strip()
        if paragraph:
            for block_text, block_type in split_structured_paragraph(paragraph):
                blocks.append(Block(block_type=block_type, text=block_text, state=copy_state(state), line_start=buffer_start, line_end=end_line))
        buffer = []

    def flush_table(end_line: int) -> None:
        nonlocal table_buffer, in_table
        table = "\n".join(table_buffer).strip()
        if table:
            blocks.append(Block(block_type="table", text=table, state=copy_state(state), line_start=table_start, line_end=end_line))
        table_buffer = []
        in_table = False

    for line_no, raw_line in enumerate(lines, start=1):
        line = raw_line.rstrip()
        heading = HEADING_RE.match(line)
        if heading:
            if in_table:
                flush_table(line_no - 1)
            flush_paragraph(line_no - 1)
            level = len(heading.group(1))
            title = heading.group(2).strip()
            state.update(level, title)
            continue

        if is_table_line(line):
            flush_paragraph(line_no - 1)
            if not in_table:
                in_table = True
                table_start = line_no
                table_buffer = []
            table_buffer.append(line)
            continue

        if in_table:
            flush_table(line_no - 1)

        if not line.strip():
            flush_paragraph(line_no - 1)
            buffer_start = line_no + 1
            continue

        if not buffer:
            buffer_start = line_no
        buffer.append(line.strip())

    if in_table:
        flush_table(len(lines))
    flush_paragraph(len(lines))
    return merge_numbered_item_continuations(blocks)


def split_structured_paragraph(text: str) -> list[tuple[str, str]]:
    text = normalize_text(text)
    list_kind = classify_inline_numbered_list(text)
    if list_kind == "explanatory_numbered_list":
        items = split_numbered_items(text)
        if len(items) >= 2:
            return [
                (item, "numbered_item" if NUMBERED_ITEM_RE.match(item) else "paragraph")
                for item in items
            ]
    if list_kind == "short_label_list":
        return [(text, "short_label_list")]
    if is_standalone_numbered_item(text):
        return [(text, "numbered_item")]
    return [(text, "paragraph")]


def classify_inline_numbered_list(text: str) -> str:
    matches = list(NUMBERED_ITEM_RE.finditer(text))
    if len(matches) < 2:
        return "none"

    items = split_numbered_items(text)
    if len(items) < 2:
        return "none"

    lengths = [len(strip_item_number(item)) for item in items]
    avg_len = sum(lengths) / len(lengths)
    explanatory_hits = sum(text.count(keyword) for keyword in EXPLANATORY_KEYWORDS)
    semicolon_density = text.count("；") + text.count(";")

    if len(items) >= 3 and avg_len <= 28 and semicolon_density >= max(3, len(items) // 2):
        return "short_label_list"
    if avg_len >= 45 or explanatory_hits >= 2:
        return "explanatory_numbered_list"
    return "none"


def split_numbered_items(text: str) -> list[str]:
    matches = list(NUMBERED_ITEM_RE.finditer(text))
    if not matches:
        return [text]

    prefix = text[: matches[0].start()].strip()
    items: list[str] = []
    if prefix:
        items.append(prefix)
    for idx, match in enumerate(matches):
        start = match.start()
        end = matches[idx + 1].start() if idx + 1 < len(matches) else len(text)
        item = text[start:end].strip()
        if item:
            items.append(item)
    return items


def is_standalone_numbered_item(text: str) -> bool:
    if not NUMBERED_ITEM_RE.match(text):
        return False
    body = strip_item_number(text)
    if len(body) >= 24:
        return True
    return any(keyword in body for keyword in EXPLANATORY_KEYWORDS)


def merge_numbered_item_continuations(blocks: list[Block]) -> list[Block]:
    merged: list[Block] = []
    for block in blocks:
        if (
            merged
            and merged[-1].block_type == "numbered_item"
            and block.block_type == "paragraph"
            and merged[-1].state.path_text() == block.state.path_text()
            and block.line_start - merged[-1].line_end <= 4
            and is_probable_continuation(merged[-1].text, block.text)
        ):
            previous = merged[-1]
            merged[-1] = Block(
                block_type="numbered_item",
                text=join_continuation(previous.text, block.text),
                state=previous.state,
                line_start=previous.line_start,
                line_end=block.line_end,
            )
            continue
        merged.append(block)
    return merged


def is_probable_continuation(previous_text: str, current_text: str) -> bool:
    if not current_text or NUMBERED_ITEM_RE.match(current_text):
        return False
    if re.search(r"[。！？!?；;]\s*$", previous_text):
        return False
    return True


def join_continuation(previous_text: str, current_text: str) -> str:
    if re.search(r"[\u4e00-\u9fff]$", previous_text) and re.match(r"^[\u4e00-\u9fff]", current_text):
        return previous_text + current_text
    return previous_text.rstrip() + " " + current_text.lstrip()


def build_chunks(
    blocks: list[Block],
    source_path: Path,
    source_id: str,
    max_chars: int,
    overlap_chars: int,
    min_chars: int,
    doc_type: str,
) -> list[dict]:
    chunks: list[dict] = []
    pending: list[Block] = []

    def flush_pending() -> None:
        nonlocal pending
        if pending:
            chunks.extend(make_chunks_from_blocks(pending, source_path, source_id, len(chunks), max_chars, overlap_chars, doc_type))
            pending = []

    for block in blocks:
        chars = count_chars(block.text)
        if block.block_type in {"table", "short_label_list", "numbered_item"}:
            flush_pending()
            chunks.extend(make_chunks_from_blocks([block], source_path, source_id, len(chunks), max_chars, overlap_chars, doc_type))
            continue

        pending_chars = sum(count_chars(item.text) for item in pending)
        same_path = not pending or pending[-1].state.path_text() == block.state.path_text()
        if pending and (not same_path or pending_chars + chars > max_chars or pending_chars >= min_chars):
            flush_pending()
        pending.append(block)

    flush_pending()
    for index, chunk in enumerate(chunks):
        chunk["chunk_index"] = index
        chunk["chunk_id"] = stable_chunk_id(chunk["source_id"], index, chunk["answer_text"])
    return chunks


def make_chunks_from_blocks(
    blocks: list[Block],
    source_path: Path,
    source_id: str,
    start_index: int,
    max_chars: int,
    overlap_chars: int,
    doc_type: str,
) -> list[dict]:
    text = "\n\n".join(block.text for block in blocks).strip()
    if not text:
        return []
    state = blocks[0].state
    block_type = blocks[0].block_type if len(blocks) == 1 else "paragraph_group"
    pieces = split_long_text(text, max_chars=max_chars, overlap_chars=overlap_chars)
    result = []
    for piece_index, piece in enumerate(pieces):
        metadata = {
            "doc_type": doc_type,
            "source_id": source_id,
            "source_file": source_path.name,
            "source_stem": source_path.stem,
            "part": state.part,
            "chapter": state.chapter,
            "section": state.section,
            "subsection": state.subsection,
            "subsubsection": state.subsubsection,
            "section_path": state.path_text(),
            "block_type": block_type,
            "line_start": blocks[0].line_start,
            "line_end": blocks[-1].line_end,
            "piece_index": piece_index,
            "piece_count": len(pieces),
        }
        answer_text = piece.strip()
        search_text = build_search_text(metadata, answer_text)
        result.append(
            {
                "chunk_id": "",
                "chunk_index": start_index + len(result),
                "source_id": source_id,
                "source_file": source_path.name,
                "source_stem": source_path.stem,
                "doc_type": doc_type,
                "section": metadata["section_path"],
                "answer_text": answer_text,
                "search_text": search_text,
                "metadata": metadata,
            }
        )
    return result


def split_long_text(text: str, max_chars: int, overlap_chars: int) -> list[str]:
    if len(text) <= max_chars:
        return [text]

    pieces: list[str] = []
    start_char = 0
    while start_char < len(text):
        hard_end = min(start_char + max_chars, len(text))
        end_char = choose_char_end(text, start_char, hard_end)
        if end_char <= start_char:
            end_char = hard_end
        piece = text[start_char:end_char].strip()
        if piece:
            pieces.append(piece)
        if end_char >= len(text):
            break
        start_char = max(end_char - overlap_chars, start_char + 1)
    return pieces


def choose_char_end(text: str, start_char: int, hard_end_char: int) -> int:
    if hard_end_char >= len(text):
        return len(text)
    min_end = min(start_char + max(1, (hard_end_char - start_char) // 2), hard_end_char)
    candidates: list[int] = []
    for char_idx in range(min_end, hard_end_char):
        lookback = text[max(0, char_idx - 3) : char_idx + 1]
        if re.search(r"[。！？!?；;：:]\s*$", lookback):
            candidates.append(char_idx + 1)
    return candidates[-1] if candidates else hard_end_char


def build_search_text(metadata: dict, answer_text: str) -> str:
    context_lines = [
        f"文档类型：{metadata['doc_type']}",
        f"书名：{metadata['source_stem']}",
        f"章节路径：{metadata['section_path']}",
    ]
    return "\n".join(line for line in context_lines if line and not line.endswith("：")) + f"\n正文：{answer_text}"


def count_chars(text: str) -> int:
    return len(text)


def strip_item_number(item: str) -> str:
    return NUMBERED_ITEM_RE.sub("", item, count=1).strip()


def normalize_text(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n+", "\n", text)
    return text.strip()


def is_table_line(line: str) -> bool:
    stripped = line.strip()
    return stripped.startswith("|") and stripped.endswith("|") and stripped.count("|") >= 2


def stable_chunk_id(source_path: str, index: int, text: str) -> str:
    digest = hashlib.sha1(f"{source_path}\n{index}\n{text[:200]}".encode("utf-8")).hexdigest()[:16]
    return f"{Path(source_path).stem}:{index}:{digest}"


def copy_state(state: HeadingState) -> HeadingState:
    return HeadingState(**asdict(state))


if __name__ == "__main__":
    main()
