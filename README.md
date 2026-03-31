# NBN - Extraction des Notices Biographiques

Pipeline d'extraction automatique des notices biographiques individuelles depuis les volumes PDF de la **Nouvelle Biographie Nationale** (Académie Royale de Belgique) vers des fichiers `.txt` nommés `NOM (Prénom).txt`.

## Résultats

| Volume | Notices extraites |
|--------|-------------------|
| 1      | 103               |
| 2      | 155               |
| 3      | 128               |
| 4      | 144               |
| 5      | 162               |
| 6      | 155               |
| 7      | 139               |
| 8      | 160               |
| 9      | 148               |
| 10     | 2                 |
| **Total** | **1 296**      |

100 % des biographies extraites et validées contre l'index JSON de référence.

---

## Technologies et bibliothèques

| Technologie | Version | Rôle |
|-------------|---------|------|
| **Python**  | 3.11+   | Langage principal |
| **PyMuPDF** (`fitz`) | >= 1.24.0 | Parsing PDF : extraction de texte, analyse des blocs, polices, positions (`bbox`), tailles de caractères |
| **pdfplumber** | >= 0.11.0 | Bibliothèque PDF complémentaire (dépendance optionnelle) |

### Installation

```bash
pip install -r requirements.txt
```

Contenu de `requirements.txt` :

```
pymupdf>=1.24.0
pdfplumber>=0.11.0
```

---

## Utilisation

```bash
# Tout faire automatiquement (index + extraction + validation) :
python app.py

# Spécifier un répertoire d'entrée et de sortie :
python app.py -i NewBioPdf/ -o output/

# Traiter un seul volume :
python app.py -i NewBioPdf/NouvelleBiographieNationale_Volume1.pdf

# Mode silencieux :
python app.py -q
```

### Arguments

| Argument | Défaut | Description |
|----------|--------|-------------|
| `-i`, `--input` | `NewBioPdf/` | Fichier PDF ou répertoire de PDFs |
| `-o`, `--output` | `output/` | Répertoire de sortie des `.txt` |
| `--index` | `index_nouvelles_biographies.json` | Chemin du fichier JSON d'index |
| `-q`, `--quiet` | `False` | Désactive la sortie console |

---

## Architecture

```
app.py                  ← Orchestrateur principal (3 étapes)
├── engine.py           ← Moteur d'extraction PDF (analyse de blocs PyMuPDF)
├── cleaner.py          ← Nettoyage du texte brut → texte continu
└── index_builder.py    ← Génération et validation de l'index JSON
```

### Flux d'exécution complet

```
python app.py
    │
    ├── ÉTAPE 1 — Scan des PDFs (une seule passe par volume)
    │   └── engine.scan_volume_precise(pdf_path)
    │       ├── detect_first_bio_page()   → page de la première biographie
    │       ├── detect_last_bio_page()    → page de la dernière biographie
    │       ├── detect_page_offset()      → décalage PDF ↔ page imprimée
    │       ├── Pass 1 : is_biography_header() sur chaque bloc
    │       ├── Pass 1.5 : _filter_false_positives() (alias)
    │       ├── Pass 2 : calcul des bornes de pages
    │       └── Pass 3 : _extract_entry_text_precise() (texte brut)
    │
    ├── ÉTAPE 2 — Index JSON + export .txt
    │   ├── index_builder.build_index_from_volumes()
    │   ├── index_builder.save_index()
    │   └── Pour chaque volume (résultats réutilisés, pas de re-scan) :
    │       ├── cleaner.clean_biography_text()
    │       │   ├── fix_soft_hyphens()
    │       │   ├── fix_hyphenation()
    │       │   ├── remove_page_numbers()
    │       │   ├── join_paragraph_lines()    → texte continu
    │       │   └── normalize_whitespace()
    │       ├── cleaner.detect_author_signature()
    │       ├── cleaner.format_filename()     → "NOM (Prénom).txt"
    │       └── Écriture du fichier .txt
    │
    └── ÉTAPE 3 — Validation croisée
        └── index_builder.validate_extraction()
            └── Comparaison noms normalisés + pages
```

---

## Modules en détail

### `engine.py` — Moteur d'extraction PDF

