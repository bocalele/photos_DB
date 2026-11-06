#!/usr/bin/env python3
"""Affiche la classification GBIF d'un taxon, avec les noms français.

Le script accepte indifféremment :

- un nom scientifique : ``Pyrrhocoris apterus``, ``Ovis aries`` ;
- un nom vernaculaire français : ``gendarme``, ``mouton``, ``chêne pédonculé`` ;
- une clé GBIF avec ``--key 4486826``.

Il interroge la GBIF Backbone Taxonomy et récupère, pour chaque niveau de
l'arbre, les noms vernaculaires français disponibles. Les noms français
proviennent des checklists GBIF : ils peuvent être incomplets, régionaux ou
concurrents. Ce script sert à comparer GBIF aux thésaurus locaux, pas à les
remplacer automatiquement.

Exemples :

    python faune/gbif/get_taxonomy.py "Pyrrhocoris apterus"
    python faune/gbif/get_taxonomy.py gendarme
    python faune/gbif/get_taxonomy.py "mouton domestique"
    python faune/gbif/get_taxonomy.py "chêne pédonculé" --json
    python faune/gbif/get_taxonomy.py --key 4486826
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import unicodedata
import warnings
from collections import Counter
from typing import Any

from pygbif import species

# pygbif 0.7.0 avertit que name_usage/name_lookup utilisent encore la
# GBIF Backbone Taxonomy. C'est précisément la source voulue ici.
warnings.filterwarnings(
    "ignore",
    message=".*outdated GBIF Backbone Taxonomy.*",
    category=DeprecationWarning,
)


RANGS_FR = {
    "DOMAIN": "Domaine",
    "REALM": "Règne",
    "SUBREALM": "Sous-règne",
    "KINGDOM": "Règne",
    "SUBKINGDOM": "Sous-règne",
    "INFRAKINGDOM": "Infra-règne",
    "SUPERPHYLUM": "Super-embranchement",
    "PHYLUM": "Embranchement",
    "SUBPHYLUM": "Sous-embranchement",
    "INFRAPHYLUM": "Infra-embranchement",
    "PARVPHYLUM": "Parv-embranchement",
    "SUPERCLASS": "Super-classe",
    "CLASS": "Classe",
    "SUBCLASS": "Sous-classe",
    "INFRACLASS": "Infra-classe",
    "PARVCLASS": "Parv-classe",
    "SUPERORDER": "Super-ordre",
    "ORDER": "Ordre",
    "SUBORDER": "Sous-ordre",
    "INFRAORDER": "Infra-ordre",
    "PARVORDER": "Parv-ordre",
    "SUPERFAMILY": "Super-famille",
    "FAMILY": "Famille",
    "EPIFAMILY": "Épi-famille",
    "SUBFAMILY": "Sous-famille",
    "INFRAFAMILY": "Infra-famille",
    "SUPERTRIBE": "Super-tribu",
    "TRIBE": "Tribu",
    "SUBTRIBE": "Sous-tribu",
    "GENUS": "Genre",
    "SUBGENUS": "Sous-genre",
    "SECTION": "Section",
    "SUBSECTION": "Sous-section",
    "SERIES": "Série",
    "SUBSERIES": "Sous-série",
    "SPECIES": "Espèce",
    "SUBSPECIES": "Sous-espèce",
    "VARIETY": "Variété",
    "SUBVARIETY": "Sous-variété",
    "FORM": "Forme",
    "SUBFORM": "Sous-forme",
    "CULTIVAR": "Cultivar",
    "HYBRID": "Hybride",
    "INFRASPECIFIC_NAME": "Nom infraspécifique",
    "INFRASUBSPECIFIC_NAME": "Nom infra-sous-spécifique",
}

# Noms français usuels pour les niveaux où GBIF n'a pas toujours de nom
# vernaculaire exploitable. Cela évite un arbre à moitié anglais.
NOMS_FR_COURANTS = {
    "Animalia": "Animaux",
    "Plantae": "Plantes",
    "Fungi": "Champignons",
    "Chromista": "Chromistes",
    "Protozoa": "Protozoaires",
    "Bacteria": "Bactéries",
    "Archaea": "Archées",
    "Chordata": "Chordés",
    "Arthropoda": "Arthropodes",
    "Mollusca": "Mollusques",
    "Annelida": "Annélides",
    "Echinodermata": "Échinodermes",
    "Cnidaria": "Cnidaires",
    "Porifera": "Éponges",
    "Platyhelminthes": "Plathelminthes",
    "Nematoda": "Nématodes",
    "Tracheophyta": "Trachéophytes",
    "Magnoliopsida": "Magnoliopsides",
    "Liliopsida": "Liliopsides",
    "Insecta": "Insectes",
    "Arachnida": "Arachnides",
    "Mammalia": "Mammifères",
    "Aves": "Oiseaux",
    "Reptilia": "Reptiles",
    "Amphibia": "Amphibiens",
    "Actinopterygii": "Actinoptérygiens",
    "Hemiptera": "Hémiptères",
    "Coleoptera": "Coléoptères",
    "Hymenoptera": "Hyménoptères",
    "Lepidoptera": "Lépidoptères",
    "Diptera": "Diptères",
    "Artiodactyla": "Artiodactyles",
    "Cetartiodactyla": "Cétartiodactyles",
    "Bovidae": "Bovidés",
    "Fagaceae": "Fagacées",
    "Rosaceae": "Rosacées",
    "Asteraceae": "Astéracées",
    "Pyrrhocoridae": "Pyrrhocoridés",
}

LANGUES_FRANCAISES = {"fra", "fre", "fr"}


def normaliser(texte: str | None) -> str:
    """Clé de comparaison sans accents, sans casse et sans ponctuation lourde."""
    valeur = unicodedata.normalize("NFKD", str(texte or ""))
    valeur = "".join(char for char in valeur if not unicodedata.combining(char))
    valeur = re.sub(r"[\s\-_/]+", " ", valeur.casefold())
    return valeur.strip()


def decouper_noms(valeurs: list[str]) -> list[str]:
    """Éclate les synonymes vernaculaires séparés par des virgules."""
    resultat: list[str] = []
    for valeur in valeurs:
        for morceau in re.split(r"[,;]", str(valeur or "")):
            morceau = morceau.strip()
            if morceau:
                resultat.append(morceau)
    return resultat


def choisir_variante(variantes: Counter) -> str:
    """Choisit l'orthographe la plus lisible parmi des variantes égales."""
    return max(
        variantes,
        key=lambda nom: (
            sum(1 for char in nom if ord(char) > 127),  # accents préférés
            nom[:1].isupper(),                           # majuscule préférée
            -len(nom),                                   # puis le plus court
        ),
    )


