"""FlaskGPT - routes Flask. La logique est rangée dans les modules : database, llm, sources,
data_quality, exports."""
import io
import json
import time

from flask import Flask, Response, jsonify, render_template, request, send_file
from werkzeug.exceptions import HTTPException

import data_quality
import database as db
import exports
import llm
import sources as src
from errors import AppError

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 16 * 1024 * 1024  # 16 Mo max par fichier importé
app.teardown_appcontext(db.close_db)
db.init_db()

META_SEP = "\x1e"  # séparateur : texte de la réponse | JSON de métadonnées envoyé à la fin du flux


# ---------------------------------------------------------------- erreurs (fonctionnalité 4)
@app.errorhandler(AppError)
def on_app_error(e):
    return jsonify(error={"code": e.code, "message": e.message}), e.status


@app.errorhandler(Exception)
def on_any_error(e):
    if isinstance(e, HTTPException):
        message = "Fichier trop volumineux (16 Mo maximum)." if e.code == 413 else e.description
        if request.path.startswith(("/api", "/prompt")):
            return jsonify(error={"code": e.name, "message": message}), e.code
        return e
    app.logger.exception("Erreur non gérée")
    return jsonify(error={"code": "unexpected", "message": "Erreur interne du serveur."}), 500


# ---------------------------------------------------------------- helpers
def _json():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        raise AppError("Le corps de la requête doit être un objet JSON.", 400, "bad_request")
    return data


def _int(value, default, lo, hi):
    try:
        value = int(value)
    except (TypeError, ValueError):
        value = default
    return max(lo, min(hi, value))


def _domain(value):
    if value not in llm.DOMAINS:
        raise AppError("Domaine inconnu.", 400, "bad_domain")
    return value


def _conversation(cid):
    conv = db.get_conversation(cid)
    if not conv:
        raise AppError("Conversation introuvable.", 404, "not_found")
    conv["domain_label"] = llm.DOMAINS.get(conv["domain"], {}).get("label", conv["domain"])
    return conv


# ---------------------------------------------------------------- pages
@app.route("/")
def home():
    return render_template("index.html", domains=llm.DOMAINS)


# ---------------------------------------------------------------- conversations (fonctionnalités 1, 2, 3)
@app.get("/api/conversations")
def list_conversations():
    out = db.list_conversations()
    for c in out:
        c["domain_label"] = llm.DOMAINS.get(c["domain"], {}).get("label", c["domain"])
    return jsonify(out)


@app.post("/api/conversations")
def create_conversation():
    domain = _domain(_json().get("domain", "general"))
    return jsonify(_conversation(db.create_conversation(domain))), 201


@app.get("/api/conversations/<int:cid>")
def get_conversation(cid):
    return jsonify(conversation=_conversation(cid), messages=db.get_messages(cid))


@app.patch("/api/conversations/<int:cid>")
def patch_conversation(cid):
    _conversation(cid)
    data = _json()
    if "domain" in data:
        db.update_conversation(cid, domain=_domain(data["domain"]))
    return jsonify(_conversation(cid))


@app.delete("/api/conversations/<int:cid>")
def delete_conversation(cid):
    _conversation(cid)
    db.delete_conversation(cid)
    return jsonify(deleted=cid)