Le coeur du pipeline. Utilise l'API `page.get_text("dict")` de PyMuPDF pour accéder à la structure interne du PDF : blocs, lignes, spans (fragments de texte avec métadonnées de police).

#### Structures de données

```python
@dataclass
class BiographyEntry:
    surname: str               # "ABEL", "VAN AELST III", "d'ARTIGUES"
    full_header: str           # Texte complet du bloc d'en-tête
    first_name: str            # "Armand", "Pierre", ""
    pdf_page_start: int        # Index de page PDF (0-based)
    pdf_page_end: int          # Page de fin (exclusive)
    printed_page_start: int    # Numéro de page imprimé
    block_y: float             # Position Y du bloc dans la page
    raw_text: str              # Texte brut extrait
    author_signature: str      # Signature de l'auteur

@dataclass
class VolumeInfo:
    file_path: str
    total_pages: int
    first_bio_page: int        # Première page de biographies
    last_bio_page: int         # Dernière page de biographies
    page_offset: int           # pdf_page - printed_page
    volume_number: int
```

#### Détection de la première page de biographies

Recherche une **lettre de section** (A, B, C...) en grande police (>40pt) qui marque le début des biographies :

```python
def detect_first_bio_page(doc: fitz.Document) -> int:
    start = 0 if len(doc) <= 15 else 3  # Volumes courts : page 0
    for page_num in range(start, min(30, len(doc))):
        page = doc[page_num]
        blocks = page.get_text("dict")["blocks"]
        for block in blocks:
            for span in block["lines"][0]["spans"]:
                # Lettre de section : grande police, 1-2 caractères
                if span["size"] > 40 and len(span["text"].strip()) <= 2:
                    return page_num
    # Fallback : chercher le premier en-tête de biographie
    ...
```

#### Détection de la dernière page

Scan depuis les 2/3 du document pour trouver les marqueurs de fin :

```python
_BACK_MATTER_MARKERS = [
    "LISTE DES AUTEURS",
    "TABLE DES ILLUSTRATIONS",
    "TABLE DES MATIÈRES",
    "TABLE ALPHABÉTIQUE",
]
```

#### Détection des en-têtes de biographie

Deux stratégies pour identifier un bloc comme en-tête biographique :

**Stratégie 1** — Nom + virgule sur la première ligne (cas standard) :
```
ABEL, Armand, Frédéric, Charles, Valère, islamologue...
     ↑ virgule détectée → extraction du nom de famille
```

**Stratégie 2** — Nom seul sur la première ligne, virgule sur les lignes suivantes :
```
MALENGREAU,            ← première ligne : nom seul
Paul, musicien...      ← virgule sur la ligne suivante

VAN                    ← nom fragmenté sur plusieurs lignes
AELST                     (particule + nom + numéral)
III,
Pierre, tapissier...
```

#### Règles de validation d'un nom de famille

```python
NAME_PARTICLES = {'d', 'de', 'du', 'van', 'von', 'der', 'den', 'la', 'le',
                  'les', 'het', 'ten', 'ter', 'dit', 'des'}
NAME_CONNECTORS = {'ou', 'dit', 'dite', 'née', 'alias'}
```

Un nom valide doit :
- Contenir au moins un **mot substantiel en MAJUSCULES** (>= 3 lettres, hors particules)
- Ne pas contenir d'abréviations avec points (`R.D.I.L.C.`)
- Avoir une taille de police entre **7.5pt** et **12.5pt**
- Ne pas être un en-tête courant (petite police <= 8pt, Y < 45px)

Exemples reconnus :
```
ABEL                    → simple
VAN DER MEERSCH         → avec particules
d'ARTIGUES              → apostrophe + particule
LOETS ou LOOTS          → connecteur "ou"
VAN AELST III           → numéraux romains
ÉLISABETH               → caractères accentués
```

#### Filtrage des faux positifs

Les **alias** (noms alternatifs) proches du bloc principal sont fusionnés :

```python
# Deux entrées sur la même page, distance Y < 25px
# et contenant des mots-clés d'alias :
alias_keywords = ['DIT', 'ÉGALEMENT', 'SURNOM', 'ALIAS', 'OU ', 'AUSSI',
                  'APPELÉ', 'CONNU SOUS']
```