def noms_francais_tries(noms: list[str], rang: str | None = None) -> list[str]:
    """Classe les noms français par fréquence, avec une préférence taxinomique."""
    groupes: dict[str, dict[str, Any]] = {}
    for position, nom in enumerate(noms):
        cle = normaliser(nom)
        if not cle:
            continue
        groupe = groupes.setdefault(
            cle,
            {"count": 0, "variantes": Counter(), "premiere": position},
        )
        groupe["count"] += 1
        groupe["variantes"][nom] += 1

    if not groupes:
        return []

    classement = list(groupes.items())
    rang = (rang or "").upper()
    if rang in {
        "FAMILY", "SUBFAMILY", "SUPERFAMILY", "INFRAFAMILY",
        "TRIBE", "SUBTRIBE",
    }:
        # Pour une famille, « Bovidés » est plus utile que « Bisons » ou
        # « Bœufs » quand GBIF renvoie les trois avec la même fréquence.
        suffixes = ("ides", "acees", "ees", "aceae", "idae")
        preferees = [
            (cle, groupe)
            for cle, groupe in classement
            if cle.endswith(suffixes)
        ]
        if preferees:
            classement = preferees

    classement.sort(
        key=lambda item: (-item[1]["count"], item[1]["premiere"])
    )
    return [
        mettre_en_majuscule(choisir_variante(groupe["variantes"])) or ""
        for _, groupe in classement
    ]


def mettre_en_majuscule(nom: str | None) -> str | None:
    if not nom:
        return nom
    return nom[:1].upper() + nom[1:]


def nom_francais_principal(noms: list[str], rang: str | None = None) -> str | None:
    tries = noms_francais_tries(noms, rang)
    return mettre_en_majuscule(tries[0]) if tries else None


