from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


KEEP_ROLES = ("system", "user")


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Normalize mNGS JSONL records to OpenAI-style messages only. "
            "Each output line is {'messages': [system, user]}."
        )
    )
    parser.add_argument("input", help="Input JSONL file.")
    parser.add_argument("-o", "--output", required=True, help="Output JSONL file.")
    args = parser.parse_args()

    input_path = Path(args.input)
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    total = 0
    written = 0
    with input_path.open("r", encoding="utf-8") as reader, output_path.open("w", encoding="utf-8", newline="\n") as writer:
        for line_no, line in enumerate(reader, start=1):
            line = line.strip()
            if not line:
                continue
            total += 1
            record = parse_json_line(line, input_path, line_no)
            normalized = normalize_record(record, input_path, line_no)
            writer.write(json.dumps(normalized, ensure_ascii=False, separators=(",", ":")) + "\n")
            written += 1

    print(json.dumps({"input": str(input_path), "output": str(output_path), "read": total, "written": written}, ensure_ascii=False))


def parse_json_line(line: str, input_path: Path, line_no: int) -> dict[str, Any]:
    try:
        payload = json.loads(line)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"Invalid JSON at {input_path}:{line_no}: {exc}") from exc
    if not isinstance(payload, dict):
        raise SystemExit(f"Invalid record at {input_path}:{line_no}: expected object, got {type(payload).__name__}")
    return payload


def normalize_record(record: dict[str, Any], input_path: Path, line_no: int) -> dict[str, list[dict[str, str]]]:
    messages = record.get("messages")
    if not isinstance(messages, list):
        raise SystemExit(f"Invalid record at {input_path}:{line_no}: missing messages list")

    normalized_messages: list[dict[str, str]] = []
    seen_roles: set[str] = set()
    for message in messages:
        if not isinstance(message, dict):
            continue
        role = str(message.get("role") or "").strip()
        if role not in KEEP_ROLES or role in seen_roles:
            continue
        content = message.get("content")
        if content is None:
            raise SystemExit(f"Invalid message at {input_path}:{line_no}: {role} content is missing")
        normalized_messages.append({"role": role, "content": str(content)})
        seen_roles.add(role)

    missing = [role for role in KEEP_ROLES if role not in seen_roles]
    if missing:
        raise SystemExit(f"Invalid record at {input_path}:{line_no}: missing role(s): {', '.join(missing)}")

    # Keep the exact model input only; labels and other metadata are intentionally dropped.
    return {"messages": normalized_messages}


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    main()
