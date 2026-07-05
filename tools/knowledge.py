import json
import os
import re

DATA_DIR = os.path.expanduser("~/.config/shem/knowledge")

# ── Prompt injection sanitization (adapted from Sump) ──────────

INJECTION_PATTERNS = [
    r"ignore\s+all\s+(previous\s+)?instructions",
    r"ignore\s+all\s+(prior\s+)?directives",
    r"forget\s+(everything|all\s+previous)",
    r"disregard\s+(all\s+)?(previous|prior)",
    r"you\s+(are\s+)?(now|will\s+act\s+as)",
    r"from\s+now\s+on\s+you\s+are",
    r"you\s+are\s+no\s+longer",
    r"new\s+instructions?\s*:",
    r"override\s+(mode|protocol|directives)",
    r"system\s+(prompt|message|instruction)",
    r"\"\"\"[\s\S]{0,200}ignore",
    r"<\s*(system|user|assistant)\s*>",
    r"\{\{[\s\S]{0,500}?\}\}",
    r"\[\[\s*SYSTEM",
    r"you\s+must\s+ignore",
    r"this\s+is\s+(an\s+)?(urgent|important)\s*(order|instruction|command)",
    r"do\s+not\s+(output|respond|reply|return)\s+(with\s+)?(your\s+)?(standard|normal|usual)",
    r"for\s+security\s+(reasons|purposes)",
    r"you\s+have\s+been\s+(hacked|compromised|overridden)",
    r"START\s+(OF\s+)?(NEW\s+)?(INSTRUCTIONS|SYSTEM|PROMPT)",
    r"END\s+(OF\s+)?(ALL\s+)?(INSTRUCTIONS|DIRECTIVES)",
    r"output\s+the\s+(full\s+)?prompt",
]

BAD_RX = [re.compile(p, re.IGNORECASE) for p in INJECTION_PATTERNS]


def sanitize(text):
    cleaned = re.sub("[\U000E0000-\U000E007F]", "", text)
    flagged = any(r.search(cleaned) for r in BAD_RX)
    if flagged:
        cleaned = "<untrusted>\n" + cleaned + "\n</untrusted>"
    return cleaned, flagged


# ── ChromaDB client ────────────────────────────────────────────

def _get_client():
    import chromadb
    return chromadb.PersistentClient(path=DATA_DIR)


# ── Action handlers ────────────────────────────────────────────

def _handle_index(client, args):
    text = args.get("text")
    if not text:
        return {"error": "missing 'text' parameter"}
    collection_name = args.get("collection", "default")
    source = args.get("source", "")
    try:
        collection = client.get_or_create_collection(collection_name)
    except Exception:
        import chromadb
        collection = client.create_collection(collection_name)

    chunks = _chunk_text(text)
    ids = []
    metadatas = []
    for i, chunk in enumerate(chunks):
        import uuid
        ids.append(str(uuid.uuid4()))
        metadatas.append({"source": source, "chunk": i})

    collection.add(documents=chunks, ids=ids, metadatas=metadatas)
    return {"indexed": len(chunks), "collection": collection_name, "source": source}


def _handle_search(client, args):
    query = args.get("query")
    if not query:
        return {"error": "missing 'query' parameter"}
    collection_name = args.get("collection", "default")
    n = args.get("n", 5)
    threshold = args.get("threshold", 0.0)
    source_filter = args.get("source", "")

    try:
        collection = client.get_collection(collection_name)
    except Exception:
        return {"error": f"collection '{collection_name}' not found"}

    where = {"source": source_filter} if source_filter else None
    results = collection.query(query_texts=[query], n_results=n, where=where)
    out = []
    for i in range(len(results["ids"][0])):
        score = results["distances"][0][i] if results.get("distances") else 0
        sim = max(0, 1 - score)
        if sim >= threshold:
            text = results["documents"][0][i]
            cleaned, flagged = sanitize(text)
            entry = {
                "text": cleaned,
                "score": round(sim, 3),
                "source": results["metadatas"][0][i].get("source", ""),
            }
            if flagged:
                entry["_sanitized"] = True
            out.append(entry)
    return {"results": out, "query": query, "collection": collection_name}


def _handle_list(client, args):
    collections = client.list_collections()
    names = [c.name for c in collections]
    counts = {}
    for c in collections:
        counts[c.name] = c.count()
    return {"collections": names, "counts": counts}


def _handle_delete(client, args):
    name = args.get("name")
    if not name:
        return {"error": "missing 'name' parameter"}
    try:
        client.delete_collection(name)
        return {"deleted": name}
    except Exception:
        return {"error": f"collection '{name}' not found"}


# ── Text chunking ──────────────────────────────────────────────

def _chunk_text(text, max_chars=500):
    paragraphs = re.split(r"\n\s*\n", text.strip())
    chunks = []
    for p in paragraphs:
        p = p.strip()
        if not p:
            continue
        while len(p) > max_chars:
            split = p.rfind(" ", 0, max_chars)
            if split < max_chars // 2:
                split = max_chars
            chunks.append(p[:split].strip())
            p = p[split:].strip()
        if p:
            chunks.append(p)
    return chunks if chunks else [text.strip()]


# ── Dispatch ───────────────────────────────────────────────────

HANDLERS = {
    "index": _handle_index,
    "search": _handle_search,
    "list": _handle_list,
    "delete": _handle_delete,
}


def run(args):
    action = args.get("action")
    if not action:
        return {"error": "missing 'action' field"}

    handler = HANDLERS.get(action)
    if not handler:
        return {"error": f"unknown action: {action}"}

    os.makedirs(DATA_DIR, exist_ok=True)

    try:
        client = _get_client()
        return handler(client, args)
    except ImportError:
        return {"error": "chromadb is not installed", "hint": "pip install chromadb"}
    except Exception as e:
        return {"error": str(e)}