class ClientGBIF:
    """Petit cache pour éviter de redemander les mêmes noms vernaculaires."""

    def __init__(self) -> None:
        self._vernaculaires: dict[str, list[str]] = {}

    def backbone_scientifique(self, nom: str) -> dict[str, Any] | None:
        resultat = species.name_backbone(
            scientificName=nom,
            checklistKey=None,
            strict=True,
        )
        usage = resultat.get("usage") or {}
        if not usage.get("key"):
            return None
        return self._accepter_synonyme(resultat)

    def backbone_cle(self, cle: str) -> dict[str, Any] | None:
        resultat = species.name_backbone(
            usageKey=cle,
            checklistKey=None,
        )
        usage = resultat.get("usage") or {}
        if not usage.get("key"):
            return None
        return self._accepter_synonyme(resultat)

    def _accepter_synonyme(self, resultat: dict[str, Any]) -> dict[str, Any]:
        usage = resultat.get("usage") or {}
        accepte = resultat.get("acceptedUsage") or {}
        if usage.get("status") == "SYNONYM" and accepte.get("key"):
            accepte_resultat = species.name_backbone(
                usageKey=accepte["key"],
                checklistKey=None,
            )
            if (accepte_resultat.get("usage") or {}).get("key"):
                accepte_resultat["_synonyme"] = usage
                return accepte_resultat
        return resultat

    def noms_francais(self, cle: str | int | None) -> list[str]:
        if cle is None:
            return []
        cle = str(cle)
        if cle in self._vernaculaires:
            return self._vernaculaires[cle]

        noms: list[str] = []
        offset = 0
        try:
            while True:
                page = species.name_usage(
                    key=cle,
                    data="vernacularNames",
                    limit=1000,
                    offset=offset,
                )
                resultats = page.get("results") or []
                for item in resultats:
                    langue = str(item.get("language") or "").casefold()
                    if langue not in LANGUES_FRANCAISES:
                        continue
                    brut = str(item.get("vernacularName") or "").strip()
                    if brut:
                        noms.extend(decouper_noms([brut]))
                if page.get("endOfRecords") or not resultats:
                    break
                offset += len(resultats)
                if offset >= 5000:
                    break
        except Exception:
            # Un taxon sans noms vernaculaires ne doit pas faire échouer
            # l'affichage du reste de l'arbre.
            noms = []

        self._vernaculaires[cle] = noms
        return noms

    def rechercher_vernaculaire(
        self,
        texte: str,
        max_candidats: int = 1200,
    ) -> list[dict[str, Any]]:
        """Cherche un nom vernaculaire dans la recherche GBIF.

        La recherche GBIF n'a pas de filtre « nom vernaculaire » direct. On
        parcourt donc les résultats tous rangs confondus, on ne garde que ceux
        qui ont un nom français et on les classe par qualité de correspondance.
        """
        requete = normaliser(texte)
        if not requete:
            return []

        candidats: dict[str, dict[str, Any]] = {}
        offset = 0
        while offset < max_candidats:
            page = species.name_lookup(
                q=texte,
                limit=min(300, max_candidats - offset),
                offset=offset,
            )
            resultats = page.get("results") or []
            if not resultats:
                break
            for resultat in resultats:
                cle = resultat.get("nubKey") or resultat.get("key")
                if cle is None:
                    continue
                cle = str(cle)
                noms = [
                    str(item.get("vernacularName") or "").strip()
                    for item in resultat.get("vernacularNames") or []
                    if str(item.get("language") or "").casefold()
                    in LANGUES_FRANCAISES
                ]
                noms = decouper_noms(noms)
                if not noms:
                    continue

                score = _score_vernaculaire(requete, noms)
                if score <= 0:
                    continue

                candidat = candidats.setdefault(
                    cle,
                    {
                        "key": cle,
                        "scientificName": (
                            resultat.get("canonicalName")
                            or resultat.get("scientificName")
                            or cle
                        ),
                        "rank": resultat.get("rank"),
                        "frenchNames": set(),
                        "score": 0,
                        "first": offset,
                    },
                )
                candidat["frenchNames"].update(noms)
                candidat["score"] = max(candidat["score"], score)

            if page.get("endOfRecords"):
                break
            offset += len(resultats)

        resultats = []
        for candidat in candidats.values():
            candidat["frenchNames"] = noms_francais_tries(
                list(candidat["frenchNames"]),
                candidat.get("rank"),
            )
            resultats.append(candidat)
        resultats.sort(key=lambda item: (-item["score"], item["first"]))
        return resultats


def _score_vernaculaire(requete: str, noms: list[str]) -> int:
    meilleur = 0
    for nom in noms:
        cle = normaliser(nom)
        if not cle:
            continue
        if cle == requete:
            score = 100
        elif cle.startswith(requete + " "):
            score = 85
        elif requete in cle.split():
            score = 75
        elif cle.startswith(requete):
            score = 65
        elif requete in cle:
            score = 55
        else:
            score = 0
        meilleur = max(meilleur, score)
    return meilleur


