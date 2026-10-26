"""Fonctionnalité 7 : analyse de la qualité et de la gouvernance d'un jeu de données (pandas)."""
import io
import re
import warnings

import pandas as pd

from errors import AppError

EMAIL = r"^[\w.+-]+@[\w-]+(\.[\w-]+)+$"
NON_NEGATIVE = re.compile(r"(age|prix|price|salaire|salary|montant|amount|quantit|stock|poids|weight)", re.I)
PII = re.compile(r"(nom|name|prenom|email|mail|tel|phone|cin|passport|adresse|address|iban|rib|naissance|birth)", re.I)
KEY = re.compile(r"^(id|.*_id|id_.*|code|email|mail|cin|matricule)$", re.I)

LABELS = {"completeness": "Complétude", "uniqueness": "Unicité", "validity": "Conformité",
          "consistency": "Cohérence", "anomalies": "Anomalies"}


def read_dataframe(filename, raw):
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    try:
        if ext in {"csv", "tsv", "txt"}:
            try:
                df = pd.read_csv(io.BytesIO(raw), sep=None, engine="python", encoding="utf-8-sig")
            except UnicodeDecodeError:
                df = pd.read_csv(io.BytesIO(raw), sep=None, engine="python", encoding="latin-1")
        elif ext == "xlsx":
            df = pd.read_excel(io.BytesIO(raw))
        elif ext == "json":
            df = pd.read_json(io.BytesIO(raw))
        else:
            raise AppError("Format non supporté. Utilise .csv, .xlsx ou .json.", 400, "bad_format")
    except AppError:
        raise
    except Exception as exc:
        raise AppError("Impossible de lire ce fichier : vérifie qu'il s'agit d'un tableau valide.",
                       400, "bad_file") from exc
    if df.empty:
        raise AppError("Le fichier ne contient aucune donnée.", 400, "empty_file")
    return df


def _grade(score):
    return "Excellent" if score >= 90 else "Bon" if score >= 75 else "Moyen" if score >= 50 else "Insuffisant"


def _pct(bad, total):
    return round(max(0.0, min(100.0, 100 * (1 - bad / total))), 1) if total else 100.0


