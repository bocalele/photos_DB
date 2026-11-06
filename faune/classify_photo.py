#!/usr/bin/env python3
"""Classify one photo with BioCLIP 2 and display its French TAXREF taxonomy."""

from __future__ import annotations

import argparse
import csv
import hashlib
import os
import pickle
import subprocess
import sys
import tempfile
import unicodedata
from collections import defaultdict
from pathlib import Path
import re
from typing import Any

from thesaurus import ThesaurusIndex


RANK_COLUMNS = (
    ("REGNE", "KD"),
    ("PHYLUM", "PH"),
    ("CLASSE", "CL"),
    ("ORDRE", "OR"),
    ("FAMILLE", "FM"),
)


def normalize_name(name: str) -> str:
    return " ".join(name.strip().casefold().split())


def normalize_search_text(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text.casefold())
    return " ".join(
        "".join(char for char in decomposed if not unicodedata.combining(char)).split()
    )


TAXREF_COLUMNS = (
    "REGNE",
    "PHYLUM",
    "CLASSE",
    "ORDRE",
    "FAMILLE",
    "CD_NOM",
    "CD_REF",
    "RANG",
    "LB_NOM",
    "NOM_VERN",
)
# Seuls ces rangs servent à construire les arborescences et les classes ;
# conserver les 708 000 lignes de TAXREF serait inutilement coûteux.
TAXREF_RANKS = {"KD", "PH", "CL", "OR", "FM", "GN"}
# À incrémenter quand la structure sérialisée change.
CACHE_VERSION = 2


def _cache_signature(*paths: Path) -> str:
    parts = [f"v{CACHE_VERSION}", f"python{sys.version_info.major}.{sys.version_info.minor}"]
    for path in paths:
        try:
            source = Path(path).expanduser().resolve()
        except (TypeError, OSError):
            continue
        files = (
            sorted(source.glob("th*saurus-*-utf8.txt"))
            if source.is_dir()
            else [source]
        )
        parts.append(str(source))
        for file in files:
            try:
                stat = file.stat()
            except OSError:
                continue
            parts.append(f"{file.name}:{stat.st_size}:{stat.st_mtime_ns}")
    return "\n".join(parts)