# ---------------------------------------------------------------- chat en streaming (cœur de l'app)
@app.post("/prompt")
def prompt():
    data = _json()
    text = str(data.get("prompt", "")).strip()
    if not text:
        raise AppError("Le message est vide.", 400, "empty_prompt")
    if len(text) > 8000:
        raise AppError("Le message est trop long (8000 caractères maximum).", 400, "prompt_too_long")
    conv = _conversation(_int(data.get("conversation_id"), 0, 0, 10**9))
    cid = conv["id"]

    # Fonctionnalité 10 : passages des sources cochées, au nombre d'extraits / lignes choisi
    ids = [i for i in data.get("source_ids", []) if isinstance(i, int)]
    passages = src.retrieve(db.get_sources(ids), text,
                            per_source=_int(data.get("excerpts"), 2, 1, 5),
                            max_lines=_int(data.get("max_lines"), 6, 0, 30)) if ids else []

    history = db.get_messages(cid)[-20:]  # on limite le contexte envoyé au modèle
    messages = ([{"role": "system", "content": llm.build_system_prompt(conv["domain"], passages)}]
                + [{"role": m["role"], "content": m["content"]} for m in history]
                + [{"role": "user", "content": text}])

    started = time.perf_counter()
    stream = llm.open_stream(messages)  # lève AppError (401/429/503...) AVANT de commencer à streamer

    db.add_message(cid, "user", text)
    if conv["title"] == db.DEFAULT_TITLE:
        db.update_conversation(cid, title=text[:50])
    title = conv["title"] if conv["title"] != db.DEFAULT_TITLE else text[:50]
    prompt_chars = sum(len(m["content"]) for m in messages)

    def generate():
        parts, usage = [], None
        try:
            for kind, value in llm.iter_stream(stream):
                if kind == "text":
                    parts.append(value)
                    yield value
                else:
                    usage = value
        except Exception as exc:  # erreur survenue en plein milieu du flux
            err = llm.translate_error(exc)
            yield META_SEP + json.dumps({"error": {"code": err.code, "message": err.message}})
            return
        answer = "".join(parts)
        if not answer.strip():
            yield META_SEP + json.dumps({"error": {"code": "empty_answer",
                                                   "message": "Le modèle n'a renvoyé aucune réponse. Réessaie."}})
            return
        elapsed_ms = round((time.perf_counter() - started) * 1000)
        estimated = usage is None  # si l'API ne donne pas les tokens : estimation ~4 caractères/token
        if estimated:
            p, c = prompt_chars // 4, len(answer) // 4
            usage = {"prompt_tokens": p, "completion_tokens": c, "total_tokens": p + c}
        conn = db.connect()  # le contexte Flask n'est plus garanti ici : connexion dédiée
        try:
            mid = db.add_message(cid, "assistant", answer, conn=conn, sources=passages,
                                 response_time_ms=elapsed_ms, tokens_estimated=estimated, **usage)
        finally:
            conn.close()
        yield META_SEP + json.dumps({"message_id": mid, "response_time_ms": elapsed_ms, "tokens_estimated": estimated,
                                     "rating": None, "sources": passages, "conversation": {"id": cid, "title": title},
                                     **usage}, ensure_ascii=False)

    return Response(generate(), content_type="text/plain; charset=utf-8",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# ---------------------------------------------------------------- notes (fonctionnalité 9)
@app.put("/api/messages/<int:mid>/rating")
def rate_message(mid):
    rating = _json().get("rating")
    if rating is not None and (not isinstance(rating, int) or not 1 <= rating <= 5):
        raise AppError("La note doit être un entier entre 1 et 5.", 400, "bad_rating")
    if not db.set_rating(mid, rating):
        raise AppError("Message introuvable.", 404, "not_found")
    return jsonify(id=mid, rating=rating)


# ---------------------------------------------------------------- performances (fonctionnalité 8)
@app.get("/api/stats")
def stats():
    cid = request.args.get("conversation_id", type=int)
    result = {"global": {**db.summarize(db.assistant_messages()), "conversations": len(db.list_conversations())},
              "conversation": None}
    if cid:
        _conversation(cid)
        msgs = db.assistant_messages(cid)
        result["conversation"] = {**db.summarize(msgs), "messages": [
            {"id": m["id"], "response_time_ms": m["response_time_ms"], "total_tokens": m["total_tokens"],
             "completion_tokens": m["completion_tokens"], "tokens_estimated": m["tokens_estimated"],
             "rating": m["rating"]} for m in msgs]}
    return jsonify(result)


# ---------------------------------------------------------------- export et résumé PDF (fonctionnalités 5 et 6)
@app.get("/api/conversations/<int:cid>/export")
def export_conversation(cid):
    conv, msgs = _conversation(cid), db.get_messages(cid)
    if not msgs:
        raise AppError("Cette conversation est vide, il n'y a rien à exporter.", 400, "empty")
    fmt = request.args.get("format", "txt").lower()
    if fmt == "txt":
        data, mime = exports.to_txt(conv, msgs).encode("utf-8"), "text/plain; charset=utf-8"
    elif fmt == "json":
        stats_ = db.summarize([m for m in msgs if m["role"] == "assistant"])
        data, mime = exports.to_json(conv, msgs, stats_).encode("utf-8"), "application/json"
    elif fmt == "pdf":
        data, mime = exports.to_pdf_transcript(conv, msgs), "application/pdf"
    else:
        raise AppError("Format d'export inconnu (txt, json ou pdf).", 400, "bad_format")
    return send_file(io.BytesIO(data), mimetype=mime, as_attachment=True, download_name=f"conversation-{cid}.{fmt}")


@app.get("/api/conversations/<int:cid>/summary.pdf")
def summary_pdf(cid):
    conv, msgs = _conversation(cid), db.get_messages(cid)
    if not msgs:
        raise AppError("Cette conversation est vide, il n'y a rien à résumer.", 400, "empty")
    transcript = exports.to_txt(conv, msgs)[-12000:]
    summary = llm.complete([
        {"role": "system", "content": "Résume la conversation suivante en français : un court paragraphe, "
                                      "puis 'Points clés' en puces, puis 'Décisions / suite' si pertinent."},
        {"role": "user", "content": transcript}])
    stats_ = db.summarize([m for m in msgs if m["role"] == "assistant"])
    pdf = exports.to_pdf_summary(conv, msgs, summary or "(résumé indisponible)", stats_)
    return send_file(io.BytesIO(pdf), mimetype="application/pdf", as_attachment=True,
                     download_name=f"resume-conversation-{cid}.pdf")


# ---------------------------------------------------------------- sources (fonctionnalité 10)
@app.get("/api/sources")
def list_sources():
    return jsonify(db.list_sources())


@app.post("/api/sources")
def upload_source():
    f = request.files.get("file")
    if not f or not f.filename:
        raise AppError("Aucun fichier reçu.", 400, "no_file")
    text = src.extract_text(f.filename, f.read())
    return jsonify(id=db.add_source(f.filename, text), name=f.filename, chars=len(text)), 201


@app.delete("/api/sources/<int:sid>")
def delete_source(sid):
    db.delete_source(sid)
    return jsonify(deleted=sid)


# ---------------------------------------------------------------- qualité des données (fonctionnalité 7)
@app.post("/api/analyze")
def analyze_dataset():
    f = request.files.get("file")
    if not f or not f.filename:
        raise AppError("Aucun fichier reçu.", 400, "no_file")
    report = data_quality.analyze(data_quality.read_dataframe(f.filename, f.read()), f.filename)
    db.add_report(f.filename, report["global_score"], report)
    return jsonify(report)


if __name__ == "__main__":
    app.run(debug=True)
