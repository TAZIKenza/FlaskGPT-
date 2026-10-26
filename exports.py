"""Fonctionnalités 5 et 6 : export TXT / JSON / PDF et résumé PDF (reportlab)."""
import json
import re
from io import BytesIO
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from llm import DOMAINS


def _domain(conv):
    return DOMAINS.get(conv["domain"], {}).get("label", conv["domain"])


def _meta_line(m):
    parts = []
    if m.get("response_time_ms") is not None:
        parts.append(f"{m['response_time_ms'] / 1000:.1f} s")
    if m.get("total_tokens"):
        parts.append(f"{m['total_tokens']} tokens" + (" (estimés)" if m.get("tokens_estimated") else ""))
    if m.get("rating"):
        parts.append(f"note {m['rating']}/5")
    return " · ".join(parts)


# ---------------- TXT / JSON ----------------
def to_txt(conv, messages):
    lines = [f"FlaskGPT - conversation #{conv['id']}", f"Titre : {conv['title']}",
             f"Domaine : {_domain(conv)}", f"Créée le : {conv['created_at']}", "=" * 60, ""]
    for m in messages:
        who = "Vous" if m["role"] == "user" else "Assistant"
        lines += [f"[{who}] {m['created_at']}", m["content"]]
        if m["role"] == "assistant":
            if _meta_line(m):
                lines.append(f"({_meta_line(m)})")
            for s in m["sources"]:
                lines.append(f"  Source [{s['ref']}] {s['source_name']} : {s['text'][:150]}")
        lines.append("")
    return "\n".join(lines)


def to_json(conv, messages, stats):
    return json.dumps({"conversation": conv, "statistiques": stats, "messages": messages},
                      ensure_ascii=False, indent=2)


# ---------------- PDF ----------------
def _safe(text):  # Helvetica ne gère que le jeu latin : les emojis deviennent "?"
    return text.encode("cp1252", "replace").decode("cp1252")


def _inline(text):
    t = escape(_safe(text))
    t = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", t)
    return re.sub(r"`(.+?)`", r"<font face='Courier'>\1</font>", t)


def _styles():
    base = getSampleStyleSheet()
    return {
        "title": ParagraphStyle("t", parent=base["Title"], fontSize=20, spaceAfter=4),
        "h": ParagraphStyle("h", parent=base["Heading3"], textColor=colors.HexColor("#4338ca"), spaceBefore=10),
        "body": ParagraphStyle("b", parent=base["BodyText"], leading=14),
        "bullet": ParagraphStyle("bl", parent=base["BodyText"], leading=14, leftIndent=14),
        "small": ParagraphStyle("s", parent=base["BodyText"], fontSize=8, textColor=colors.grey),
        "who": ParagraphStyle("w", parent=base["BodyText"], fontName="Helvetica-Bold", spaceBefore=10),
    }


def _markdown(text, st):
    out = []
    for line in text.splitlines():
        if line.strip().startswith("```"):
            continue
        if not line.strip():
            out.append(Spacer(1, 4))
        elif re.match(r"^#{1,4}\s", line):
            out.append(Paragraph(_inline(line.lstrip("# ")), st["h"]))
        elif re.match(r"^\s*[-*•]\s+", line):
            out.append(Paragraph(_inline(re.sub(r"^\s*[-*•]\s+", "", line)), st["bullet"], bulletText="•"))
        else:
            out.append(Paragraph(_inline(line), st["body"]))
    return out


def _build(story):
    buf = BytesIO()

    def footer(canvas, doc):
        canvas.setFont("Helvetica", 8)
        canvas.setFillColor(colors.grey)
        canvas.drawRightString(A4[0] - 2 * cm, 1.2 * cm, f"FlaskGPT - page {doc.page}")

    SimpleDocTemplate(buf, pagesize=A4, leftMargin=2 * cm, rightMargin=2 * cm,
                      topMargin=2 * cm, bottomMargin=2 * cm).build(story, onFirstPage=footer, onLaterPages=footer)
    return buf.getvalue()


def _header(conv, st, subtitle):
    return [Paragraph(_inline(subtitle), st["title"]),
            Paragraph(_inline(f"{conv['title']} - domaine : {_domain(conv)} - {conv['created_at']}"), st["small"]),
            Spacer(1, 10)]


def to_pdf_transcript(conv, messages):
    st = _styles()
    story = _header(conv, st, "Conversation complète")
    for m in messages:
        who = "Vous" if m["role"] == "user" else "Assistant"
        extra = f" ({_meta_line(m)})" if m["role"] == "assistant" and _meta_line(m) else ""
        story.append(Paragraph(_inline(who + extra), st["who"]))
        story += _markdown(m["content"], st)
        for s in m["sources"]:
            story.append(Paragraph(_inline(f"[{s['ref']}] {s['source_name']} : {s['text'][:200]}"), st["small"]))
    return _build(story)


def to_pdf_summary(conv, messages, summary, stats):
    st = _styles()
    story = _header(conv, st, "Résumé de conversation")
    rows = [["Questions posées", str(sum(1 for m in messages if m["role"] == "user"))],
            ["Réponses", str(stats["answers"])],
            ["Tokens consommés", str(stats["total_tokens"])],
            ["Temps de réponse moyen", f"{stats['avg_time_ms'] / 1000:.1f} s" if stats["avg_time_ms"] else "-"],
            ["Note moyenne", f"{stats['avg_rating']}/5" if stats["avg_rating"] else "non noté"]]
    table = Table(rows, colWidths=[7 * cm, 5 * cm])
    table.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 0.4, colors.lightgrey),
                               ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#eef2ff")),
                               ("FONTSIZE", (0, 0), (-1, -1), 9)]))
    story += [table, Spacer(1, 12), Paragraph("Synthèse", st["h"])] + _markdown(summary, st)
    return _build(story)