class TaxrefIndex:
    def __init__(self, taxref_path: Path, thesaurus_dir: Path | str | None = None):
        vernacular_path = taxref_path.with_name(
            taxref_path.name.replace("TAXREF", "TAXVERN", 1)
        )
        if not taxref_path.is_file():
            raise FileNotFoundError(f"Fichier TAXREF introuvable : {taxref_path}")
        if not vernacular_path.is_file():
            raise FileNotFoundError(
                f"Fichier des noms vernaculaires introuvable : {vernacular_path}"
            )

        if thesaurus_dir is None:
            thesaurus_dir = Path(__file__).resolve().parent / "Thesaurus"
        thesaurus_directory = Path(thesaurus_dir).expanduser()
        signature = _cache_signature(taxref_path, vernacular_path, thesaurus_directory)
        cache_path = (
            Path(__file__).resolve().parent
            / ".cache"
            / f"taxref-index-{hashlib.sha256(signature.encode('utf-8')).hexdigest()[:24]}.pickle"
        )
        if self._load_cache(cache_path):
            return

        # Le CSV TAXREF pèse plusieurs centaines de mégaoctets. csv.reader et
        # une projection sur les dix colonnes utiles évitent de construire un
        # dictionnaire de 40 champs pour chacune des 700 000 lignes.
        self.vernaculars: dict[str, list[str]] = defaultdict(list)
        with vernacular_path.open(encoding="utf-8-sig", newline="") as source:
            reader = csv.reader(source, delimiter="\t")
            header = next(reader, None)
            if not header:
                raise ValueError(f"Fichier TAXVERN vide : {vernacular_path}")
            indexes = {name: index for index, name in enumerate(header)}
            try:
                cd_nom_index = indexes["CD_NOM"]
                name_index = indexes["LB_VERN"]
                language_index = indexes["LANGUE"]
            except KeyError as error:
                raise ValueError(
                    f"Colonne TAXVERN manquante : {error.args[0]}"
                ) from error
            for row in reader:
                if row[language_index].casefold() != "français":
                    continue
                name = row[name_index].strip()
                cd_nom = row[cd_nom_index].strip()
                if name and name not in self.vernaculars[cd_nom]:
                    self.vernaculars[cd_nom].append(name)

        species_rows: list[dict[str, str]] = []
        vernacular_rows: dict[str, dict[str, str]] = {}
        taxa_by_rank_and_name: dict[tuple[str, str], dict[str, str]] = {}
        with taxref_path.open(encoding="utf-8-sig", newline="") as source:
            reader = csv.reader(source, delimiter="\t")
            header = next(reader, None)
            if not header:
                raise ValueError(f"Fichier TAXREF vide : {taxref_path}")
            indexes = {name: index for index, name in enumerate(header)}
            try:
                column_indexes = [indexes[name] for name in TAXREF_COLUMNS]
            except KeyError as error:
                raise ValueError(
                    f"Colonne TAXREF manquante : {error.args[0]}"
                ) from error

            for source_row in reader:
                values = [source_row[index].strip() for index in column_indexes]
                row = dict(zip(TAXREF_COLUMNS, values))
                rang = row["RANG"]
                cd_nom = row["CD_NOM"]
                if rang == "ES":
                    species_rows.append(row)
                    if cd_nom in self.vernaculars:
                        vernacular_rows[cd_nom] = row
                if rang not in TAXREF_RANKS or not row["LB_NOM"]:
                    continue
                key = (rang, normalize_name(row["LB_NOM"]))
                current = taxa_by_rank_and_name.get(key)
                if current is None or row["CD_NOM"] == row["CD_REF"]:
                    taxa_by_rank_and_name[key] = row

        accepted_species = {
            row["CD_NOM"]: row
            for row in species_rows
            if row.get("CD_NOM") == row.get("CD_REF")
        }
        self.species_by_name: dict[str, dict[str, str]] = {}
        self.search_terms: dict[str, set[str]] = {
            reference_id: set() for reference_id in accepted_species
        }
        for row in species_rows:
            reference_id = row.get("CD_REF", "")
            accepted = accepted_species.get(reference_id)
            if accepted is not None:
                scientific_name = row.get("LB_NOM", "").strip()
                self.species_by_name[normalize_name(scientific_name)] = accepted
                # La recherche accepte le nom scientifique (accepté ou synonyme)
                # autant que le nom vernaculaire.
                if scientific_name:
                    self.search_terms[reference_id].add(scientific_name)
        vernacular_terms: dict[str, set[str]] = {
            reference_id: set() for reference_id in accepted_species
        }
        for cd_nom, names in self.vernaculars.items():
            row = vernacular_rows.get(cd_nom)
            if row is None:
                continue
            reference_id = row.get("CD_REF", "")
            if reference_id in self.search_terms:
                self.search_terms[reference_id].update(names)
                vernacular_terms[reference_id].update(names)
        # Les noms vernaculaires restent seuls reconnus comme tels (drapeau
        # « nom vernaculaire présent » dans les métadonnées) ; la recherche
        # manuelle, elle, accepte aussi les noms scientifiques.
        self.species_by_vernacular: dict[str, dict[str, str]] = {}
        for reference_id, names in vernacular_terms.items():
            accepted = accepted_species[reference_id]
            for name in names:
                self.species_by_vernacular.setdefault(normalize_search_text(name), accepted)

        # TAXREF regroupe parfois plusieurs synonymes vernaculaires dans une
        # seule valeur (« Gendarme, Pyrrhocore, Soldat, Suisse »). Un index
        # par synonyme permet de reconnaître aussi « Gendarme » seul, sans
        # laisser le thésaurus local imposer son ancien nom scientifique.
        synonym_candidates: dict[str, dict[str, str]] = {}
        ambiguous_synonyms: set[str] = set()
        for reference_id, names in vernacular_terms.items():
            accepted = accepted_species[reference_id]
            for name in names:
                for synonym in re.split(r"[,;]", name):
                    key = normalize_search_text(synonym.strip())
                    if not key:
                        continue
                    previous = synonym_candidates.get(key)
                    if previous is not None and previous.get("CD_NOM") != accepted.get("CD_NOM"):
                        ambiguous_synonyms.add(key)
                    else:
                        synonym_candidates[key] = accepted
        for key in ambiguous_synonyms:
            synonym_candidates.pop(key, None)
        self.vernacular_synonyms = synonym_candidates

        self.taxa_by_rank_and_name = taxa_by_rank_and_name
        self.accepted_species = accepted_species

        # Classes proposées pour rattacher un taxon absent de TAXREF et des
        # thésaurus. La valeur retenue est le libellé vernaculaire quand il
        # existe (« Oiseaux »), sinon le nom scientifique (« Aves »).
        self.class_options_by_key: dict[str, dict[str, str]] = {}
        for row in accepted_species.values():
            scientific = row.get("CLASSE", "").strip()
            if not scientific:
                continue
            key = normalize_name(scientific)
            if key in self.class_options_by_key:
                continue
            class_row = self.taxa_by_rank_and_name.get(("CL", key))
            label = self.vernacular_name(class_row) if class_row else scientific
            self.class_options_by_key[key] = {
                "label": label or scientific,
                "scientificName": scientific,
            }

        self.thesaurus = (
            ThesaurusIndex(thesaurus_directory)
            if thesaurus_directory.is_dir()
            else None
        )
        self.thesaurus_root_keys = {
            normalize_name(node.name)
            for node in (self.thesaurus.nodes if self.thesaurus is not None else [])
            if node.parent is None
        }
        self._save_cache(cache_path)

    def _load_cache(self, path: Path) -> bool:
        try:
            with path.open("rb") as source:
                cached = pickle.load(source)
        except (
            OSError,
            EOFError,
            pickle.PickleError,
            AttributeError,
            ImportError,
            TypeError,
            ValueError,
        ):
            return False
        if not isinstance(cached, TaxrefIndex):
            return False
        self_dict = getattr(cached, "__dict__", None)
        if not isinstance(self_dict, dict) or "species_by_name" not in self_dict:
            return False
        # L'objet est reconstruit par pickle, puis ses attributs remplacent
        # ceux de cette instance. Le cache ne contient aucun fichier photo.
        self.__dict__.update(self_dict)
        return True

    def _save_cache(self, path: Path) -> None:
        temporary = None
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            descriptor, temporary = tempfile.mkstemp(
                prefix=path.name + ".",
                suffix=".tmp",
                dir=path.parent,
            )
            with os.fdopen(descriptor, "wb") as target:
                pickle.dump(self, target, protocol=pickle.HIGHEST_PROTOCOL)
            os.replace(temporary, path)
            temporary = None
        except (OSError, pickle.PickleError, TypeError, AttributeError, ValueError):
            pass
        finally:
            if temporary is not None:
                try:
                    os.unlink(temporary)
                except OSError:
                    pass

    def vernacular_name(self, row: dict[str, str]) -> str:
        names = self.vernaculars.get(row.get("CD_NOM", ""), [])
        if names:
            return names[0]
        name = row.get("NOM_VERN", "").strip()
        if row.get("RANG") == "ES":
            return name.split(",")[0].strip()
        return name

    def lineage(self, scientific_name: str) -> tuple[str | None, str | None, list[str]]:
        species = self.species_by_name.get(normalize_name(scientific_name))
        if species is None:
            if self.thesaurus is not None:
                return self.thesaurus.lineage(scientific_name)
            return None, None, []

        species_name = self.vernacular_name(species)
        lineage: list[str] = []
        for column, rank in RANK_COLUMNS:
            name = species.get(column, "").strip()
            if not name:
                continue
            taxon = self.taxa_by_rank_and_name.get((rank, normalize_name(name)))
            vernacular = self.vernacular_name(taxon) if taxon else ""
            if vernacular:
                lineage.append(vernacular)

        genus = species.get("LB_NOM", "").split()[0]
        if genus:
            genus_row = self.taxa_by_rank_and_name.get(("GN", normalize_name(genus)))
            genus_vernacular = self.vernacular_name(genus_row) if genus_row else ""
            if genus_vernacular:
                lineage.append(genus_vernacular)
        if species_name:
            lineage.append(species_name)
        return species.get("LB_NOM"), species_name or None, lineage

    def resolve_species(self, scientific_name: str) -> dict[str, Any] | None:
        canonical, vernacular, lineage = self.lineage(scientific_name)
        if canonical is None:
            return None
        return {
            "scientificName": canonical,
            "vernacularName": vernacular or "",
            "lineage": lineage,
        }

    def resolve_vernacular(self, vernacular_name: str) -> dict[str, Any] | None:
        row = self.species_by_vernacular.get(normalize_search_text(vernacular_name))
        if row is not None:
            return self.resolve_species(row["LB_NOM"])
        if self.thesaurus is not None:
            return self.thesaurus.resolve_vernacular(vernacular_name)
        return None

    def _taxref_taxon(self, species: dict[str, str]) -> dict[str, Any] | None:
        taxon = self.resolve_species(species.get("LB_NOM", ""))
        if taxon is None:
            return None
        taxon["authority"] = "taxref"
        taxon["taxrefId"] = species.get("CD_NOM", "")
        return taxon

    def _thesaurus_taxon(self, value: str) -> dict[str, Any] | None:
        """Résout un libellé dans le thésaurus local, avec sa source."""
        if self.thesaurus is None:
            return None
        node = self.thesaurus.node_for_scientific(value)
        if node is None:
            candidates = self.thesaurus.by_vernacular.get(
                normalize_search_text(value), []
            )
            if not candidates:
                return None
            leaves = [item for item in candidates if item.scientific and not item.children]
            pool = leaves or candidates
            node = min(pool, key=lambda item: (len(item.lineage()), item.name.casefold()))
        if node.scientific:
            species = self.species_by_name.get(normalize_name(node.scientific))
            if species is not None:
                taxon = self._taxref_taxon(species)
                if taxon is not None:
                    return taxon
        return {
            "scientificName": node.scientific or None,
            "vernacularName": node.name,
            "lineage": node.lineage(),
            "authority": "thesaurus",
        }

    def resolve_taxon(self, value: str) -> dict[str, Any] | None:
        """Résout un mot-clé taxonomique en donnant la priorité à TAXREF.

        TAXREF est la référence pour les noms scientifiques et vernaculaires
        français. Le thésaurus local ne sert que de repli pour les noms absents
        de TAXREF (races, groupes, anciens libellés).
        """
        text = str(value or "").strip()
        if not text:
            return None

        species = self.species_by_name.get(normalize_name(text))
        if species is not None:
            return self._taxref_taxon(species)

        species = self.species_by_vernacular.get(normalize_search_text(text))
        if species is not None:
            return self._taxref_taxon(species)

        species = getattr(self, "vernacular_synonyms", {}).get(
            normalize_search_text(text)
        )
        if species is not None:
            return self._taxref_taxon(species)

        # Une valeur peut regrouper plusieurs synonymes séparés par des
        # virgules. On essaie chaque partie avant de revenir au thésaurus.
        parts = [part.strip() for part in re.split(r"[,;]", text) if part.strip()]
        if len(parts) > 1:
            for part in parts:
                synonym = getattr(self, "vernacular_synonyms", {}).get(
                    normalize_search_text(part)
                )
                if synonym is not None:
                    return self._taxref_taxon(synonym)
                species = self.species_by_vernacular.get(
                    normalize_search_text(part)
                )
                if species is not None:
                    return self._taxref_taxon(species)

        return self._thesaurus_taxon(text)

    def resolve_hierarchy(self, path: str) -> dict[str, Any] | None:
        """Résout une arborescence en privilégiant TAXREF."""
        segments = [
            segment.strip()
            for segment in re.split(r"[|/>\\\\]+", path)
            if segment.strip()
        ]
        if len(segments) < 2:
            return None
        taxon = self.resolve_taxon(segments[-1])
        if taxon is not None:
            lineage = [normalize_search_text(name) for name in taxon.get("lineage") or []]
            position = 0
            for segment in segments:
                normalized = normalize_search_text(segment)
                try:
                    position = lineage.index(normalized, position) + 1
                except ValueError:
                    break
            else:
                return taxon
        if self.thesaurus is not None:
            resolved = self.thesaurus.resolve_hierarchical_path(path)
            if resolved is not None:
                resolved = dict(resolved)
                scientific = resolved.get("scientificName")
                species = (
                    self.species_by_name.get(normalize_name(scientific))
                    if scientific
                    else None
                )
                if species is not None:
                    taxon = self._taxref_taxon(species)
                    if taxon is not None:
                        return taxon
                resolved["authority"] = "thesaurus"
                return resolved
        return None

    def resolve_hierarchical_path(self, path: str) -> dict[str, Any] | None:
        segments = [
            segment.strip()
            for segment in re.split(r"[|/>\\\\]+", path)
            if segment.strip()
        ]
        if len(segments) < 2:
            return None
        taxon = self.resolve_vernacular(segments[-1])
        if taxon is None:
            return None
        lineage = [normalize_search_text(name) for name in taxon["lineage"]]
        position = 0
        for segment in segments:
            normalized = normalize_search_text(segment)
            try:
                position = lineage.index(normalized, position) + 1
            except ValueError:
                break
        else:
            return taxon
        if self.thesaurus is not None:
            return self.thesaurus.resolve_hierarchical_path(path)
        return None

    def class_options(self) -> list[dict[str, str]]:
        """Grandes classes des thésaurus, comme dans le sélecteur.

        Les racines « Oiseaux », « Plantes terrestres », « Insectes s.l. »…
        correspondent aux groupes affichés dans la vue de sélection. Elles
        remplacent la longue liste des classes TAXREF pour le rattachement
        manuel des taxons inconnus.
        """
        if self.thesaurus is not None:
            roots = self.thesaurus.root_names()
            if roots:
                return [
                    {"label": name, "scientificName": name}
                    for name in roots
                ]
        options = list(self.class_options_by_key.values())
        return sorted(
            options,
            key=lambda item: (normalize_search_text(item["label"]), item["scientificName"]),
        )

    def is_class_name(self, value: str) -> bool:
        key = normalize_name(value)
        if not key:
            return False
        if key in self.class_options_by_key or key in self.thesaurus_root_keys:
            return True
        return any(
            normalize_name(item["label"]) == key
            for item in self.class_options_by_key.values()
        )

    def search_species(self, query: str, limit: int = 20) -> list[dict[str, Any]]:
        needle = normalize_search_text(query)
        if len(needle) < 2:
            return []
        matches = []
        for cd_nom, row in self.accepted_species.items():
            terms = self.search_terms.get(cd_nom, set())
            match_rank = min(
                (
                    0 if normalize_search_text(term).startswith(needle)
                    else 1 if needle in normalize_search_text(term)
                    else 2
                    for term in terms
                    if needle in normalize_search_text(term)
                ),
                default=3,
            )
            if match_rank == 3:
                continue
            resolved = self.resolve_species(row["LB_NOM"])
            if resolved:
                matches.append((match_rank, normalize_search_text(resolved["vernacularName"]), resolved))
        matches.sort(key=lambda item: (item[0], item[1]))
        results = [item[2] for item in matches[:limit]]
        if self.thesaurus is not None:
            known = {normalize_name(item["scientificName"]) for item in results}
            for item in self.thesaurus.search(query, limit=limit):
                if normalize_name(item["scientificName"]) not in known:
                    results.append(item)
                    if len(results) >= limit:
                        break
        return results


