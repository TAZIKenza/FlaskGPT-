# FlaskGPT

Assistant conversationnel développé avec Flask, Tailwind CSS et SQLite. Il utilise l'API Groq à travers le SDK OpenAI. En plus du chat en streaming, l'application gère l'historique des conversations, des domaines de spécialisation, l'export, l'analyse de la qualité de données, le suivi des performances, la notation des réponses et la citation de sources.

## Fonctionnalités

| N° | Fonctionnalité | Description |
|----|----------------|-------------|
| 1 | Gestion des conversations | Démarrer une nouvelle conversation, supprimer la conversation en cours, rouvrir une ancienne conversation depuis l'historique |
| 2 | Domaines | Liste déroulante (général, informatique, finance, analyse de données, gouvernance des données) qui adapte les réponses |
| 3 | Base de données | Conversations, messages, sources et rapports stockés dans SQLite |
| 4 | Gestion des erreurs | Messages clairs pour une clé API invalide, un quota dépassé ou un problème de connexion |
| 5 | Résumé PDF | Résumé généré par le modèle, accompagné d'indicateurs chiffrés |
| 6 | Export | Conversation complète au format TXT, JSON ou PDF |
| 7 | Qualité des données | Import CSV, XLSX ou JSON : valeurs manquantes, doublons, incohérences, anomalies, scores de qualité, recommandations |
| 8 | Performances | Temps de réponse, tokens par réponse et par conversation, débit en tokens par seconde |
| 9 | Évaluation des réponses | Note de 1 à 5 étoiles avec code couleur (vert, orange, rouge) |
| 10 | Sources et références | Import de documents, sélection par cases à cocher, nombre d'extraits et de lignes réglable, citations `[S1.1]` reliées aux passages |

## Prérequis

- Python 3.10 ou plus
- Une clé API Groq (https://console.groq.com/keys)
- Optionnel : Poetry pour gérer les dépendances, Tailwind CSS pour modifier le style

## Installation

### Avec venv et pip (Windows PowerShell)

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install flask openai python-dotenv pandas openpyxl reportlab pypdf
```

Sous macOS ou Linux, l'activation se fait avec `source .venv/bin/activate`.

### Avec Poetry

```bash
poetry install
```

Si la commande `poetry` n'est pas reconnue : `python -m poetry install`.

## Configuration

Copier le fichier d'exemple puis renseigner la clé :

```bash
copy .env.example .env        # Windows
cp .env.example .env          # macOS / Linux
```

Contenu du fichier `.env` :

```
GROQ_API_KEY=votre_cle
# GROQ_MODEL=openai/gpt-oss-20b   (optionnel)
```

Le fichier `.env` est ignoré par Git : la clé ne doit jamais être publiée.

## Lancement

```bash
python app.py
```

Ouvrir ensuite http://127.0.0.1:5000. Avec Poetry : `poetry run python app.py`.

Le CSS compilé est fourni dans `static/dist/css/style.css`. Après une modification des classes Tailwind, le régénérer avec :

```bash
tailwindcss -i ./static/css/input.css -o ./static/dist/css/style.css --watch
```

## Utilisation

1. Choisir un domaine dans la liste déroulante, puis poser une question.
2. Noter chaque réponse avec les étoiles. La bordure de la carte devient verte (4 à 5), orange (3) ou rouge (1 à 2).
3. Bouton **Sources** : importer des documents, cocher ceux à consulter, régler le nombre d'extraits et de lignes. Cliquer sur une citation `[S1.1]` dans la réponse ouvre le passage correspondant.
4. Bouton **Qualité des données** : importer un tableau et lire le rapport. Le bouton « Discuter de ce rapport » l'envoie à l'assistant.
5. Bouton **Performances** : consulter les temps de réponse et la consommation de tokens.
6. Boutons **Exporter** et **Résumé PDF** : télécharger la conversation en cours.

Des fichiers d'essai sont dans `samples/` : `clients_demo.csv` (erreurs volontaires, à utiliser avec la qualité des données) et `gouvernance_donnees.txt` (à utiliser comme source).

## Structure du projet

```
flaskgpt/
├── app.py              Routes Flask
├── database.py         Accès SQLite
├── llm.py              Appel au modèle, domaines, traduction des erreurs
├── sources.py          Extraction de texte, découpage, recherche de passages
├── data_quality.py     Analyse qualité et gouvernance (pandas)
├── exports.py          Export TXT, JSON, PDF et résumé PDF
├── errors.py           Exception applicative
├── templates/
│   └── index.html      Page unique
├── static/
│   ├── css/input.css   Source Tailwind
│   └── dist/
│       ├── css/style.css   CSS compilé
│       └── js/script.js    Logique du navigateur
├── samples/            Fichiers d'essai
├── tests/              Tests automatiques
├── pyproject.toml
└── .env.example
```

## Routes

| Méthode | Route | Rôle |
|---------|-------|------|
| GET | `/` | Page principale |
| POST | `/prompt` | Envoie une question, réponse en streaming |
| GET, POST | `/api/conversations` | Lister, créer |
| GET, PATCH, DELETE | `/api/conversations/<id>` | Lire, changer le domaine, supprimer |
| GET | `/api/conversations/<id>/export?format=txt\|json\|pdf` | Export |
| GET | `/api/conversations/<id>/summary.pdf` | Résumé PDF |
| PUT | `/api/messages/<id>/rating` | Noter une réponse (1 à 5) |
| GET | `/api/stats?conversation_id=<id>` | Indicateurs de performance |
| GET, POST | `/api/sources` | Lister, importer une source |
| DELETE | `/api/sources/<id>` | Supprimer une source |
| POST | `/api/analyze` | Analyser un jeu de données |

## Format de la réponse en streaming

La réponse de `/prompt` est du texte envoyé au fur et à mesure. À la fin, le serveur ajoute le caractère `\x1e` suivi d'un objet JSON contenant l'identifiant du message, le temps de réponse, les tokens et les sources utilisées. En cas d'erreur avant le début de la réponse, un code HTTP adapté est renvoyé (401 clé invalide, 429 quota, 503 connexion) avec `{"error": {"code": ..., "message": ...}}`.

## Tests

Les tests utilisent un faux modèle et ne nécessitent aucune clé API.

```bash
pip install pytest httpx
pytest
```

## Limites connues

- La recherche dans les sources se base sur les mots-clés, pas sur des embeddings.
- Si l'API ne fournit pas la consommation de tokens, elle est estimée (environ 4 caractères par token) et signalée comme telle.
- Les emojis ne s'affichent pas dans les PDF (police de base).
- Les PDF scannés (images) ne contiennent pas de texte extractible.


