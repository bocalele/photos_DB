"""Règles de lecture et de classement des annotations XMP.

Les métadonnées d'anciennes photos contiennent parfois une liste de mots-clés
sérialisée dans une seule chaîne (``"ESPAGNE MONTAGNES VACANCES"``) ou même une
représentation Python de liste (``"['A', 'B']"``). Ce module normalise ces
valeurs, éclate les listes et distingue les mots-clés des personnes et des
taxons.

Il ne dépend ni de pandas ni d'ExifTool : il est utilisable par le
reconstructeur, le script de mise à jour et les hooks d'écriture des applis.
"""

from __future__ import annotations

import importlib.util
import re
import sys
import unicodedata
from pathlib import Path
from typing import Any, Callable, Iterable

PROJECT_DIR = Path(__file__).resolve().parent.parent
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))


# --------------------------------------------------------------------------- #
# Normalisation
# --------------------------------------------------------------------------- #


def normalize_name(text: str | None) -> str:
    """Clé de comparaison sans accents, sans casse et sans tirets."""
    if not text:
        return ""
    value = unicodedata.normalize("NFKD", str(text))
    value = "".join(char for char in value if not unicodedata.combining(char))
    value = value.replace("’", "'").replace("-", " ")
    return " ".join(value.casefold().split())


def normalize_search_text(text: str | None) -> str:
    """Normalisation du thésaurus (accents retirés, espaces compactés)."""
    if not text:
        return ""
    decomposed = unicodedata.normalize("NFKD", str(text).casefold())
    return " ".join(
        "".join(char for char in decomposed if not unicodedata.combining(char)).split()
    )


# --------------------------------------------------------------------------- #
# Décodage des listes Python écrites dans un champ XMP
# --------------------------------------------------------------------------- #

_OPEN_QUOTES = {"'": "'", '"': '"', "‘": "’", "“": "”"}
_CLOSING_QUOTES = set("'\"’”")


def strip_outer_quotes(text: str) -> str:
    text = str(text or "").strip()
    if len(text) >= 2 and text[0] in "'\"‘“" and text[-1] in _CLOSING_QUOTES:
        return text[1:-1].strip()
    return text


def list_items(text: str) -> list[tuple[str, bool]]:
    """Décode une valeur qui ressemble à une liste Python, même tronquée.

    Retourne des couples ``(valeur, tronquée)``. Le dernier élément est marqué
    tronqué quand le guillemet fermant manque ; l'appelant peut alors tenter de
    le compléter avec le thésaurus ou l'ignorer.
    """
    body = str(text or "").strip()
    if body.startswith("["):
        body = body[1:]
    if "]" in body:
        body = body.split("]", 1)[0]
    items: list[tuple[str, bool]] = []
    index = 0
    length = len(body)
    while index < length:
        while index < length and (body[index].isspace() or body[index] == ","):
            index += 1
        if index >= length:
            break
        if body[index] in _OPEN_QUOTES:
            opener = body[index]
            closer = _OPEN_QUOTES[opener]
            index += 1
            chars: list[str] = []
            truncated = True
            while index < length:
                char = body[index]
                if char == closer:
                    lookahead = index + 1
                    while lookahead < length and body[lookahead].isspace():
                        lookahead += 1
                    if lookahead >= length or body[lookahead] in ",]":
                        index = lookahead
                        truncated = False
                        break
                chars.append(char)
                index += 1
            value = "".join(chars).strip()
            if truncated and value and value[-1] in _CLOSING_QUOTES:
                value = value[:-1].rstrip()
                truncated = False
            if value:
                items.append((value, truncated))
        else:
            start = index
            while index < length and body[index] != ",":
                index += 1
            value = body[start:index].strip()
            if value.endswith("]"):
                value = value[:-1].strip()
            if value:
                items.append((value, False))
    return items


def _looks_like_python_list(text: str) -> bool:
    value = str(text or "").strip()
    return value.startswith("[") and (
        value.endswith("]") or any(quote in value for quote in "'\"‘’“”")
    )


