# Journal de Développement

Historique technique complet du projet d'extraction des notices biographiques de la Nouvelle Biographie Nationale (NBN).

---

## 26 mars 2026 — Premier commit

**Commit :** `a1c5653`

Initialisation du dépôt avec la structure de base du projet et les fichiers PDF sources dans `NewBioPdf/`.

```
Pdf-to-txt-nouvelles-biographies/
├── NewBioPdf/
│   ├── NouvelleBiographieNationale_Volume1.pdf
│   ├── NouvelleBiographieNationale_Volume2.pdf
│   └── NouvelleBiographieNationale_Volume3.pdf
├── requirements.txt
└── .gitignore
```

---

## 30 mars 2026 — Pipeline d'extraction initial

**Commit :** `2fc1b25` — `feat: pipeline d'extraction des biographies NBN (PDF → TXT)`

### Architecture modulaire mise en place

Création des 4 modules principaux :

| Module | Responsabilité |
|--------|---------------|
| `engine.py` | Extraction PDF via PyMuPDF (`fitz`) |
| `cleaner.py` | Nettoyage du texte brut |
| `index_builder.py` | Index JSON + validation |
| `app.py` | Orchestration |

### Choix technique : PyMuPDF (`fitz`)

L'API `page.get_text("dict")` donne accès à la structure interne du PDF :

```python
blocks = page.get_text("dict")["blocks"]
# Chaque bloc contient :
# {
#   "type": 0,           ← texte (vs 1 = image)
#   "bbox": (x0, y0, x1, y1),
#   "lines": [
#     {"spans": [
#       {"text": "ABEL, ", "size": 9.0, "font": "Times-Roman", ...}
#     ]}
#   ]
# }
```

Ce niveau de détail (taille de police, position `bbox`, police) est essentiel pour distinguer les en-têtes de biographie du corps de texte.

### Détection des en-têtes : première approche

Premier algorithme basé sur la **détection de texte en gras** :

```python
# Approche initiale (abandonnée) :
def is_bold(span):
    return "Bold" in span["font"] or "bold" in span["font"].lower()
```

**Problème :** Les PDFs de la NBN n'utilisent pas systématiquement le gras pour les noms. Les en-têtes sont en fait identifiables par le **format ALL CAPS** à la taille du corps de texte (~8-10pt).

### Approche retenue : ALL CAPS + virgule

```python
def _extract_surname(text_with_comma: str) -> tuple[str, bool]:
    name_part = text_with_comma.split(',')[0].strip()
    words = name_part.split()
    # Vérifier que chaque mot est en MAJUSCULES
    for word in words:
        if not _is_upper_word(word):
            return "", False
    return name_part, True

def _is_upper_word(word: str) -> bool:
    alpha = [c for c in word if c.isalpha()]
    return bool(alpha) and all(c.isupper() for c in alpha)
```

### Problèmes résolus lors de cette itération

**1. Particules en minuscules non reconnues**

Les noms comme `d'ARTIGUES`, `de DEVENTER`, `van STRYDONCK` n'étaient pas détectés car `_extract_surname()` rejetait les mots non-MAJUSCULES.

```python
# Avant (KO) :
words = ["d'ARTIGUES"]  # → "d'" n'est pas UPPER → rejeté

# Après (OK) :
NAME_PARTICLES = {'d', 'de', 'du', 'van', 'von', 'der', 'den', ...}

# Les particules sont autorisées en minuscules avant un mot UPPER
if prefix.lower() in NAME_PARTICLES and _is_upper_word(main):
    return name_part, True
```

**2. En-têtes courants filtrés comme headers de biographie**

`DELPORTE` (Y=40.2, taille 9pt) était filtré comme en-tête courant car `Y < 55px`.

```python
# Avant : seuil de position trop large
def is_header_footer_block(block, page_height):
    return block["bbox"][1] < 55  # Trop agressif

# Après : combinaison position + taille de police
RUNNING_HEADER_MAX_SIZE = 8.0
RUNNING_HEADER_MAX_Y = 45

def is_running_header(block, page_height):
    if block["bbox"][1] > RUNNING_HEADER_MAX_Y:
        return False
    first_span = first_line["spans"][0]
    return first_span["size"] <= RUNNING_HEADER_MAX_SIZE
```

**3. `HUYSMANS` filtré à tort comme en-tête courant**

Le bloc de `HUYSMANS` (Y=42.8) contenait un span de 7.6pt (exposant) qui déclenchait le filtre. Correction : ne vérifier que le **premier span significatif**, pas tous les spans.