Exemple : `GERMANUS` (alias) détecté à 15px sous `de MIDDELBOURG` → fusionné.

#### Extraction du texte

L'extraction utilise `page.get_text("text")` (ordre de lecture naturel) plutôt que l'API bloc par bloc, pour garantir l'ordre correct :

```python
def _extract_entry_text_precise(doc, entry, next_entry, page_text_cache):
    for page_num in range(entry.pdf_page_start, entry.pdf_page_end):
        # Cache des pages déjà parsées (évite les doublons)
        if page_num not in page_text_cache:
            page_text_cache[page_num] = _get_page_content_text(doc[page_num])
        page_text = page_text_cache[page_num]

        # Délimitation : du nom courant au nom suivant
        if page_num == entry.pdf_page_start:
            start_pos = _find_header_in_text(page_text, entry.surname)
            page_text = page_text[start_pos:]
        if next_entry and page_num == next_entry.pdf_page_start:
            end_pos = _find_header_in_text(page_text, next_entry.surname)
            page_text = page_text[:end_pos]
```

Un **cache de pages** (`page_text_cache`) évite de re-parser les pages partagées entre entrées adjacentes.

#### Suppression des éléments parasites

Avant l'extraction, chaque page est nettoyée :

```python
def _get_page_content_text(page):
    blocks = page.get_text("dict")["blocks"]
    skip_texts = set()
    for block in blocks:
        if is_running_header(block, page_height) or \
           is_page_number(block, page_height) or \
           is_section_letter_block(block):
            # Collecter le texte à supprimer
            skip_texts.add(text_du_bloc)
```

| Élément | Critère de détection |
|---------|---------------------|
| En-tête courant | Police <= 8pt **et** Y < 45px |
| Numéro de page | Bloc en bas de page (Y > hauteur - 35px) + contenu numérique |
| Lettre de section | Police > 30pt + texte <= 3 caractères |

---

### `cleaner.py` — Nettoyage du texte

Transforme le texte brut du PDF en texte continu lisible.

#### Pipeline de nettoyage

```python
def clean_biography_text(raw_text: str) -> str:
    text = fix_soft_hyphens(text)       # 1. U+00AD → rejointure
    text = fix_hyphenation(text)        # 2. "islamolo-\ngue" → "islamologue"
    text = remove_page_numbers(text)    # 3. Lignes "42" seules → supprimées
    text = join_paragraph_lines(text)   # 4. Lignes → texte continu
    text = normalize_whitespace(text)   # 5. Espaces multiples, sauts de ligne
    return text
```

#### Rejointure des césures

```python
# Césure typographique : mot-\n + minuscule → rejoint
re.sub(r'(\w)-\n\s*([a-zàâäéèêëïîôùûüÿçæœ])', r'\1\2', text)

# "profes-\nseur"  → "professeur"
# "Jean-\nPierre"  → conservé (P majuscule = tiret intentionnel)
```

#### Jointure en texte continu

Les sauts de ligne intra-paragraphe (hérités de la mise en page PDF) sont remplacés par des espaces. Les vrais paragraphes (doubles sauts de ligne ou tabulations) sont préservés :

```python
def join_paragraph_lines(text: str) -> str:
    paragraphs = re.split(r'\n\s*\n', text)  # Séparer par lignes vides
    for para in paragraphs:
        lines = para.split('\n')
        for line in lines:
            if line.startswith('\t') and merged_parts:
                # Tabulation = nouveau paragraphe
                joined.append(' '.join(merged_parts))
                merged_parts = [stripped]
            else:
                merged_parts.append(stripped)
    return '\n\n'.join(joined)
```

**Avant :**
```
ABEL, Armand, Frédéric, Charles, Valère,
islamologue, professeur d'université, né à Uccle
le 11 juin 1903, décédé à Aywaille le 31 mai
1973.
```

**Après :**
```
ABEL, Armand, Frédéric, Charles, Valère, islamologue, professeur d'université, né à Uccle le 11 juin 1903, décédé à Aywaille le 31 mai 1973.
```

#### Formatage des noms de fichiers

