"""离线检索演示：python demo_retrieve.py --question "..." --role employee"""

import argparse

from rag import RERANK_TOP_N, search_with_permission
from reranker import SiliconFlowReranker


def main() -> None:
    parser = argparse.ArgumentParser(description="离线检索演示（权限过滤 + Reranker）")
    parser.add_argument("--question", required=True, help="用户问题")
    parser.add_argument(
        "--role",
        default="admin",
        choices=["employee", "finance", "sales", "aftersale", "admin"],
        help="用户角色（按权限过滤）",
    )
    args = parser.parse_args()

    candidates = search_with_permission(args.question, args.role, k=10)
    if not candidates:
        print("未检索到结果")
        return

    top5 = SiliconFlowReranker(top_n=RERANK_TOP_N).compress_documents(
        candidates, args.question
    )
    print(f"角色 {args.role} 的 Top-{RERANK_TOP_N} 检索结果：")
    for i, doc in enumerate(top5, 1):
        meta = doc.metadata
        print(
            f"{i}. [{meta.get('domain')}] {meta.get('filename')} "
            f"(chunk {meta.get('chunk_id')})"
        )
        print("   ", doc.page_content[:120].replace("\n", " "))


if __name__ == "__main__":
    main()