```python
# Avant (KO) : vérifie TOUS les spans
for span in first_line["spans"]:
    if span["size"] <= RUNNING_HEADER_MAX_SIZE:
        return True  # ← faux positif sur un exposant

# Après (OK) : vérifie seulement le PREMIER span non-vide
for span in first_line["spans"]:
    if span["text"].strip():
        return span["size"] <= RUNNING_HEADER_MAX_SIZE
```

---

## 30 mars 2026 — Commande unique `python app.py`

**Commit :** `98b368c` — `refactor: lancer tout le pipeline avec simplement python app.py`

Refactoring de `app.py` pour enchaîner automatiquement les 3 étapes :
1. Génération de l'index JSON
2. Extraction des fichiers `.txt`
3. Validation croisée

```python
def main():
    parser = argparse.ArgumentParser(...)
    parser.add_argument("-i", "--input", default="NewBioPdf")
    parser.add_argument("-o", "--output", default="output")
    parser.add_argument("--index", default="index_nouvelles_biographies.json")
    parser.add_argument("-q", "--quiet", action="store_true")
```

Avant ce commit, il fallait lancer chaque étape séparément. Après : `python app.py` fait tout.

---

## 30 mars 2026 — Ajout des volumes 4 à 10

**Commit :** `1b34e67` — `compilation test`

Ajout de 7 volumes supplémentaires (volumes 4 à 10) au répertoire `NewBioPdf/`. Test de compilation sur l'ensemble des 10 volumes.

