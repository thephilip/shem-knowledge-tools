import json
import os
import re

DATA_DIR = os.path.expanduser("~/.config/shem/knowledge")


def _get_client():
    import chromadb
    return chromadb.PersistentClient(path=DATA_DIR)


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

    try:
        collection = client.get_collection(collection_name)
    except Exception:
        return {"error": f"collection '{collection_name}' not found"}

    results = collection.query(query_texts=[query], n_results=n)
    out = []
    for i in range(len(results["ids"][0])):
        score = results["distances"][0][i] if results.get("distances") else 0
        sim = max(0, 1 - score)
        if sim >= threshold:
            out.append({
                "text": results["documents"][0][i],
                "score": round(sim, 3),
                "source": results["metadatas"][0][i].get("source", ""),
            })
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
