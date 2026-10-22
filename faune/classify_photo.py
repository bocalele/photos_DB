#!/usr/bin/env python3
"""Classify one photo with BioCLIP 2 and display its French TAXREF taxonomy."""

from __future__ import annotations

import argparse
import csv
import subprocess
import sys
import tempfile
import unicodedata
from collections import defaultdict
from pathlib import Path
import re
from typing import Any


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


class TaxrefIndex:
    def __init__(self, taxref_path: Path):
        vernacular_path = taxref_path.with_name(
            taxref_path.name.replace("TAXREF", "TAXVERN", 1)
        )
        if not taxref_path.is_file():
            raise FileNotFoundError(f"Fichier TAXREF introuvable : {taxref_path}")
        if not vernacular_path.is_file():
            raise FileNotFoundError(
                f"Fichier des noms vernaculaires introuvable : {vernacular_path}"
            )

        self.vernaculars: dict[str, list[str]] = defaultdict(list)
        with vernacular_path.open(encoding="utf-8-sig", newline="") as source:
            for row in csv.DictReader(source, delimiter="\t"):
                if row.get("LANGUE", "").casefold() != "français":
                    continue
                name = row.get("LB_VERN", "").strip()
                if name and name not in self.vernaculars[row.get("CD_NOM", "")]:
                    self.vernaculars[row.get("CD_NOM", "")].append(name)

        rows_by_id: dict[str, dict[str, str]] = {}
        species_rows: list[dict[str, str]] = []
        with taxref_path.open(encoding="utf-8-sig", newline="") as source:
            for row in csv.DictReader(source, delimiter="\t"):
                cd_nom = row.get("CD_NOM", "").strip()
                if cd_nom:
                    rows_by_id[cd_nom] = row
                if row.get("RANG") == "ES":
                    species_rows.append(row)

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
        self.vernacular_terms: dict[str, set[str]] = {
            reference_id: set() for reference_id in accepted_species
        }
        for cd_nom, names in self.vernaculars.items():
            row = rows_by_id.get(cd_nom)
            if row is None or row.get("RANG") != "ES":
                continue
            reference_id = row.get("CD_REF", "")
            if reference_id in self.search_terms:
                self.search_terms[reference_id].update(names)
                self.vernacular_terms[reference_id].update(names)
        # Les noms vernaculaires restent seuls reconnus comme tels (drapeau
        # « nom vernaculaire présent » dans les métadonnées) ; la recherche
        # manuelle, elle, accepte aussi les noms scientifiques.
        self.species_by_vernacular: dict[str, dict[str, str]] = {}
        for reference_id, names in self.vernacular_terms.items():
            accepted = accepted_species[reference_id]
            for name in names:
                self.species_by_vernacular.setdefault(normalize_search_text(name), accepted)

        self.taxa_by_rank_and_name: dict[tuple[str, str], dict[str, str]] = {}
        for row in rows_by_id.values():
            key = (row.get("RANG", ""), normalize_name(row.get("LB_NOM", "")))
            if not key[1]:
                continue
            current = self.taxa_by_rank_and_name.get(key)
            if current is None or row.get("CD_NOM") == row.get("CD_REF"):
                self.taxa_by_rank_and_name[key] = row
        self.accepted_species = accepted_species

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
        return self.resolve_species(row["LB_NOM"]) if row else None

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
                return None
        return taxon

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
        return [item[2] for item in matches[:limit]]


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
    return {
        "scientificName": scientific_name,
        "vernacularName": vernacular or scientific_name,
        "score": float(prediction.get("score", 0.0)),
        "lineage": lineage,
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
        taxref = TaxrefIndex(taxref_path)
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
