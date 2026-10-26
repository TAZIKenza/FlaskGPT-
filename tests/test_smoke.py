"""Tests de bout en bout avec un faux modèle (aucune clé API nécessaire) : `pytest`"""
import io
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import database as db  # noqa: E402
import llm  # noqa: E402
from errors import AppError  # noqa: E402


def fake_stream(text="Bonjour ! Voir [S1.1]."):
    chunks = [SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content=w))], usage=None)
              for w in (text[:7], text[7:])]
    chunks.append(SimpleNamespace(choices=[], usage=SimpleNamespace(prompt_tokens=10, completion_tokens=5, total_tokens=15)))
    return iter(chunks)


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "test.db"))
    db.init_db()
    import app as app_module
    app_module.app.config["TESTING"] = True
    monkeypatch.setattr(llm, "open_stream", lambda messages: fake_stream())
    monkeypatch.setattr(llm, "complete", lambda messages: "## Résumé\n- point **clé**")
    return app_module.app.test_client()


def ask(client, cid, text="Qu'est-ce que la complétude ?", **extra):
    r = client.post("/prompt", json={"conversation_id": cid, "prompt": text, **extra})
    return r, r.get_data(as_text=True)


def test_conversation_flow_and_metrics(client):
    cid = client.post("/api/conversations", json={"domain": "finance"}).get_json()["id"]
    r, body = ask(client, cid)
    assert r.status_code == 200
    answer, meta = body.split("\x1e")
    meta = json.loads(meta)
    assert answer.startswith("Bonjour") and meta["total_tokens"] == 15 and not meta["tokens_estimated"]
    data = client.get(f"/api/conversations/{cid}").get_json()
    assert [m["role"] for m in data["messages"]] == ["user", "assistant"]
    assert data["conversation"]["title"].startswith("Qu'est-ce")


def test_rating_stats_and_exports(client):
    cid = client.post("/api/conversations", json={"domain": "general"}).get_json()["id"]
    _, body = ask(client, cid)
    mid = json.loads(body.split("\x1e")[1])["message_id"]
    assert client.put(f"/api/messages/{mid}/rating", json={"rating": 4}).status_code == 200
    assert client.put(f"/api/messages/{mid}/rating", json={"rating": 9}).status_code == 400
    stats = client.get(f"/api/stats?conversation_id={cid}").get_json()
    assert stats["conversation"]["avg_rating"] == 4 and stats["global"]["total_tokens"] == 15
    for fmt, magic in (("txt", b"FlaskGPT"), ("json", b"{"), ("pdf", b"%PDF")):
        r = client.get(f"/api/conversations/{cid}/export?format={fmt}")
        assert r.status_code == 200 and r.data.startswith(magic)
    assert client.get(f"/api/conversations/{cid}/export?format=xml").status_code == 400
    assert client.get(f"/api/conversations/{cid}/summary.pdf").data.startswith(b"%PDF")


def test_domain_change_and_delete(client):
    cid = client.post("/api/conversations", json={"domain": "general"}).get_json()["id"]
    assert client.patch(f"/api/conversations/{cid}", json={"domain": "data"}).get_json()["domain"] == "data"
    assert client.patch(f"/api/conversations/{cid}", json={"domain": "nope"}).status_code == 400
    assert client.delete(f"/api/conversations/{cid}").status_code == 200
    assert client.get(f"/api/conversations/{cid}").status_code == 404


@pytest.mark.parametrize("code,status", [("invalid_api_key", 401), ("quota_exceeded", 429), ("connection_error", 503)])
def test_api_errors_are_friendly(client, monkeypatch, code, status):
    def boom(messages):
        raise AppError("Message clair", status, code)
    monkeypatch.setattr(llm, "open_stream", boom)
    cid = client.post("/api/conversations", json={"domain": "general"}).get_json()["id"]
    r, _ = ask(client, cid)
    assert r.status_code == status and r.get_json()["error"]["code"] == code
    assert client.get(f"/api/conversations/{cid}").get_json()["messages"] == []  # rien d'enregistré


def test_translate_openai_errors():
    httpx = pytest.importorskip("httpx")
    from openai import APIConnectionError, AuthenticationError, RateLimitError
    req = httpx.Request("POST", "http://x")
    resp = lambda c: httpx.Response(c, request=req)  # noqa: E731
    assert llm.translate_error(AuthenticationError("x", response=resp(401), body=None)).code == "invalid_api_key"
    assert llm.translate_error(RateLimitError("x", response=resp(429), body=None)).code == "quota_exceeded"
    assert llm.translate_error(APIConnectionError(request=req)).code == "connection_error"


def test_sources_are_used_and_cited(client):
    text = (ROOT / "samples" / "gouvernance_donnees.txt").read_bytes()
    r = client.post("/api/sources", data={"file": (io.BytesIO(text), "gouvernance.txt")}, content_type="multipart/form-data")
    sid = r.get_json()["id"]
    cid = client.post("/api/conversations", json={"domain": "gouvernance"}).get_json()["id"]
    _, body = ask(client, cid, "Que dit la loi 09-08 sur les données personnelles ?", source_ids=[sid], excerpts=1, max_lines=3)
    meta = json.loads(body.split("\x1e")[1])
    assert meta["sources"][0]["ref"] == "S1.1" and "09-08" in meta["sources"][0]["text"]
    assert client.post("/api/sources", data={"file": (io.BytesIO(b"x"), "a.exe")}, content_type="multipart/form-data").status_code == 400


def test_data_quality_report(client):
    csv = (ROOT / "samples" / "clients_demo.csv").read_bytes()
    r = client.post("/api/analyze", data={"file": (io.BytesIO(csv), "clients_demo.csv")}, content_type="multipart/form-data")
    rep = r.get_json()
    assert r.status_code == 200 and 0 <= rep["global_score"] < 100
    assert rep["duplicate_rows"] >= 1 and rep["missing_cells"] >= 2
    text = json.dumps(rep["columns_report"], ensure_ascii=False)
    assert "e-mail" in text and "casse" in text and "aberrante" in text and "négative" in text
    assert "nom" in rep["pii_columns"]
    assert client.post("/api/analyze", data={"file": (io.BytesIO(b"x"), "a.pdf")}, content_type="multipart/form-data").status_code == 400


def test_validation(client):
    assert client.post("/prompt", data="pas du json").status_code == 400
    assert client.post("/prompt", json={"conversation_id": 999, "prompt": "salut"}).status_code == 404
