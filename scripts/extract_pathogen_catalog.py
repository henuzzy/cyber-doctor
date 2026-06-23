from __future__ import annotations

import argparse
import ast
import json
import re
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

DEFAULT_OUTPUT = ROOT / "mngs" / "pathogen_catalog.json"


def main() -> None:
    parser = argparse.ArgumentParser(description="Extract pathogen aliases from mNGS JSONL datasets.")
    parser.add_argument("input", nargs="+", help="Input JSONL file(s).")
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT), help="Output pathogen catalog JSON path.")
    args = parser.parse_args()

    records: dict[str, dict] = {}
    stats = Counter()
    for input_path in args.input:
        for line_no, payload in iter_jsonl(Path(input_path)):
            stats["rows"] += 1
            info = extract_pathogen_info(payload)
            if not info.get("species_latin") and not info.get("species_chinese"):
                stats["missing_pathogen"] += 1
                print(f"skip missing pathogen: {input_path}:{line_no}", file=sys.stderr)
                continue
            key = stable_key(info)
            current = records.get(key)
            records[key] = merge_record(current, info)

    catalog = {
        "version": 1,
        "description": "Pathogen aliases extracted from mNGS training JSONL for rule-based query rewriting.",
        "count": len(records),
        "pathogens": sorted(records.values(), key=lambda item: (item.get("species_latin") or "", item.get("species_chinese") or "")),
    }

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(catalog, ensure_ascii=False, indent=2), encoding="utf-8")
    stats["unique_pathogens"] = len(records)
    print(json.dumps(stats, ensure_ascii=False, indent=2))
    print(f"wrote {output_path}")


def iter_jsonl(path: Path):
    with path.open("r", encoding="utf-8") as file:
        for line_no, line in enumerate(file, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                yield line_no, json.loads(line)
            except json.JSONDecodeError as exc:
                raise SystemExit(f"Invalid JSON at {path}:{line_no}: {exc}") from exc


def extract_pathogen_info(payload: dict) -> dict:
    prompt_text = "\n".join(
        message.get("content", "")
        for message in payload.get("messages", [])
        if isinstance(message, dict) and isinstance(message.get("content"), str)
    )
    prompt_info = extract_prompt_pathogen_dict(prompt_text)

    species_latin = clean_text(payload.get("Latin")) or clean_text(prompt_info.get("种-拉丁名"))
    species_chinese = clean_text(payload.get("Chinese")) or clean_text(prompt_info.get("种-中文名"))
    genus_latin = clean_text(prompt_info.get("属-拉丁名")) or latin_genus(species_latin)
    genus_chinese = clean_text(prompt_info.get("属-中文名"))
    pathogen_type = clean_text(payload.get("病原类型")) or clean_text(prompt_info.get("类型"))

    aliases = build_aliases(species_latin, species_chinese, genus_latin, genus_chinese)
    return {
        "type": pathogen_type,
        "species_latin": species_latin,
        "species_latin_space": species_latin.replace("_", " ") if species_latin else "",
        "species_chinese": species_chinese,
        "genus_latin": genus_latin,
        "genus_latin_space": genus_latin.replace("_", " ") if genus_latin else "",
        "genus_chinese": genus_chinese,
        "aliases": aliases,
        "source_count": 1,
    }


def extract_prompt_pathogen_dict(text: str) -> dict:
    marker = "病原基本信息"
    pos = text.find(marker)
    if pos < 0:
        return {}
    start = text.find("{", pos)
    if start < 0:
        return {}
    depth = 0
    for index in range(start, len(text)):
        char = text[index]
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                raw = text[start : index + 1]
                try:
                    value = ast.literal_eval(raw)
                except Exception:
                    return {}
                return value if isinstance(value, dict) else {}
    return {}


def build_aliases(species_latin: str, species_chinese: str, genus_latin: str, genus_chinese: str) -> list[str]:
    aliases = [
        species_latin,
        species_latin.replace("_", " ") if species_latin else "",
        species_chinese,
        genus_latin,
        genus_latin.replace("_", " ") if genus_latin else "",
        genus_chinese,
    ]
    if species_chinese:
        aliases.extend(chinese_aliases(species_chinese))
    return unique(alias for alias in aliases if alias)


def chinese_aliases(name: str) -> list[str]:
    aliases = []
    if "氏菌" in name:
        aliases.append(name.replace("氏菌", "菌"))
    if name.endswith("菌") and "杆菌" not in name:
        aliases.append(f"{name[:-1]}杆菌")
    return aliases


def merge_record(current: dict | None, incoming: dict) -> dict:
    if current is None:
        return incoming
    merged = dict(current)
    for key in ("type", "species_latin", "species_latin_space", "species_chinese", "genus_latin", "genus_latin_space", "genus_chinese"):
        if not merged.get(key) and incoming.get(key):
            merged[key] = incoming[key]
    merged["aliases"] = unique([*merged.get("aliases", []), *incoming.get("aliases", [])])
    merged["source_count"] = int(merged.get("source_count") or 0) + int(incoming.get("source_count") or 0)
    return merged


def stable_key(info: dict) -> str:
    if info.get("species_latin"):
        return f"latin:{info['species_latin'].lower()}"
    return f"zh:{info.get('species_chinese', '').lower()}"


def latin_genus(species_latin: str) -> str:
    if not species_latin:
        return ""
    normalized = species_latin.replace(" ", "_")
    return normalized.split("_", 1)[0]


def clean_text(value) -> str:
    return "" if value is None else re.sub(r"\s+", " ", str(value)).strip().strip("'\"")


def unique(items) -> list[str]:
    seen = set()
    result = []
    for item in items:
        cleaned = clean_text(item)
        if cleaned and cleaned not in seen:
            seen.add(cleaned)
            result.append(cleaned)
    return result


if __name__ == "__main__":
    main()
