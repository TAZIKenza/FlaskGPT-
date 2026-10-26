"""Fonctionnalité 3 : base SQLite (module sqlite3 de Python, aucune installation)."""
import json
import os
import sqlite3
from datetime import datetime

from flask import g

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "flaskgpt.db")
DEFAULT_TITLE = "Nouvelle conversation"

SCHEMA = """
CREATE TABLE IF NOT EXISTS conversations (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    title      TEXT NOT NULL DEFAULT 'Nouvelle conversation',
    domain     TEXT NOT NULL DEFAULT 'general',
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS messages (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id   INTEGER NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    role              TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
    content           TEXT NOT NULL,
    created_at        TEXT NOT NULL,
    response_time_ms  INTEGER,
    prompt_tokens     INTEGER,
    completion_tokens INTEGER,
    total_tokens      INTEGER,
    tokens_estimated  INTEGER NOT NULL DEFAULT 0,
    rating            INTEGER CHECK (rating BETWEEN 1 AND 5),
    sources_json      TEXT
);
CREATE TABLE IF NOT EXISTS sources (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    name       TEXT NOT NULL,
    content    TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS dataset_reports (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    filename    TEXT NOT NULL,
    global_score REAL NOT NULL,
    report_json TEXT NOT NULL,
    created_at  TEXT NOT NULL
);
"""


def now():
    return datetime.now().isoformat(timespec="seconds")


def connect():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row            # accès aux colonnes par leur nom
    conn.execute("PRAGMA foreign_keys = ON")  # active ON DELETE CASCADE
    return conn


def get_db():
    """Une connexion par requête Flask, stockée dans g puis fermée par close_db."""
    if "db" not in g:
        g.db = connect()
    return g.db


def close_db(_exc=None):
    conn = g.pop("db", None)
    if conn is not None:
        conn.close()


def init_db():
    conn = connect()
    conn.executescript(SCHEMA)
    conn.commit()
    conn.close()


def _c(conn):
    return conn or get_db()


# ---------- conversations ----------
def create_conversation(domain, conn=None):
    conn = _c(conn)
    cur = conn.execute("INSERT INTO conversations (title, domain, created_at) VALUES (?, ?, ?)",
                       (DEFAULT_TITLE, domain, now()))
    conn.commit()
    return cur.lastrowid


def list_conversations():
    rows = _c(None).execute("SELECT * FROM conversations ORDER BY id DESC").fetchall()
    return [dict(r) for r in rows]


def get_conversation(cid, conn=None):
    row = _c(conn).execute("SELECT * FROM conversations WHERE id = ?", (cid,)).fetchone()
    return dict(row) if row else None


def update_conversation(cid, **fields):
    allowed = {k: v for k, v in fields.items() if k in ("title", "domain")}
    if not allowed:
        return
    conn = _c(None)
    sets = ", ".join(f"{k} = ?" for k in allowed)  # noms de colonnes en liste blanche, valeurs paramétrées
    conn.execute(f"UPDATE conversations SET {sets} WHERE id = ?", (*allowed.values(), cid))
    conn.commit()


def delete_conversation(cid):
    conn = _c(None)
    conn.execute("DELETE FROM conversations WHERE id = ?", (cid,))  # supprime aussi les messages
    conn.commit()


# ---------- messages ----------
def _message(row):
    d = dict(row)
    d["sources"] = json.loads(d.pop("sources_json") or "[]")
    return d


def get_messages(cid, conn=None):
    rows = _c(conn).execute("SELECT * FROM messages WHERE conversation_id = ? ORDER BY id", (cid,)).fetchall()
    return [_message(r) for r in rows]


def add_message(cid, role, content, conn=None, sources=None, **metrics):
    conn = _c(conn)
    cur = conn.execute(
        """INSERT INTO messages (conversation_id, role, content, created_at, response_time_ms,
               prompt_tokens, completion_tokens, total_tokens, tokens_estimated, sources_json)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (cid, role, content, now(), metrics.get("response_time_ms"), metrics.get("prompt_tokens"),
         metrics.get("completion_tokens"), metrics.get("total_tokens"),
         int(metrics.get("tokens_estimated", 0)), json.dumps(sources or [], ensure_ascii=False)))
    conn.commit()
    return cur.lastrowid


def set_rating(mid, rating):
    conn = _c(None)
    cur = conn.execute("UPDATE messages SET rating = ? WHERE id = ? AND role = 'assistant'", (rating, mid))
    conn.commit()
    return cur.rowcount > 0


# ---------- sources ----------
def add_source(name, content):
    conn = _c(None)
    cur = conn.execute("INSERT INTO sources (name, content, created_at) VALUES (?, ?, ?)", (name, content, now()))
    conn.commit()
    return cur.lastrowid


def list_sources():
    rows = _c(None).execute(
        "SELECT id, name, LENGTH(content) AS chars, created_at FROM sources ORDER BY id DESC").fetchall()
    return [dict(r) for r in rows]


def get_sources(ids):
    if not ids:
        return []
    marks = ",".join("?" * len(ids))
    rows = _c(None).execute(f"SELECT * FROM sources WHERE id IN ({marks})", ids).fetchall()
    by_id = {r["id"]: dict(r) for r in rows}
    return [by_id[i] for i in ids if i in by_id]  # on garde l'ordre demandé


def delete_source(sid):
    conn = _c(None)
    conn.execute("DELETE FROM sources WHERE id = ?", (sid,))
    conn.commit()


# ---------- rapports de qualité ----------
def add_report(filename, score, report):
    conn = _c(None)
    conn.execute("INSERT INTO dataset_reports (filename, global_score, report_json, created_at) VALUES (?, ?, ?, ?)",
                 (filename, score, json.dumps(report, ensure_ascii=False), now()))
    conn.commit()


# ---------- statistiques (fonctionnalité 8) ----------
def summarize(assistant_messages):
    msgs = assistant_messages
    times = [m["response_time_ms"] for m in msgs if m["response_time_ms"] is not None]
    ratings = [m["rating"] for m in msgs if m["rating"]]
    return {
        "answers": len(msgs),
        "prompt_tokens": sum(m["prompt_tokens"] or 0 for m in msgs),
        "completion_tokens": sum(m["completion_tokens"] or 0 for m in msgs),
        "total_tokens": sum(m["total_tokens"] or 0 for m in msgs),
        "avg_time_ms": round(sum(times) / len(times)) if times else None,
        "avg_rating": round(sum(ratings) / len(ratings), 2) if ratings else None,
        "rated": len(ratings),
    }


def assistant_messages(cid=None):
    if cid is None:
        rows = _c(None).execute("SELECT * FROM messages WHERE role = 'assistant' ORDER BY id").fetchall()
    else:
        rows = _c(None).execute("SELECT * FROM messages WHERE role = 'assistant' AND conversation_id = ? ORDER BY id",
                                (cid,)).fetchall()
    return [_message(r) for r in rows]