def raw_items(value: Any) -> list[tuple[str, bool]]:
    """Décode une valeur XMP en éléments bruts, sans éclater les phrases."""
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        result: list[tuple[str, bool]] = []
        for item in value:
            result.extend(raw_items(item))
        return result
    text = str(value).strip()
    if not text:
        return []
    if _looks_like_python_list(text):
        return list_items(text)
    return [(strip_outer_quotes(text), False)]


# --------------------------------------------------------------------------- #
# Éclatement des phrases en mots-clés
# --------------------------------------------------------------------------- #

_CONNECTORS = {
    "a", "à", "au", "aux", "d", "de", "des", "du", "en", "et", "l", "la",
    "le", "les", "of", "the", "and", "sur", "sous", "chez", "dans",
}


def _word_tokens(text: str) -> list[str]:
    return [
        token.strip(" ,;:")
        for token in re.split(r"\s+", str(text or "").strip())
        if token.strip(" ,;:")
    ]


def _has_letter(token: str) -> bool:
    return any(char.isalpha() for char in token)


def looks_like_concatenated_keywords(text: str) -> bool:
    """Heuristique pour reconnaître une liste de tags collée par des espaces.

    Les anciennes annotations utilisent souvent des mots entièrement en
    majuscules (``MINXTATE ESPAGNE MONTAGNES``). Une suite de trois mots
    capitalisés sans petit mot de liaison est également considérée comme une
    liste. Les phrases ordinaires (``king's college``, ``Cheese wrings``,
    ``Porto da Cruz``) restent intactes.
    """
    if "," in text or ";" in text:
        # Une virgule sépare souvent des synonymes dans un nom vernaculaire
        # (« Cheval sauvage, Tarpan ») : on ne l'utilise pas comme séparateur.
        return False
    words = [word for word in _word_tokens(text) if _has_letter(word)]
    if len(words) < 2:
        return False
    if any(len(word) > 1 and word.isupper() for word in words):
        return True
    if len(words) >= 3 and all(word[:1].isupper() for word in words):
        if not any(word.casefold() in _CONNECTORS for word in words):
            return True
    return False


def _quoted_parts(text: str) -> tuple[list[str], list[str], bool]:
    """Sépare les fragments entre guillemets des fragments libres.

    Les apostrophes internes (``king's``) ne sont pas prises pour des
    guillemets. Retourne ``(phrases_citées, fragments_libres, a_des_citations)``.
    """
    quoted: list[str] = []
    free: list[str] = []
    buffer: list[str] = []
    index = 0
    length = len(text)
    while index < length:
        char = text[index]
        opens_quote = (
            char in _OPEN_QUOTES
            and (index == 0 or text[index - 1].isspace())
            and index + 1 < length
            and not text[index + 1].isspace()
        )
        if opens_quote:
            closer = _OPEN_QUOTES[char]
            end = index + 1
            while end < length:
                if text[end] == closer:
                    after = end + 1
                    if after >= length or text[after].isspace() or text[after] in ",;":
                        break
                end += 1
            if end < length and text[end] == closer:
                fragment = "".join(buffer).strip()
                if fragment:
                    free.append(fragment)
                buffer = []
                value = text[index + 1:end].strip()
                if value:
                    quoted.append(value)
                index = end + 1
                continue
        buffer.append(char)
        index += 1
    fragment = "".join(buffer).strip()
    if fragment:
        free.append(fragment)
    return quoted, free, bool(quoted)


def split_keyword_text(text: str) -> list[str]:
    """Éclate une valeur si elle ressemble à une liste de tags.

    Une phrase ordinaire est conservée telle quelle. Une suite de tags en
    majuscules ou une valeur contenant des fragments entre guillemets est
    découpée en mots-clés atomiques.
    """
    value = str(text or "").strip()
    if not value:
        return []
    quoted, free, has_quotes = _quoted_parts(value)
    result: list[str] = []
    for fragment in free:
        if has_quotes or looks_like_concatenated_keywords(fragment):
            result.extend(_word_tokens(fragment))
        else:
            result.append(fragment)
    result.extend(quoted)
    cleaned = []
    seen = set()
    for item in result:
        item = str(item or "").strip()
        if not item or not _has_letter(item):
            continue
        key = normalize_name(item)
        if not key or key in seen:
            continue
        seen.add(key)
        cleaned.append(item)
    return cleaned


