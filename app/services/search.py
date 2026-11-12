from collections import defaultdict
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.llm.service import LLMService
from app.models.knowledge import KnowledgeChunk, KnowledgeDocument
from app.schemas.knowledge import SearchRequest, SearchResult


def _conditions(request: SearchRequest, owner_id: int) -> tuple[list[Any], dict[str, Any]]:
    conditions: list[Any] = [KnowledgeDocument.owner_id == owner_id]
    params: dict[str, Any] = {"owner_id": owner_id}
    if request.service_id is not None:
        conditions.append(KnowledgeDocument.service_id == request.service_id)
    if request.repository_id is not None:
        conditions.append(KnowledgeDocument.repository_id == request.repository_id)
    if request.document_id is not None:
        conditions.append(KnowledgeDocument.id == request.document_id)
    if request.incident_id is not None:
        conditions.append(KnowledgeDocument.incident_id == request.incident_id)
    return conditions, params


async def search_chunks(db: AsyncSession, *, owner_id: int, request: SearchRequest, llm: LLMService) -> list[SearchResult]:
    conditions, _ = _conditions(request, owner_id)
    rankings: dict[int, dict[str, Any]] = defaultdict(dict)
    if db.bind is not None and db.bind.dialect.name == "postgresql":
        lexical_query = text("""
            SELECT c.id, c.document_id, c.chunk_index, c.content, c.metadata_json,
                   d.title, d.source_type, d.source_uri,
                   ts_rank_cd(c.search_vector, plainto_tsquery('english', :query)) AS score
            FROM knowledge_chunks c JOIN knowledge_documents d ON d.id = c.document_id
            WHERE d.owner_id = :owner_id
              AND c.search_vector @@ plainto_tsquery('english', :query)
              AND (:service_id IS NULL OR d.service_id = :service_id)
              AND (:repository_id IS NULL OR d.repository_id = :repository_id)
              AND (:document_id IS NULL OR d.id = :document_id)
              AND (:incident_id IS NULL OR d.incident_id = :incident_id)
            ORDER BY score DESC
            LIMIT :limit
        """)
        lexical_params = {"query": request.query, "owner_id": owner_id, "service_id": request.service_id, "repository_id": request.repository_id, "document_id": request.document_id, "incident_id": request.incident_id, "limit": request.top_k * 3}
        for rank, row in enumerate((await db.execute(lexical_query, lexical_params)).mappings().all(), start=1):
            rankings[row["id"]].update(row=row, lexical_rank=rank, lexical_score=float(row["score"] or 0.0))
    else:
        lexical_query = select(KnowledgeChunk, KnowledgeDocument).join(KnowledgeDocument).where(*conditions, KnowledgeChunk.content.ilike(f"%{request.query}%")).limit(request.top_k * 3)
        for rank, (chunk, document) in enumerate((await db.execute(lexical_query)).all(), start=1):
            rankings[chunk.id].update(chunk=chunk, document=document, lexical_rank=rank, lexical_score=1.0 / rank)

    if db.bind is not None and db.bind.dialect.name == "postgresql":
        vector = await llm.embed(request.query)
        vector_literal = "[" + ",".join(str(value) for value in vector) + "]"
        where = ["d.owner_id = :owner_id"]
        params: dict[str, Any] = {"owner_id": owner_id, "embedding": vector_literal, "limit": request.top_k * 3}
        if request.service_id is not None:
            where.append("d.service_id = :service_id")
            params["service_id"] = request.service_id
        if request.repository_id is not None:
            where.append("d.repository_id = :repository_id")
            params["repository_id"] = request.repository_id
        if request.document_id is not None:
            where.append("d.id = :document_id")
            params["document_id"] = request.document_id
        if request.incident_id is not None:
            where.append("d.incident_id = :incident_id")
            params["incident_id"] = request.incident_id
        semantic_query = text(f"""
            SELECT c.id, c.document_id, c.chunk_index, c.content, c.metadata_json,
                   d.title, d.source_type, d.source_uri,
                   1 - (c.embedding <=> CAST(:embedding AS vector)) AS score
            FROM knowledge_chunks c JOIN knowledge_documents d ON d.id = c.document_id
            WHERE {' AND '.join(where)} AND c.embedding IS NOT NULL
            ORDER BY c.embedding <=> CAST(:embedding AS vector)
            LIMIT :limit
        """)
        for rank, row in enumerate((await db.execute(semantic_query, params)).mappings().all(), start=1):
            entry = rankings[row["id"]]
            entry.update(row=row, semantic_rank=rank, semantic_score=float(row["score"] or 0.0))

    results: list[tuple[float, dict[str, Any]]] = []
    for item in rankings.values():
        rrf = 0.0
        if "lexical_rank" in item:
            rrf += 1.0 / (60 + item["lexical_rank"])
        if "semantic_rank" in item:
            rrf += 1.0 / (60 + item["semantic_rank"])
        results.append((rrf, item))
    results.sort(key=lambda pair: pair[0], reverse=True)
    output: list[SearchResult] = []
    for score, item in results[: request.top_k]:
        chunk = item.get("chunk")
        document = item.get("document")
        row = item.get("row")
        output.append(SearchResult(
            chunk_id=chunk.id if chunk else row["id"],
            document_id=chunk.document_id if chunk else row["document_id"],
            title=document.title if document else row["title"],
            source_type=document.source_type if document else row["source_type"],
            source_uri=document.source_uri if document else row["source_uri"],
            chunk_index=chunk.chunk_index if chunk else row["chunk_index"],
            content=chunk.content if chunk else row["content"],
            score=score,
            citation=f"[{document.title if document else row['title']}#{chunk.chunk_index if chunk else row['chunk_index']}]",
        ))
    return output
