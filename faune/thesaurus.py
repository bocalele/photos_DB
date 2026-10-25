#!/usr/bin/env python3
"""Index des thésaurus naturalistes au format Lightroom.

Les fichiers `th*saurus-*-utf8.txt` sont des arbres indentés par des tabulations.
Un nœud ordinaire est un nom vernaculaire ; ses enfants entre accolades donnent
le nom scientifique puis le nom anglais :

    Oiseaux
    \tAccipitriformes
    \t\tAccipitridés
    \t\t\tAigle criard
    \t\t\t\t{Aquila clanga}
    \t\t\t\t{Greater Spotted Eagle}

Cet index sert de repli quand BioCLIP propose un nom scientifique absent de
TAXREF : on retrouve alors le nom vernaculaire et l'arborescence du thésaurus.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import re
import unicodedata


def normalize_name(name: str) -> str:
    return " ".join(str(name).strip().casefold().split())


def normalize_search_text(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", str(text).casefold())
    return " ".join(
        "".join(char for char in decomposed if not unicodedata.combining(char)).split()
    )


@dataclass
class ThesaurusNode:
    name: str
    depth: int
    source: str = ""
    scientific: str = ""
    english: str = ""
    parent: "ThesaurusNode | None" = None
    children: list["ThesaurusNode"] = field(default_factory=list)

    def path(self) -> list["ThesaurusNode"]:
        nodes: list[ThesaurusNode] = []
        current: ThesaurusNode | None = self
        while current is not None:
            nodes.append(current)
            current = current.parent
        nodes.reverse()
        return nodes

    def lineage(self) -> list[str]:
        return [node.name for node in self.path()]


class ThesaurusIndex:
    """Arbres fusionnés de tous les thésaurus d'un dossier."""

    def __init__(self, directory: Path | str):
        self.directory = Path(directory).expanduser()
        self.nodes: list[ThesaurusNode] = []
        self.by_scientific: dict[str, ThesaurusNode] = {}
        self.by_vernacular: dict[str, list[ThesaurusNode]] = {}
        self.by_path: dict[tuple[str, ...], ThesaurusNode] = {}
        if self.directory.is_dir():
            for path in sorted(self.directory.glob("th*saurus-*-utf8.txt")):
                self._parse(path)
        self._build_indexes()

    # ------------------------------------------------------------------ #
    # Analyse
    # ------------------------------------------------------------------ #

    def _parse(self, path: Path) -> None:
        stack: list[ThesaurusNode | None] = []
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            for raw in handle:
                line = raw.rstrip("\r\n")
                if not line.strip():
                    continue
                depth = 0
                while depth < len(line) and line[depth] == "\t":
                    depth += 1
                content = line[depth:].strip()
                if not content:
                    continue

                if content.startswith("{") and content.endswith("}"):
                    value = content[1:-1].strip()
                    target = None
                    # Une accolade appartient au dernier nœud ordinaire de
                    # profondeur inférieure ou égale : le nom scientifique d'un
                    # genre est indenté comme le nom vernaculaire du genre.
                    for level in range(min(depth, len(stack) - 1), -1, -1):
                        if stack[level] is not None:
                            target = stack[level]
                            break
                    if target is None or not value:
                        continue
                    if not target.scientific:
                        target.scientific = value
                    elif not target.english:
                        target.english = value
                    continue

                parent = None
                for level in range(min(depth, len(stack)) - 1, -1, -1):
                    if stack[level] is not None:
                        parent = stack[level]
                        break
                node = ThesaurusNode(
                    name=content, depth=depth, source=path.name, parent=parent
                )
                if parent is not None:
                    parent.children.append(node)
                self.nodes.append(node)
                if len(stack) <= depth:
                    stack.extend([None] * (depth - len(stack) + 1))
                stack[depth] = node
                del stack[depth + 1 :]

    def _build_indexes(self) -> None:
        for node in self.nodes:
            if node.scientific:
                self.by_scientific.setdefault(normalize_name(node.scientific), node)
            self.by_vernacular.setdefault(normalize_search_text(node.name), []).append(node)
            key = tuple(normalize_search_text(item) for item in node.lineage())
            self.by_path.setdefault(key, node)

    # ------------------------------------------------------------------ #
    # Résolution
    # ------------------------------------------------------------------ #

    def node_for_scientific(self, scientific_name: str) -> ThesaurusNode | None:
        return self.by_scientific.get(normalize_name(scientific_name))

    def lineage(self, scientific_name: str) -> tuple[str | None, str | None, list[str]]:
        node = self.node_for_scientific(scientific_name)
        if node is None:
            return None, None, []
        return node.scientific or scientific_name, node.name, node.lineage()

    def resolve_species(self, scientific_name: str) -> dict | None:
        node = self.node_for_scientific(scientific_name)
        if node is None:
            return None
        return {
            "scientificName": node.scientific or scientific_name,
            "vernacularName": node.name,
            "lineage": node.lineage(),
        }

    def resolve_vernacular(self, vernacular_name: str) -> dict | None:
        candidates = self.by_vernacular.get(normalize_search_text(vernacular_name))
        if not candidates:
            return None
        # Seuls les noms de feuilles (espèces) servent de mot-clé taxonomique :
        # un terme générique comme « Oiseaux » ne doit pas être retiré des
        # métadonnées lorsqu'on confirme une espèce.
        leaves = [item for item in candidates if item.scientific and not item.children]
        if not leaves:
            return None
        node = leaves[0]
        return {
            "scientificName": node.scientific,
            "vernacularName": node.name,
            "lineage": node.lineage(),
        }

    def resolve_hierarchical_path(self, path: str) -> dict | None:
        segments = [
            normalize_search_text(segment)
            for segment in re.split(r"[|/>\\]+", path)
            if segment.strip()
        ]
        if len(segments) < 2:
            return None
        exact = self.by_path.get(tuple(segments))
        if exact is not None and exact.scientific:
            return {
                "scientificName": exact.scientific,
                "vernacularName": exact.name,
                "lineage": exact.lineage(),
            }
        for candidate in self.nodes:
            if not candidate.scientific:
                continue
            lineage = tuple(normalize_search_text(item) for item in candidate.lineage())
            if lineage[-len(segments):] == tuple(segments):
                return {
                    "scientificName": candidate.scientific,
                    "vernacularName": candidate.name,
                    "lineage": candidate.lineage(),
                }
        return None

    def search(self, query: str, limit: int = 20) -> list[dict]:
        needle = normalize_search_text(query)
        if len(needle) < 2:
            return []
        results: list[tuple[int, str, dict]] = []
        seen: set[str] = set()
        for node in self.nodes:
            if not node.scientific:
                continue
            terms = (node.scientific, node.name, node.english)
            rank = 3
            for term in terms:
                normalized = normalize_search_text(term)
                if normalized.startswith(needle):
                    rank = min(rank, 0)
                elif needle in normalized:
                    rank = min(rank, 1)
            if rank == 3:
                continue
            key = normalize_name(node.scientific)
            if key in seen:
                continue
            seen.add(key)
            results.append(
                (
                    rank,
                    normalize_search_text(node.name),
                    {
                        "scientificName": node.scientific,
                        "vernacularName": node.name,
                        "lineage": node.lineage(),
                    },
                )
            )
        results.sort(key=lambda item: (item[0], item[1]))
        return [item[2] for item in results[:limit]]