# --------------------------------------------------------------------------- #
# Thésaurus
# --------------------------------------------------------------------------- #


def load_thesaurus(directory: Path | str | None):
    if not directory:
        return None
    directory = Path(directory).expanduser()
    if not directory.is_dir():
        return None
    module_path = PROJECT_DIR / "faune" / "thesaurus.py"
    spec = importlib.util.spec_from_file_location("photos_faune_thesaurus", module_path)
    if spec is None or spec.loader is None:
        return None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module.ThesaurusIndex(directory)


def choose_node(candidates):
    if not candidates:
        return None
    leaves = [node for node in candidates if node.scientific and not node.children]
    pool = leaves or candidates
    return min(pool, key=lambda node: (len(node.lineage()), node.name.casefold()))


def taxon_for_text(thesaurus, text: str):
    if thesaurus is None:
        return None
    value = str(text or "").strip()
    if not value:
        return None
    node = thesaurus.by_scientific.get(normalize_name(value))
    if node is not None:
        return node
    return choose_node(thesaurus.by_vernacular.get(normalize_search_text(value)))


def taxon_record(node) -> dict:
    return {
        "scientificName": node.scientific or None,
        "vernacularName": node.name,
        "lineage": node.lineage(),
    }


def _thesaurus_keys(thesaurus) -> set[str]:
    cached = getattr(thesaurus, "_photos_keyword_keys", None)
    if cached is not None:
        return cached
    keys: set[str] = set()
    for node in getattr(thesaurus, "nodes", []):
        keys.add(normalize_name(node.name))
        if node.scientific:
            keys.add(normalize_name(node.scientific))
    thesaurus._photos_keyword_keys = keys
    return keys


def is_taxonomy_text(thesaurus, text: str) -> bool:
    if thesaurus is None:
        return False
    key = normalize_name(text)
    return bool(key and key in _thesaurus_keys(thesaurus))


def complete_truncated_keyword(text: str, thesaurus) -> str | None:
    """Complète un nom tronqué si le thésaurus ne propose qu'une suite."""
    if thesaurus is None:
        return None
    needle = normalize_search_text(text)
    if not needle:
        return None
    exact = thesaurus.by_vernacular.get(needle)
    if exact:
        return exact[0].name
    scientific_key = normalize_name(text)
    exact_scientific = thesaurus.by_scientific.get(scientific_key)
    if exact_scientific is not None:
        return exact_scientific.name

    matches: dict[str, str] = {}
    for key, nodes in thesaurus.by_vernacular.items():
        if key.startswith(needle):
            for node in nodes:
                matches.setdefault(key, node.name)
    if scientific_key:
        for key, node in thesaurus.by_scientific.items():
            if key.startswith(scientific_key):
                matches.setdefault(key, node.name)
    if len(matches) == 1:
        return next(iter(matches.values()))
    return None


# --------------------------------------------------------------------------- #
# Valeurs XMP
# --------------------------------------------------------------------------- #


def keyword_values(
    value: Any,
    thesaurus=None,
    *,
    preserve: Callable[[str], bool] | None = None,
) -> list[str]:
    """Valeurs de mots-clés éclatées, sans représentation de liste Python."""
    result: list[str] = []
    seen: set[str] = set()
    for text, truncated in raw_items(value):
        text = text.strip()
        if not text:
            continue
        if truncated:
            completed = complete_truncated_keyword(text, thesaurus)
            if completed is None:
                # Un dernier élément tronqué inutilisable ne doit pas créer
                # un faux mot-clé (par exemple « Passérifor »).
                continue
            text = completed
        if preserve and preserve(text):
            items = [text]
        else:
            items = split_keyword_text(text)
        for item in items:
            key = normalize_name(item)
            if key and key not in seen:
                seen.add(key)
                result.append(item)
    return result


