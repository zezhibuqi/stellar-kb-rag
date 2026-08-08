"""离线灌库脚本：python ingest.py --domain <domain> --path <path>

复用在线灌库流水线（tasks），支持单文件或目录批量处理。
"""

import argparse
from pathlib import Path

import tasks
from models import create_document, get_document, init_db


def main() -> None:
    parser = argparse.ArgumentParser(description="离线灌库脚本")
    parser.add_argument(
        "--domain",
        required=True,
        help="知识领域：finance/regulation/product/aftersale/common",
    )
    parser.add_argument("--path", required=True, help="Markdown 文件或目录路径")
    args = parser.parse_args()

    init_db()
    path = Path(args.path)
    files = [path] if path.is_file() else sorted(path.glob("*.md"))
    if not files:
        raise SystemExit(f"未找到 .md 文件：{args.path}")

    for file in files:
        content = tasks.read_markdown_file(str(file))
        doc_id = create_document(
            file.name, args.domain, source_content=content
        )
        print(f"[{file.name}] 提交灌库 (doc_id={doc_id})")
        tasks.submit_processing(doc_id).result()
        doc = get_document(doc_id)
        print(
            f"[{file.name}] status={doc['status']} chunk_count={doc['chunk_count']}"
            + (f" error={doc['error_message']}" if doc["error_message"] else "")
        )


if __name__ == "__main__":
    main()