def construire_arbre(
    resultat: dict[str, Any],
    client: ClientGBIF,
) -> list[dict[str, Any]]:
    usage = resultat.get("usage") or {}
    classification = resultat.get("classification") or []

    noeuds: list[dict[str, Any]] = []
    cles_vues: set[str] = set()
    for noeud in classification:
        cle = str(noeud.get("key") or "")
        if not cle or cle in cles_vues:
            continue
        cles_vues.add(cle)
        rang = str(noeud.get("rank") or "").upper()
        scientifique = str(noeud.get("name") or "").strip()
        noms = client.noms_francais(cle)
        francais = nom_francais_principal(noms, rang)
        if not francais:
            francais = NOMS_FR_COURANTS.get(scientifique)
        noeuds.append(
            {
                "key": cle,
                "rank": rang,
                "rankFr": RANGS_FR.get(rang, rang.replace("_", " ").title()),
                "scientificName": scientifique,
                "frenchName": francais,
                "frenchNames": noms_francais_tries(noms, rang),
            }
        )

    # Certains résultats de synonymes n'incluent pas le taxon accepté dans
    # la classification. On l'ajoute pour ne pas perdre la cible.
    cle_usage = str(usage.get("key") or "")
    if cle_usage and cle_usage not in cles_vues:
        rang = str(usage.get("rank") or "").upper()
        scientifique = str(
            usage.get("canonicalName") or usage.get("name") or cle_usage
        ).strip()
        noms = client.noms_francais(cle_usage)
        francais = nom_francais_principal(noms, rang)
        if not francais:
            francais = NOMS_FR_COURANTS.get(scientifique)
        noeuds.append(
            {
                "key": cle_usage,
                "rank": rang,
                "rankFr": RANGS_FR.get(rang, rang.replace("_", " ").title()),
                "scientificName": scientifique,
                "frenchName": francais,
                "frenchNames": noms_francais_tries(noms, rang),
            }
        )

    return noeuds


def afficher_candidats(candidats: list[dict[str, Any]], limite: int) -> None:
    print("Plusieurs taxons correspondent :")
    for index, candidat in enumerate(candidats[:limite], start=1):
        noms = ", ".join(candidat.get("frenchNames") or [])
        print(
            f"  {index:2d}. {candidat['scientificName']}"
            f" — {noms or 'sans nom français'}"
            f" (clé {candidat['key']}, score {candidat['score']})"
        )
    if len(candidats) > limite:
        print(f"  … et {len(candidats) - limite} autre(s).")


def afficher_arbre(
    requete: str,
    resultat: dict[str, Any],
    noeuds: list[dict[str, Any]],
) -> None:
    usage = resultat.get("usage") or {}
    diagnostics = resultat.get("diagnostics") or {}
    synonyme = resultat.get("_synonyme") or {}
    scientifique = (
        usage.get("canonicalName") or usage.get("name") or "?"
    )
    rang = RANGS_FR.get(
        str(usage.get("rank") or "").upper(),
        str(usage.get("rank") or "").title(),
    )

    print(f"Recherche : {requete}")
    if synonyme:
        print(
            "Synonyme accepté : "
            f"{synonyme.get('canonicalName') or synonyme.get('name')}"
        )
    print(
        f"Correspondance GBIF : {scientifique} ({rang}, clé {usage.get('key')})"
        f" — {diagnostics.get('matchType', '?')}"
        f", confiance {diagnostics.get('confidence', '?')}"
    )
    print()

    for noeud in noeuds:
        label = noeud["rankFr"]
        nom_fr = noeud["frenchName"] or "—"
        print(f"{label:<18} : {noeud['scientificName']} — {nom_fr}")

    cible = next(
        (noeud for noeud in noeuds if str(noeud["key"]) == str(usage.get("key"))),
        noeuds[-1] if noeuds else None,
    )
    if cible:
        noms = cible.get("frenchNames") or []
        print()
        if noms:
            print("Noms français disponibles : " + ", ".join(noms[:12]))
        else:
            print("Aucun nom français trouvé dans GBIF pour ce taxon.")

    print()
    print(
        "Source : GBIF Backbone Taxonomy. Les noms français viennent des "
        "checklists GBIF et peuvent être incomplets ou concurrents."
    )


def construire_payload_json(
    requete: str,
    resultat: dict[str, Any],
    noeuds: list[dict[str, Any]],
) -> dict[str, Any]:
    usage = resultat.get("usage") or {}
    diagnostics = resultat.get("diagnostics") or {}
    cible = next(
        (noeud for noeud in noeuds if str(noeud["key"]) == str(usage.get("key"))),
        noeuds[-1] if noeuds else None,
    )
    return {
        "query": requete,
        "source": "GBIF Backbone Taxonomy",
        "match": {
            "key": usage.get("key"),
            "scientificName": usage.get("canonicalName") or usage.get("name"),
            "rank": usage.get("rank"),
            "status": usage.get("status"),
            "matchType": diagnostics.get("matchType"),
            "confidence": diagnostics.get("confidence"),
            "synonym": resultat.get("_synonyme"),
        },
        "lineage": noeuds,
        "frenchNames": (cible or {}).get("frenchNames") or [],
    }