def hierarchy_values(value: Any, thesaurus=None) -> list[str]:
    """Valeurs de mots-clés hiérarchiques, sans éclatement sur les espaces."""
    result: list[str] = []
    seen: set[str] = set()
    for text, truncated in raw_items(value):
        text = text.strip()
        if not text:
            continue
        if truncated:
            completed = complete_truncated_keyword(text, thesaurus)
            if completed is None:
                continue
            text = completed
        key = normalize_name(text)
        if key and key not in seen:
            seen.add(key)
            result.append(text)
    return result


# --------------------------------------------------------------------------- #
# Classement
# --------------------------------------------------------------------------- #


def _most_specific(taxa: list[dict]) -> list[dict]:
    kept: list[dict] = []
    seen: set[tuple[str, ...]] = set()
    for taxon in taxa:
        lineage = tuple(taxon.get("lineage") or [])
        if not lineage or lineage in seen:
            continue
        seen.add(lineage)
        if any(
            len(other.get("lineage") or []) > len(lineage)
            and tuple((other.get("lineage") or [])[: len(lineage)]) == lineage
            for other in taxa
        ):
            continue
        kept.append(taxon)
    return kept


def classify_keywords(
    keywords: Iterable[str],
    hierarchies: Iterable[str],
    thesaurus,
    person_index: dict[str, int],
) -> tuple[list[int], list[dict], list[str]]:
    """Sépare personnes, taxons et mots-clés non taxonomiques.

    Les personnes ne sont jamais dupliquées dans les mots-clés. Un libellé
    trouvé dans le thésaurus (nom vernaculaire ou scientifique) est un taxon :
    il ne doit pas apparaître dans ``keywords``.
    """
    person_ids: list[int] = []
    taxa: list[dict] = []
    labels: list[str] = []
    seen_labels: set[str] = set()

    for raw_keyword in keywords:
        keyword = str(raw_keyword or "").strip()
        if not keyword:
            continue
        key = normalize_name(keyword)
        if key in person_index:
            person_ids.append(person_index[key])
            continue
        node = taxon_for_text(thesaurus, keyword)
        if node is not None:
            taxa.append(taxon_record(node))
            continue
        if is_taxonomy_text(thesaurus, keyword):
            continue
        if key and key not in seen_labels:
            seen_labels.add(key)
            labels.append(keyword)

    for hierarchy in hierarchies:
        text = str(hierarchy or "").strip()
        if not text:
            continue
        segments = [part.strip() for part in re.split(r"[|/>\\]+", text) if part.strip()]
        if len(segments) < 2:
            continue
        node = None
        if thesaurus is not None:
            node = thesaurus.by_path.get(
                tuple(normalize_search_text(segment) for segment in segments)
            )
        if node is not None and node.scientific:
            taxa.append(taxon_record(node))
            continue

        # Une hiérarchie qui contient au moins un taxon connu est une
        # hiérarchie taxonomique : ses niveaux supérieurs (« Animaux »,
        # « Chordés », « Passereaux ») ne doivent pas devenir des mots-clés,
        # même s'ils sont absents du thésaurus local.
        segment_nodes = [taxon_for_text(thesaurus, segment) for segment in segments]
        if any(segment_node is not None for segment_node in segment_nodes):
            for segment, segment_node in zip(segments, segment_nodes):
                if segment_node is not None:
                    taxa.append(taxon_record(segment_node))
            continue

        for segment in segments:
            key = normalize_name(segment)
            if not key:
                continue
            if key in person_index:
                person_ids.append(person_index[key])
                continue
            if is_taxonomy_text(thesaurus, segment):
                continue
            if key not in seen_labels:
                seen_labels.add(key)
                labels.append(segment)

    return sorted(set(person_ids)), _most_specific(taxa), labels


def is_malformed_keyword_label(label: str) -> bool:
    """Vrai si un libellé est encore une liste Python ou une chaîne citée."""
    text = str(label or "").strip()
    if not text:
        return True
    items = raw_items(text)
    if len(items) != 1:
        return True
    value, truncated = items[0]
    return bool(truncated or value.strip() != text)