def find_taxref_path(directory: Path) -> Path:
    matches = sorted(directory.glob("TAXREFv*.txt"))
    if len(matches) != 1:
        if not matches:
            raise FileNotFoundError(
                f"Aucun fichier TAXREFv*.txt trouvé dans {directory}"
            )
        raise ValueError(
            f"Plusieurs fichiers TAXREF trouvés dans {directory}; "
            "précisez-en un avec --taxref."
        )
    return matches[0]


def load_photo(image_path: Path, temporary_directory: Path) -> Any:
    from PIL import Image, ImageOps, UnidentifiedImageError

    try:
        with Image.open(image_path) as source:
            source.load()
            return ImageOps.exif_transpose(source).convert("RGB")
    except (OSError, ValueError, UnidentifiedImageError) as image_error:
        converted_path = temporary_directory / "converted.jpg"
        try:
            result = subprocess.run(
                ["sips", "-s", "format", "jpeg", str(image_path), "--out", str(converted_path)],
                capture_output=True,
                text=True,
                timeout=120,
                check=False,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired) as conversion_error:
            raise ValueError(
                f"Impossible de lire l’image {image_path}: {image_error}"
            ) from conversion_error
        if result.returncode != 0:
            details = result.stderr.strip() or result.stdout.strip()
            raise ValueError(
                f"Impossible de lire l’image {image_path}: {image_error}. "
                f"La conversion macOS a aussi échoué: {details}"
            ) from image_error
        try:
            with Image.open(converted_path) as source:
                source.load()
                return ImageOps.exif_transpose(source).convert("RGB")
        except (OSError, ValueError, UnidentifiedImageError) as converted_error:
            raise ValueError(
                f"Le fichier {image_path} n’a pas pu être ouvert après conversion: "
                f"{converted_error}"
            ) from converted_error