```python
def format_filename(surname: str, first_name: str) -> str:
    # "VAN DER MEERSCH" → "Van der Meersch"
    # Particules en minuscules, premier mot capitalisé
    # Numéraux romains préservés : "VAN AELST III" → "Van Aelst III"
```

Exemples de fichiers générés :
```
Abel (Armand).txt
D'artigues (Aimé-Gabriel).txt
De Jacquier de Rosée (Antoine-Laurent).txt
Van Aelst III (Pierre).txt
Élisabeth (Gabrielle).txt
Ackart.txt                    ← pas de prénom connu
```

---

### `index_builder.py` — Index JSON et validation

#### Structure de l'index

Fichier `index_nouvelles_biographies.json` :

```json
[
  {
    "nom": "ABEL",
    "prenom": "Armand",
    "description": "islamologue, professeur d'université, né à Uccle le 11 juin 1903...",
    "reference": {
      "tome": 1,
      "page": 13
    }
  },
  ...
]
```

#### Validation croisée

Compare les noms extraits avec l'index via normalisation Unicode :

```python
def normalize_name(name: str) -> str:
    nfkd = unicodedata.normalize('NFKD', name)
    no_accents = ''.join(c for c in nfkd if not unicodedata.combining(c))
    return re.sub(r'\s+', ' ', no_accents.lower().strip())

# "d'ARTIGUES" et "D'ARTIGUES" → "d'artigues"
```

Rapport de validation :
```
============================================================
RAPPORT DE VALIDATION — Tome 1
============================================================
Notices dans l'index JSON : 103
Notices extraites du PDF  : 103
Correspondances trouvées  : 103

✓ Toutes les notices correspondent !
============================================================
```

---

### `app.py` — Orchestrateur

Point d'entrée unique. Enchaîne les 3 étapes sans re-scanner les PDFs :

```python
# Étape 1 : scan unique de chaque PDF
scan_results = []
for pdf_path in pdf_files:
    vol_info, entries = scan_volume_precise(pdf_path)
    scan_results.append((vol_info, entries))

# Étape 2 : génération index + export .txt (réutilise scan_results)
index = build_index_from_volumes(scan_results)
for vol_info, entries in scan_results:
    process_volume(..., precomputed=(vol_info, entries))

# Étape 3 : validation
for report in all_reports:
    print_validation_report(report)
```

---

## Structure du projet

```
Pdf-to-txt-nouvelles-biographies/
├── app.py                              # Orchestrateur principal
├── engine.py                           # Moteur d'extraction PDF
├── cleaner.py                          # Nettoyage du texte
├── index_builder.py                    # Index JSON + validation
├── requirements.txt                    # Dépendances Python
├── index_nouvelles_biographies.json    # Index généré (1 296 entrées)
├── NewBioPdf/                          # PDFs sources (non versionnés)
│   ├── NouvelleBiographieNationale_Volume1.pdf
│   ├── ...
│   └── NouvelleBiographieNationale_Volume10.pdf
└── output/                             # Biographies extraites
    ├── Volume_1/                       # 103 fichiers .txt
    │   ├── Abel (Armand).txt
    │   ├── Boteram (Rinaldo).txt
    │   └── ...
    ├── Volume_2/                       # 155 fichiers .txt
    ├── ...
    └── Volume_10/                      # 2 fichiers .txt
```

---

## Optimisations de performance

| Optimisation | Impact |
|-------------|--------|
| **Scan unique par PDF** | Les résultats du Pass 1 sont réutilisés au lieu de re-scanner chaque volume (~2x plus rapide) |
| **Cache de pages** | `page_text_cache` évite de re-parser les pages partagées entre entrées adjacentes |
| **Index direct** | `next_entry` passé directement au lieu de `all_entries.index(entry)` (O(1) vs O(n)) |
| **Regex pré-compilée** | `_REF_PATTERN` compilé une seule fois au niveau du module |
| **Constantes partagées** | `NAME_PARTICLES` importé depuis `engine.py` dans `cleaner.py` (source unique) |

---

## Licence

Ce projet est destiné à un usage académique et de recherche dans le cadre de la numérisation de la Nouvelle Biographie Nationale de l'Académie Royale de Belgique.