def analyze(df, filename):
    rows, ncols = df.shape
    cells = rows * ncols
    missing_cells = inconsistent = invalid = outliers = numeric_cells = 0
    columns, pii_columns, key_dups = [], [], []

    for col in df.columns:
        s, name = df[col], str(col)
        lname = name.lower()
        is_text = pd.api.types.is_object_dtype(s) or pd.api.types.is_string_dtype(s)
        blank = s.isna()
        if is_text:  # une chaîne vide ou faite d'espaces compte comme valeur manquante
            blank = blank | s.astype("string").str.strip().eq("").fillna(False)
        n_missing = int(blank.sum())
        present = s[~blank]
        issues, c_incons, c_invalid, c_out = [], 0, 0, 0

        if PII.search(name):
            pii_columns.append(name)

        if is_text and len(present):
            v = present.astype(str)
            norm = v.str.strip()
            ws = int((v != norm).sum())
            if ws:
                c_incons += ws
                issues.append(f"{ws} valeur(s) avec espaces au début/à la fin")
            lower = norm.str.lower()
            variants = norm.groupby(lower).nunique()
            variants = variants[variants > 1].index
            if len(variants):
                mask = lower.isin(variants)
                c_incons += int(mask.sum())
                issues.append("Variantes de casse : " + ", ".join(map(str, norm[mask].unique()[:4])))
            num = pd.to_numeric(norm, errors="coerce")
            share = float(num.notna().mean())
            if 0.8 <= share < 1:
                bad = int(num.isna().sum())
                c_invalid += bad
                issues.append(f"{bad} valeur(s) non numérique(s) dans une colonne numérique")
            elif 0 < share <= 0.2:
                bad = int(num.notna().sum())
                c_incons += bad
                issues.append(f"{bad} nombre(s) dans une colonne de texte (types mélangés)")
            if "mail" in lname:
                bad = int((~norm.str.match(EMAIL)).sum())
                if bad:
                    c_invalid += bad
                    issues.append(f"{bad} e-mail(s) au format invalide")
            if "date" in lname:
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore")
                    parsed = pd.to_datetime(norm, errors="coerce", dayfirst=True)
                bad = int(parsed.isna().sum())
                if bad:
                    c_invalid += bad
                    issues.append(f"{bad} date(s) invalide(s) ou de format différent")
            if re.search(r"(tel|phone|gsm)", lname):
                digits = norm.str.replace(r"\D", "", regex=True).str.len()
                bad = int(((digits < 8) | (digits > 15)).sum())
                if bad:
                    c_invalid += bad
                    issues.append(f"{bad} numéro(s) de téléphone suspect(s)")

        if pd.api.types.is_numeric_dtype(s) and not pd.api.types.is_bool_dtype(s):
            x = pd.to_numeric(s, errors="coerce").dropna().astype(float)
            numeric_cells += len(x)
            if len(x) >= 8:
                q1, q3 = x.quantile(0.25), x.quantile(0.75)
                iqr = q3 - q1
                if iqr > 0:
                    c_out = int(((x < q1 - 1.5 * iqr) | (x > q3 + 1.5 * iqr)).sum())
                    if c_out:
                        issues.append(f"{c_out} valeur(s) aberrante(s) (méthode IQR)")
            if NON_NEGATIVE.search(name):
                neg = int((x < 0).sum())
                if neg:
                    c_invalid += neg
                    issues.append(f"{neg} valeur(s) négative(s) alors qu'elle devrait être positive")
            if lname.startswith("age") or lname.startswith("âge"):
                big = int((x > 120).sum())
                if big:
                    c_invalid += big
                    issues.append(f"{big} âge(s) supérieur(s) à 120")

        if len(present) > 1 and present.nunique() <= 1:
            issues.append("Colonne constante (aucune information utile)")
        if KEY.match(name):
            d = int(present.duplicated().sum())
            if d:
                key_dups.append({"column": name, "duplicates": d})
                issues.append(f"{d} doublon(s) sur une colonne qui devrait être unique")

        missing_cells += n_missing
        inconsistent += c_incons
        invalid += c_invalid
        outliers += c_out
        columns.append({"name": name, "dtype": str(s.dtype), "missing": n_missing,
                        "missing_pct": round(100 * n_missing / rows, 1), "unique": int(s.nunique()),
                        "issues": issues})

    dup_rows = int(df.duplicated().sum())
    worst_dup = max([dup_rows] + [k["duplicates"] for k in key_dups])
    scores = {
        "completeness": _pct(missing_cells, cells),
        "uniqueness": _pct(worst_dup, rows),
        "validity": _pct(invalid, cells),
        "consistency": _pct(inconsistent, cells),
        "anomalies": _pct(outliers, numeric_cells),
    }
    weights = {"completeness": .30, "uniqueness": .20, "validity": .20, "consistency": .15, "anomalies": .15}
    global_score = round(sum(scores[k] * w for k, w in weights.items()), 1)

    reco = []
    worst_missing = [c for c in columns if c["missing_pct"] >= 20]
    if worst_missing:
        reco.append("Compléter ou supprimer les colonnes très incomplètes : "
                    + ", ".join(c["name"] for c in worst_missing))
    if dup_rows:
        reco.append(f"Supprimer les {dup_rows} ligne(s) entièrement dupliquée(s).")
    for k in key_dups:
        reco.append(f"Mettre une contrainte d'unicité sur « {k['column']} » ({k['duplicates']} doublon(s)).")
    if inconsistent:
        reco.append("Normaliser les textes (espaces, casse) avec un référentiel de valeurs autorisées.")
    if invalid:
        reco.append("Ajouter des règles de validation à la saisie (formats e-mail/date/téléphone, valeurs positives).")
    if outliers:
        reco.append("Vérifier les valeurs aberrantes avec le métier avant de les corriger ou de les exclure.")
    if pii_columns:
        reco.append("Colonnes à caractère personnel détectées (" + ", ".join(pii_columns)
                    + ") : vérifier la conformité RGPD / loi 09-08, restreindre l'accès et pseudonymiser si possible.")
    if not reco:
        reco.append("Aucun problème majeur détecté : documenter le jeu de données (propriétaire, dictionnaire, fréquence de mise à jour).")

    summary = (f"Rapport qualité du fichier « {filename} » : {rows} lignes, {ncols} colonnes, score global "
               f"{global_score}/100 ({_grade(global_score)}). Complétude {scores['completeness']}, unicité "
               f"{scores['uniqueness']}, conformité {scores['validity']}, cohérence {scores['consistency']}, "
               f"anomalies {scores['anomalies']}. Problèmes : "
               + "; ".join(f"{c['name']} → {', '.join(c['issues'])}" for c in columns if c["issues"])
               + ". Propose-moi un plan d'amélioration de la gouvernance de ces données.")

    return {"filename": filename, "rows": rows, "columns": ncols, "missing_cells": missing_cells,
            "duplicate_rows": dup_rows, "key_duplicates": key_dups, "scores": scores, "labels": LABELS,
            "global_score": global_score, "label": _grade(global_score), "columns_report": columns,
            "pii_columns": pii_columns, "recommendations": reco, "summary_text": summary}