def display_prediction(
    prediction: dict[str, Any], taxref: TaxrefIndex, number: int
) -> None:
    details = prediction_details(prediction, taxref)
    print(
        f"{number}. {details['scientificName']} / {details['vernacularName']} "
        f"({details['score'] * 100:.1f} %)"
    )
    if details["lineage"]:
        print(f"   Arborescence : {' > '.join(details['lineage'])}")
    else:
        print("   Arborescence : absente de TAXREF pour ce nom scientifique.")


def prediction_details(
    prediction: dict[str, Any], taxref: TaxrefIndex
) -> dict[str, Any]:
    scientific_name = str(prediction.get("species", "")).strip()
    if not scientific_name:
        raise ValueError("BioCLIP n’a pas fourni de nom scientifique pour ce résultat.")

    taxref_name, vernacular, lineage = taxref.lineage(scientific_name)
    scientific_name = taxref_name or scientific_name
    resolver = getattr(taxref, "resolve_species", None)
    custom = (
        resolver(scientific_name) is None
        if callable(resolver)
        else not lineage
    )
    return {
        "scientificName": scientific_name,
        "vernacularName": vernacular or scientific_name,
        "score": float(prediction.get("score", 0.0)),
        "lineage": lineage,
        # BioCLIP peut proposer un nom absent de TAXREF et des thésaurus.
        # La validation demandera alors à l'utilisateur de choisir une classe.
        "custom": custom,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Classe une photo avec BioCLIP 2 et affiche les noms français "
            "ainsi que la taxonomie de TAXREF."
        )
    )
    parser.add_argument("image", type=Path, help="Photo à analyser (PSD, JPEG, etc.)")
    parser.add_argument(
        "--taxref",
        type=Path,
        help="Fichier TAXREFv*.txt (détecté automatiquement dans faune/TAXREF)",
    )
    parser.add_argument("--top", type=int, default=5, help="Nombre de résultats (défaut : 5)")
    parser.add_argument("--device", default="mps", help="Appareil PyTorch (défaut : mps)")
    args = parser.parse_args()

    if args.top < 1:
        parser.error("--top doit être supérieur ou égal à 1")
    if not args.image.is_file():
        parser.error(f"Image introuvable : {args.image}")

    project_directory = Path(__file__).resolve().parent
    try:
        taxref_path = args.taxref or find_taxref_path(project_directory / "TAXREF")
        taxref = TaxrefIndex(taxref_path, project_directory / "Thesaurus")
        from bioclip.predict import Rank, TreeOfLifeClassifier

        with tempfile.TemporaryDirectory(prefix="bioclip-") as temp_dir:
            image = load_photo(args.image, Path(temp_dir))
            classifier = TreeOfLifeClassifier(device=args.device)
            predictions = classifier.predict(
                images=[image],
                rank=Rank.SPECIES,
                k=args.top,
                batch_size=1,
            )
        for index, prediction in enumerate(predictions, start=1):
            display_prediction(prediction, taxref, index)
        return 0
    except (FileNotFoundError, OSError, ValueError, RuntimeError) as error:
        print(f"Erreur : {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