**Résultat :** Volumes 1-9 OK, **Volume 10 : 0 biographies extraites** (alors qu'il en contient 2).

---

## 31 mars 2026 — Fix Volume 10 + validation complète

**Commit :** `35bd216` — `fix: Volume 10 extraction + ajout des 1296 biographies extraites`

### Diagnostic du problème Volume 10

Le Volume 10 ne contient que **2 pages** (pas d'avant-propos, pas de table des matières). Trois fonctions échouaient sur ce cas limite :

**1. `detect_first_bio_page()` commençait à la page 3**

```python
# Avant (KO pour Volume 10, 2 pages) :
for page_num in range(3, min(30, len(doc))):  # range(3, 2) = vide !

# Après :
start = 0 if len(doc) <= 15 else 3
for page_num in range(start, min(30, len(doc))):
```

**2. `detect_page_offset()` exigeait un numéro de page >= 5**

Le Volume 10 a des pages imprimées 11 et 12. Le seuil minimum de 5 était correct pour les grands volumes mais empêchait la détection pour les petits.

```python
# Avant :
if 5 <= printed <= page_num + 20:
    return page_num - printed

# Après :
if 1 <= printed <= page_num + 50:
    return page_num - printed
```

**3. `detect_last_bio_page()` calculait un index négatif**

```python
# Avant (KO) :
start = (len(doc) * 2) // 3  # (2 * 2) // 3 = 1, OK ici
# Mais pour un document de 1 page : (1 * 2) // 3 = 0... puis page_num - 1 = -1

# Après :
start = max(0, (len(doc) * 2) // 3)
```

**4. Fallback ajouté dans `detect_first_bio_page()`**

Si aucune lettre de section n'est trouvée (Volume 10 n'en a pas), recherche du premier en-tête de biographie :

```python
# Fallback
for page_num in range(len(doc)):
    blocks = page.get_text("dict")["blocks"]
    for block in blocks:
        is_header, _, _ = is_biography_header(block, page_height)
        if is_header:
            return page_num
return 0
```

### Autres corrections cumulées

| Bug | Cause | Fix |
|-----|-------|-----|
| `GERMANUS` détecté comme biographie séparée | Alias dans l'entrée `de MIDDELBOURG` | `_filter_false_positives()` + `_is_alias_entry()` : fusion si même page et Y < 25px |
| `LOETS ou LOOTS` non reconnu | Le mot `ou` n'était pas autorisé | Ajout de `NAME_CONNECTORS = {'ou', 'dit', 'dite', 'née', 'alias'}` |
| `MALENGREAU` non reconnu | Nom seul sur la première ligne, virgule en dessous | Stratégie 2 : jointure des premières lignes du bloc |
| `VAN AELST III` incorrect | Première ligne = `VAN` seul (particule) | Stratégie 2 : construction du nom à partir de lignes consécutives name-like |
| `THONGER` (8.4pt) et `VAN AELST II` (8.0pt) non détectés | Seuil de police trop haut (8.5pt) | `BIO_HEADER_MIN_SIZE` abaissé à 7.5 |
| Texte dans le mauvais ordre | `page.get_text("dict")` ne respecte pas l'ordre de lecture | Passage à `page.get_text("text")` pour l'extraction du texte |
| Borne de fin off-by-one | `end_pos > 0` ratait les entrées à la position 0 | Corrigé en `end_pos >= 0` |
| Back-matter inclus dans le texte | `detect_last_bio_page` itérait à l'envers | Changement en scan vers l'avant depuis les 2/3 du document |
| `Ii`, `Iii` dans les noms de fichiers | `.capitalize()` sur les numéraux romains | Ajout du set `roman_numerals = {'I', 'II', 'III', ...}` dans `format_filename()` |

### Validation complète

```
Volume  1 : 103 notices ✓
Volume  2 : 155 notices ✓
Volume  3 : 128 notices ✓
Volume  4 : 144 notices ✓
Volume  5 : 162 notices ✓
Volume  6 : 155 notices ✓
Volume  7 : 139 notices ✓
Volume  8 : 160 notices ✓
Volume  9 : 148 notices ✓
Volume 10 :   2 notices ✓
─────────────────────────
TOTAL     : 1 296 notices
```

Validation croisée :
- 1 294 noms correspondent exactement à l'index JSON utilisateur (normalisés Unicode)
- 2 entrées Volume 10 (`ALBIMOOR`, `ARNOULD`) ajoutées à l'index
- Scan PDF page par page : aucun en-tête de biographie manqué

---

## 31 mars 2026 — Optimisations de performance

**Commit :** `680f48a` — `perf+refactor: optimize pipeline performance and code quality`

### 1. Élimination du double scan PDF (~2x plus rapide)

Le pipeline scannait chaque PDF **deux fois** : une pour l'index, une pour l'extraction.

```python
# Avant : 2 passes complètes
# Étape 1
for pdf_path in pdf_files:
    vol_info, entries = scan_volume_precise(pdf_path)  # Pass 1
    index_results.append((vol_info, entries))

# Étape 2
for pdf_path in pdf_files:
    process_volume(pdf_path, ...)  # Re-scan complet ! ← GASPILLAGE

# Après : résultats réutilisés via le paramètre precomputed
scan_results = []
for pdf_path in pdf_files:
    vol_info, entries = scan_volume_precise(pdf_path)  # Pass unique
    scan_results.append((vol_info, entries))

for vol_info, entries in scan_results:
    process_volume(..., precomputed=(vol_info, entries))  # Pas de re-scan
```

### 2. Fix O(n²) → O(n) dans l'extraction de texte

```python
# Avant : recherche linéaire pour chaque entrée
def _extract_entry_text_precise(doc, entry, all_entries):
    entry_idx = all_entries.index(entry)  # O(n) × n appels = O(n²)
    next_entry = all_entries[entry_idx + 1]

# Après : next_entry passé directement
for i, entry in enumerate(entries):
    next_entry = entries[i + 1] if i + 1 < len(entries) else None
    entry.raw_text = _extract_entry_text_precise(doc, entry, next_entry, cache)
```

### 3. Cache de pages partagées

Quand deux entrées sont sur la même page PDF, la page n'est parsée qu'une fois :

```python
page_text_cache: dict[int, str] = {}

for i, entry in enumerate(entries):
    # Le cache évite de re-parser les pages déjà traitées
    if page_num not in page_text_cache:
        page_text_cache[page_num] = _get_page_content_text(doc[page_num])
    page_text = page_text_cache[page_num]
```

### 4. Regex pré-compilée

```python
# Avant : compilée à chaque itération de page
for page_num in range(start, len(doc)):
    ref_pattern = re.compile(r'[IVX]+\s*,\s*\d+')  # ← compilée N fois

# Après : compilée une seule fois au niveau module
_REF_PATTERN = re.compile(r'[IVX]+\s*,\s*\d+')
```

### 5. DRY : 3 boucles identiques → 1

```python
# Avant : 3 boucles séparées pour header, footer, section letter
for block in blocks:
    if is_running_header(block, page_height):
        for line in block["lines"]:
            skip_texts.add(...)
for block in blocks:
    if is_page_number(block, page_height):
        for line in block["lines"]:
            skip_texts.add(...)
for block in blocks:
    if is_section_letter_block(block):
        for line in block["lines"]:
            skip_texts.add(...)

# Après : une seule boucle avec condition combinée
for block in blocks:
    if is_running_header(block, page_height) or \
       is_page_number(block, page_height) or \
       is_section_letter_block(block):
        for line in block["lines"]:
            skip_texts.add(...)
```

### 6. Constante partagée

```python
# Avant : particles dupliquées dans cleaner.py
particles = {'de', 'du', 'van', 'von', 'der', 'den', 'la', 'le', 'les',
             'het', 'ten', 'ter', 'dit', 'des', 'ou'}

# Après : import depuis engine.py (source unique)
from engine import NAME_PARTICLES
particles = NAME_PARTICLES | {'ou'}
```

### 7. `defaultdict` dans `index_builder.py`

```python
# Avant :
index_lookup = {}
for idx_entry in index_entries:
    key = normalize_name(idx_entry["nom"])
    if key not in index_lookup:
        index_lookup[key] = []
    index_lookup[key].append(idx_entry)

# Après :
from collections import defaultdict
index_lookup = defaultdict(list)
for idx_entry in index_entries:
    index_lookup[normalize_name(idx_entry["nom"])].append(idx_entry)
```

---

## 31 mars 2026 — Texte continu dans les fichiers TXT

**Commit :** `e919b35` — `feat: texte continu dans les fichiers TXT`

### Problème

Les fichiers `.txt` conservaient les retours à la ligne du PDF (césures de mise en page toutes les ~60 caractères) :

```
ABEL, Armand, Frédéric, Charles, Valère,
islamologue, professeur d'université, né à Uccle
le 11 juin 1903, décédé à Aywaille le 31 mai
1973.
Après des études primaires à Ixelles et à
Schaerbeek et des études secondaires à l'Athénée communal de Schaerbeek, Armand Abel
entre à l'Université libre de Bruxelles...
```

### Solution : `join_paragraph_lines()`

Nouvelle fonction dans `cleaner.py` qui fusionne les lignes d'un même paragraphe en texte continu :

```python
def join_paragraph_lines(text: str) -> str:
    # Séparer par paragraphes (lignes vides ou tabulations)
    paragraphs = re.split(r'\n\s*\n', text)

    joined = []
    for para in paragraphs:
        lines = para.split('\n')
        merged_parts = []
        for line in lines:
            stripped = line.strip()
            if not stripped:
                continue
            # Tabulation = nouveau sous-paragraphe
            if line.startswith('\t') and merged_parts:
                joined.append(' '.join(merged_parts))
                merged_parts = [stripped]
            else:
                merged_parts.append(stripped)
        if merged_parts:
            joined.append(' '.join(merged_parts))

    return '\n\n'.join(joined)
```

### Résultat

```
ABEL, Armand, Frédéric, Charles, Valère, islamologue, professeur d'université, né à Uccle le 11 juin 1903, décédé à Aywaille le 31 mai 1973.

Après des études primaires à Ixelles et à Schaerbeek et des études secondaires à l'Athénée communal de Schaerbeek, Armand Abel entre à l'Université libre de Bruxelles...
```

### Pipeline de nettoyage complet (ordre d'exécution)

```python
def clean_biography_text(raw_text: str) -> str:
    text = fix_soft_hyphens(text)       # 1. U+00AD "Lennik­-Saint" → "Lennik-Saint"
    text = fix_hyphenation(text)        # 2. "islamolo-\ngue" → "islamologue"
    text = remove_page_numbers(text)    # 3. Lignes "42" → supprimées
    text = join_paragraph_lines(text)   # 4. Jointure en texte continu ← NOUVEAU
    text = normalize_whitespace(text)   # 5. Espaces multiples, sauts de ligne
    return text
```

Impact : **-283 446 lignes** dans les fichiers de sortie (passages de ~220 lignes/fichier à ~3-5 lignes/fichier en moyenne).

---

## Résumé des constantes clés

Ces valeurs ont été calibrées empiriquement sur les 10 volumes :

```python
# engine.py
RUNNING_HEADER_MAX_SIZE = 8.0    # Police max pour un en-tête courant
RUNNING_HEADER_MAX_Y = 45        # Position Y max (pixels) pour un en-tête courant
BIO_HEADER_MIN_SIZE = 7.5        # Police min pour un en-tête de biographie

# Seuils de détection
span["size"] > 40                # Lettre de section (A, B, C...)
span["size"] > 30                # Bloc de lettre de section
bbox[3] > page_height - 35      # Zone de numéro de page (pied de page)
abs(next_y - y) < 25            # Proximité Y pour la détection d'alias
```

---

## Résumé chronologique

| Date | Commit | Description |
|------|--------|-------------|
| 26/03 | `a1c5653` | Premier commit — structure initiale |
| 30/03 | `2fc1b25` | Pipeline complet : `engine.py`, `cleaner.py`, `index_builder.py`, `app.py` |
| 30/03 | `98b368c` | Commande unique `python app.py` (index + extraction + validation) |
| 30/03 | `1b34e67` | Ajout volumes 4-10, détection bug Volume 10 |
| 31/03 | `35bd216` | Fix Volume 10 (volumes courts), validation 100%, 1 296 notices |
| 31/03 | `680f48a` | Optimisations : cache, scan unique, O(n²)→O(n), regex, DRY |
| 31/03 | `e919b35` | Texte continu dans les `.txt` (`join_paragraph_lines`) |
