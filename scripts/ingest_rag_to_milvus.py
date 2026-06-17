import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from model.RAG.milvus_store import ingest_directory_to_milvus


def main():
    parser = argparse.ArgumentParser(description="Embed knowledge-base files into Milvus.")
    parser.add_argument(
        "--rebuild",
        action="store_true",
        help="Drop the Milvus collection and rebuild the whole knowledge base.",
    )
    parser.add_argument(
        "--check-hash",
        action="store_true",
        help="Compute content hashes even when file size and mtime are unchanged.",
    )
    args = parser.parse_args()

    stats = ingest_directory_to_milvus(rebuild=args.rebuild, check_hash=args.check_hash)
    print(
        "Milvus ingest finished: "
        f"{stats['inserted_files']} files inserted, "
        f"{stats['skipped_files']} files skipped, "
        f"{stats['inserted_chunks']} chunks inserted."
    )


if __name__ == "__main__":
    main()
