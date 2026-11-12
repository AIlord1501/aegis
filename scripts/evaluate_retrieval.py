"""Run a reproducible lightweight retrieval evaluation over a JSON corpus.

Usage:
    python scripts/evaluate_retrieval.py --corpus path/to/corpus.json

The corpus entries must contain `source`, `text`, and optional `metadata` fields.
This evaluator reports retrieval metrics without requiring an LLM, so it is safe
to run in CI. Model latency/token metrics are collected by `model_calls` in the
application database and are intentionally reported separately.
"""
import argparse
import json
import re
from pathlib import Path


def tokens(text: str) -> set[str]:
    return {token for token in re.findall(r"[a-z0-9_]+", text.lower()) if len(token) > 2}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--corpus", type=Path, required=True)
    parser.add_argument("--k", type=int, default=5)
    args = parser.parse_args()
    data = json.loads(args.corpus.read_text(encoding="utf-8"))
    cases = data["cases"]
    corpus = data["corpus"]
    total_precision = total_recall = total_relevance = total_citations = 0.0
    for case in cases:
        query_tokens = tokens(case["query"])
        ranked = sorted(corpus, key=lambda item: len(query_tokens & tokens(item["text"])), reverse=True)
        top = ranked[: args.k]
        relevant = set(case.get("relevant_sources", []))
        returned = {item["source"] for item in top}
        hits = len(returned & relevant)
        total_precision += hits / max(len(top), 1)
        total_recall += hits / max(len(relevant), 1)
        total_relevance += sum(bool(query_tokens & tokens(item["text"])) for item in top) / max(len(top), 1)
        total_citations += sum(item["source"] in relevant for item in top) / max(len(top), 1)
    count = max(len(cases), 1)
    print(json.dumps({"cases": len(cases), "precision_at_k": total_precision / count, "recall_at_k": total_recall / count, "context_relevance": total_relevance / count, "citation_correctness": total_citations / count, "llm_latency_ms": None, "llm_tokens": None}, indent=2))


if __name__ == "__main__":
    main()
