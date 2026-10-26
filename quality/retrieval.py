from .http_control import client as controlled_client
import math

from sqlalchemy import select

from . import models as m
from .security import aware, current_time, digest, endpoint, fail
from .service import serialize


def embed(settings, text, category='approved_knowledge'):
    if not settings.embedding_url or not settings.model_approved:
        fail(409, "embedding_not_ready", "向量模型未配置或未获批准；关键词检索继续可用")
    if category not in settings.model_allowed_data:
        fail(409, 'model_data_policy', '客户尚未允许向此模型发送该类检索文本；关键词检索仍可用')
    endpoint(settings.embedding_url, settings)
    from .model_journal import model_slot
    with model_slot(settings), controlled_client(settings, settings.embedding_url, settings.model_timeout) as client:
        response = client.post(
            settings.embedding_url.rstrip("/") + "/embeddings",
            headers={"Authorization": "Bearer " + settings.model_api_key} if settings.model_api_key else {},
            json={"model": settings.embedding_model, "input": text[:20000]},
        )
        response.raise_for_status()
        vector = response.json()["data"][0]["embedding"]
    if len(vector) != 384 or any(not isinstance(v, (float, int)) or not math.isfinite(v) for v in vector):
        fail(422, "embedding_schema", "向量必须是384维有限数值；其他维数须经受控迁移")
    return vector


def search(service, query, process, product_id="", use_vector=False, line_id="", context=None, site_id="", entry_id=""):
    from .scopes import applicable
    if site_id:
        service.actor.require('read', site_id)
    if line_id:
        line = service.get(m.Master, line_id)
        if line.kind != 'line' or site_id and line.site_id != site_id:
            fail(422, 'filter_line', '检索产线与工厂不一致')
    settings = service.settings.model_copy(update={'site_id': site_id}) if site_id else service.settings
    queue = service.scoped(m.Knowledge).where(m.Knowledge.status == "published")
    if entry_id:
        queue = queue.where(m.Knowledge.id == entry_id)
    rows = service.db.scalars(queue).all()
    allowed = [
        r
        for r in rows
        if aware(r.valid_until) >= current_time()
        and applicable(r.scope, settings, process, line_id, {**(context or {}), 'product_id': product_id})
    ]
    results = []
    terms = list(dict.fromkeys(query.lower().split()))
    for row in allowed:
        content = (row.title + " " + row.content).lower()
        score = sum(content.count(term) for term in terms if term)
        if score:
            results.append({"entry": serialize(row), "keyword_matches": score, "retrieval_similarity": None})
    if use_vector:
        if service.db.bind.dialect.name != "postgresql":
            fail(409, "pgvector_required", "向量检索需要 PostgreSQL；开发环境可使用关键词检索")
        vector = embed(service.settings, query, 'embedding_query')
        ids = [r.id for r in allowed]
        distance = m.KnowledgeVector.embedding.cosine_distance(vector)
        q = (
            select(m.KnowledgeVector, distance.label("distance"))
            .where(
                m.KnowledgeVector.tenant_id == service.settings.tenant_id,
                m.KnowledgeVector.site_id.in_(["", *service.actor.sites]),
                m.KnowledgeVector.knowledge_id.in_(ids),
            )
            .order_by(distance)
            .limit(20)
        )
        by_id = {r.id: r for r in allowed}
        for row, dist in service.db.execute(q):
            if row.content_hash != digest(by_id[row.knowledge_id].content):
                continue
            prior = next((r for r in results if r["entry"]["id"] == row.knowledge_id), None)
            if prior:
                prior["retrieval_similarity"] = 1 - float(dist)
            else:
                results.append(
                    {
                        "entry": serialize(by_id[row.knowledge_id]),
                        "keyword_matches": 0,
                        "retrieval_similarity": 1 - float(dist),
                    }
                )
    return sorted(
        results, key=lambda r: (r["keyword_matches"], r["retrieval_similarity"] or 0), reverse=True
    )[:20]