def choisir_candidat(
    candidats: list[dict[str, Any]],
    *,
    choix: int | None = None,
) -> dict[str, Any] | None:
    if not candidats:
        return None
    if choix is not None:
        if 1 <= choix <= len(candidats):
            return candidats[choix - 1]
        raise ValueError(
            f"Choix {choix} invalide : il y a {len(candidats)} candidat(s)."
        )
    if len(candidats) == 1:
        return candidats[0]
    premier = candidats[0]["score"]
    second = candidats[1]["score"] if len(candidats) > 1 else 0
    if premier >= 100 and second < 100:
        return candidats[0]
    return None


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Affiche la classification GBIF et les noms français d'un taxon."
        )
    )
    parser.add_argument(
        "nom",
        nargs="?",
        help="Nom vernaculaire français ou nom scientifique.",
    )
    parser.add_argument(
        "--key",
        help="Clé GBIF Backbone à utiliser directement.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Sortie JSON structurée.",
    )
    parser.add_argument(
        "--choix",
        type=int,
        help="Numéro du candidat à retenir quand un nom vernaculaire est ambigu.",
    )
    parser.add_argument(
        "--limite-candidats",
        type=int,
        default=15,
        help="Nombre de candidats affichés (défaut : 15).",
    )
    parser.add_argument(
        "--max-candidats",
        type=int,
        default=1200,
        help="Nombre maximum de résultats GBIF parcourus (défaut : 1200).",
    )
    parser.add_argument(
        "--sans-interaction",
        action="store_true",
        help="Ne pas demander de choix interactif.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    requete = (args.nom or "").strip()
    if not requete and not args.key:
        try:
            requete = input("Nom vernaculaire ou scientifique : ").strip()
        except EOFError:
            return 2
    if not requete and not args.key:
        print("Aucun nom fourni.", file=sys.stderr)
        return 2

    client = ClientGBIF()
    try:
        if args.key:
            resultat = client.backbone_cle(str(args.key))
            requete = f"clé GBIF {args.key}"
        else:
            resultat = client.backbone_scientifique(requete)
            if resultat is None:
                candidats = client.rechercher_vernaculaire(
                    requete, max_candidats=args.max_candidats
                )
                if not candidats:
                    print(
                        f"Aucun taxon trouvé pour « {requete} ». "
                        "Essayez le nom scientifique ou une autre orthographe.",
                        file=sys.stderr,
                    )
                    return 2
                candidat = choisir_candidat(candidats, choix=args.choix)
                if candidat is None:
                    if args.json:
                        print(
                            json.dumps(
                                {
                                    "query": requete,
                                    "ambiguous": True,
                                    "candidates": candidats,
                                },
                                ensure_ascii=False,
                                indent=2,
                            )
                        )
                        return 2
                    afficher_candidats(candidats, args.limite_candidats)
                    if args.sans_interaction or not sys.stdin.isatty():
                        print(
                            "Précisez le nom scientifique ou utilisez --choix N."
                        )
                        return 2
                    try:
                        reponse = input(
                            "Numéro du taxon (Entrée pour annuler) : "
                        ).strip()
                    except EOFError:
                        return 2
                    if not reponse:
                        return 2
                    if not reponse.isdigit():
                        print("Choix invalide.", file=sys.stderr)
                        return 2
                    candidat = choisir_candidat(
                        candidats, choix=int(reponse)
                    )
                resultat = client.backbone_cle(candidat["key"])
                if resultat is None:
                    print(
                        f"Impossible de charger le taxon {candidat['key']}.",
                        file=sys.stderr,
                    )
                    return 2

        if resultat is None:
            print("Taxon introuvable.", file=sys.stderr)
            return 2

        noeuds = construire_arbre(resultat, client)
        if args.json:
            print(
                json.dumps(
                    construire_payload_json(requete, resultat, noeuds),
                    ensure_ascii=False,
                    indent=2,
                )
            )
        else:
            afficher_arbre(requete, resultat, noeuds)
        return 0
    except ValueError as erreur:
        print(f"Erreur : {erreur}", file=sys.stderr)
        return 2
    except Exception as erreur:
        print(f"Erreur GBIF : {erreur}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
