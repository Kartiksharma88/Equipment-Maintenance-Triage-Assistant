"""Keyword/TF retrieval over the local knowledge base. Deterministic, no network."""
from __future__ import annotations

import math
import re
from collections import Counter

_TOKEN = re.compile(r"[a-z0-9]+")


def tokenize(text: str) -> list[str]:
    return _TOKEN.findall((text or "").lower())


def retrieve(query: str, sections: list[dict], equipment_type: str | None = None, top_k: int = 4) -> dict:
    """Return {results, status, message}. Never raises for empty KB; reports failure explicitly."""
    try:
        if not sections:
            return {"results": [], "status": "failure",
                    "message": "Retrieval failure: knowledge base is empty. Check data/knowledge_base.json."}
        q_tokens = tokenize(query)
        if not q_tokens:
            return {"results": [], "status": "failure",
                    "message": "Retrieval failure: empty query — describe the issue and operating events."}
        # document frequencies
        docs = [tokenize(f"{s.get('title','')} {s.get('content','')} {' '.join(s.get('fault_tags',[]))}") for s in sections]
        df = Counter()
        for d in docs:
            for t in set(d):
                df[t] += 1
        n = len(docs)
        scored = []
        for s, d in zip(sections, docs):
            tf = Counter(d)
            score = 0.0
            for t in q_tokens:
                if t in tf:
                    idf = math.log((n + 1) / (df.get(t, 0) + 1)) + 1.0
                    score += tf[t] * idf
            # boost same equipment type, small boost for fault-tag hits
            if equipment_type and s.get("equipment_type") == equipment_type:
                score *= 1.6
            tag_hits = len(set(q_tokens) & set(t.lower() for t in s.get("fault_tags", [])))
            score += tag_hits * 2.0
            scored.append((score, s))
        scored.sort(key=lambda x: x[0], reverse=True)
        top = [{"id": s["id"], "equipment_type": s["equipment_type"], "section": s["section"],
                "title": s["title"], "content": s["content"], "inspection_ref": s.get("inspection_ref", ""),
                "score": round(float(sc), 3)} for sc, s in scored[:top_k]]
        if not top or top[0]["score"] <= 0:
            return {"results": [], "status": "failure",
                    "message": "Retrieval found no relevant manual sections for this description. Try adding symptom keywords (e.g. vibration, leak, overheat, noise, pressure)."}
        return {"results": top, "status": "ok", "message": f"Retrieved {len(top)} manual section(s)."}
    except Exception as exc:  # surfaced to UI, never silent
        return {"results": [], "status": "error", "message": f"Retrieval error: {exc}"}
