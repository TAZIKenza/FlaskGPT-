"""Fonctionnalité 10 : extraction de texte, découpage en passages et recherche par mots-clés."""
import io
import re
import textwrap
import unicodedata

from errors import AppError

STOPWORDS = set("les des une aux que qui quoi dans sur pour par avec est sont the and for with are what how "
                "pas plus mais comme cette ces son ses leur nous vous elle ils".split())


def _normalize(text):
    """minuscules + suppression des accents : 'Qualité' -> 'qualite'."""
    text = unicodedata.normalize("NFD", text.lower())
    return "".join(c for c in text if unicodedata.category(c) != "Mn")


def tokens(text):
    return [w for w in re.findall(r"[a-z0-9]{3,}", _normalize(text)) if w not in STOPWORDS]


def extract_text(filename, raw):
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if ext in {"txt", "md", "csv", "json"}:
        try:
            text = raw.decode("utf-8-sig")
        except UnicodeDecodeError:
            text = raw.decode("latin-1")
    elif ext == "pdf":
        try:
            from pypdf import PdfReader
            text = "\n\n".join((p.extract_text() or "") for p in PdfReader(io.BytesIO(raw)).pages)
        except Exception as exc:
            raise AppError("Impossible de lire ce PDF (fichier corrompu ou protégé).", 400, "bad_pdf") from exc
    else:
        raise AppError("Format non supporté. Utilise .txt, .md, .csv, .json ou .pdf.", 400, "bad_format")
    if not text.strip():
        raise AppError("Aucun texte lisible dans ce fichier (PDF scanné ?).", 400, "empty_file")
    return text


def chunk_text(text, max_chars=900):
    """Découpe en paragraphes ; les trop longs sont recoupés par phrases."""
    chunks = []
    for para in (p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()):
        if len(para) <= max_chars:
            chunks.append(para)
            continue
        current = ""
        for piece in re.split(r"(?<=[.!?])\s+|\n", para):
            if current and len(current) + len(piece) > max_chars:
                chunks.append(current.strip())
                current = ""
            current += piece + " "
        if current.strip():
            chunks.append(current.strip())
    return chunks


def _limit_lines(text, max_lines):
    lines = []
    for line in text.splitlines():
        lines += textwrap.wrap(line, 110) or [""]
    if max_lines and len(lines) > max_lines:
        lines = lines[:max_lines] + ["…"]
    return "\n".join(lines)


def retrieve(sources, query, per_source=2, max_lines=6):
    """Pour chaque source cochée, garde les `per_source` passages les plus proches de la question.
    Score = part des mots de la question présents dans le passage (+ petit bonus de répétition)."""
    wanted = set(tokens(query))
    if not wanted:
        return []
    results = []
    for s_idx, source in enumerate(sources, start=1):
        scored = []
        for para in chunk_text(source["content"]):
            words = tokens(para)
            hits = wanted & set(words)
            if hits:
                score = len(hits) / len(wanted) + 0.01 * sum(words.count(w) for w in hits)
                scored.append((score, para))
        scored.sort(key=lambda item: -item[0])
        for n, (score, para) in enumerate(scored[:per_source], start=1):
            results.append({"ref": f"S{s_idx}.{n}", "source_id": source["id"], "source_name": source["name"],
                            "score": round(score, 2), "text": _limit_lines(para, max_lines)})
    return results
