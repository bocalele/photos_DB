import unittest

from dataBase import annotations, rebuild


class KeywordDecodingTests(unittest.TestCase):
    def test_decodes_truncated_python_list(self):
        items = rebuild._keyword_items("['Bergeronnette des ruisseaux'")

        self.assertEqual(items, [("Bergeronnette des ruisseaux", False)])
        self.assertTrue(
            rebuild.is_malformed_keyword_label("['Bergeronnette des ruisseaux'")
        )

    def test_decodes_complete_list_to_separate_values(self):
        self.assertEqual(
            rebuild._keyword_items("['cote', 'paysage']"),
            [("cote", False), ("paysage", False)],
        )

    def test_decodes_curly_quotes(self):
        self.assertEqual(
            rebuild._keyword_items("[‘Arbre’, ‘Bouleau’]"),
            [("Arbre", False), ("Bouleau", False)],
        )
        self.assertEqual(
            rebuild._keyword_items("[‘Arbre’,'Mousse']"),
            [("Arbre", False), ("Mousse", False)],
        )

    def test_unwraps_single_quoted_value(self):
        self.assertEqual(
            rebuild._keyword_items("'Motacillidés'"),
            [("Motacillidés", False)],
        )
        self.assertTrue(rebuild.is_malformed_keyword_label("'Motacillidés'"))

    def test_marks_truncated_last_item(self):
        items = rebuild._keyword_items(
            "['Bergeronnette grise', 'Motacillidés', 'Oiseaux', 'Passérifor"
        )

        self.assertEqual(items[-1], ("Passérifor", True))
        self.assertEqual(len(items), 4)

    def test_keeps_normal_comma_keyword(self):
        label = "Cheval sauvage, Tarpan"

        self.assertEqual(rebuild._keyword_items(label), [(label, False)])
        self.assertFalse(rebuild.is_malformed_keyword_label(label))

    def test_keeps_ordinary_apostrophe_and_quotes(self):
        for label in ("king's college", 'VACANCES "PICO DA SUMA"'):
            self.assertFalse(rebuild.is_malformed_keyword_label(label))


class KeywordSplittingTests(unittest.TestCase):
    def test_splits_old_space_separated_tags(self):
        self.assertEqual(
            annotations.keyword_values("MINXTATE ESPAGNE MONTAGNES VACANCES USTARROZ"),
            ["MINXTATE", "ESPAGNE", "MONTAGNES", "VACANCES", "USTARROZ"],
        )
        self.assertEqual(
            annotations.keyword_values("ESPAGNE MONTAGNES VACANCES USTARROZ PYRENNEES"),
            ["ESPAGNE", "MONTAGNES", "VACANCES", "USTARROZ", "PYRENNEES"],
        )

    def test_keeps_ordinary_phrases(self):
        for label in ("king's college", "Cheese wrings", "Porto da Cruz"):
            self.assertEqual(annotations.keyword_values(label), [label])

    def test_keeps_quoted_multiword_keywords(self):
        self.assertEqual(
            annotations.keyword_values('VACANCES "ACHADO DA TEXEIRA" "PICO RUIVO"'),
            ["VACANCES", "ACHADO DA TEXEIRA", "PICO RUIVO"],
        )

    def test_classifies_people_and_taxa_out_of_keywords(self):
        class Node:
            name = "Bergeronnette grise"
            scientific = "Motacilla alba"
            children = []

            def lineage(self):
                return ["Oiseaux", self.name]

        class Thesaurus:
            by_scientific = {"motacilla alba": Node()}
            by_vernacular = {"bergeronnette grise": [Node()]}
            by_path = {}
            nodes = [Node()]

        people = {"elise": 4}
        person_ids, taxa, labels = annotations.classify_keywords(
            ["Elise", "Bergeronnette grise", "ESPAGNE"],
            [],
            Thesaurus(),
            people,
        )
        self.assertEqual(person_ids, [4])
        self.assertEqual([taxon["scientificName"] for taxon in taxa], ["Motacilla alba"])
        self.assertEqual(labels, ["ESPAGNE"])


class TaxonomyPriorityTests(unittest.TestCase):
    class Node:
        def __init__(self, name, scientific, lineage):
            self.name = name
            self.scientific = scientific
            self.children = []
            self._lineage = lineage

        def lineage(self):
            return self._lineage

    def thesaurus(self):
        mouflon = self.Node(
            "Mouflon", "Ovis aries",
            ["Mammifères", "Artiodactyles", "Bovidés", "Mouflon"],
        )
        suffolk = self.Node(
            "Mouton Suffolk", None,
            ["Mammifères", "Artiodactyles", "Bovidés", "Mouflon", "Mouton Suffolk"],
        )
        mouflon.children = [suffolk]
        return type("Thesaurus", (), {
            "by_scientific": {"ovis aries": mouflon},
            "by_vernacular": {
                "mouflon": [mouflon],
                "mouton suffolk": [suffolk],
            },
            "by_path": {},
            "nodes": [mouflon, suffolk],
        })()

    def test_taxref_wins_over_same_scientific_name_from_thesaurus(self):
        taxonomy = type("Taxonomy", (), {
            "resolve_taxon": lambda self, text: (
                {
                    "scientificName": "Ovis aries",
                    "vernacularName": "Mouton domestique",
                    "lineage": [
                        "Animaux", "Chordés", "Mammifères", "Cétartiodactyles",
                        "Bisons, Boeufs", "Mouton domestique",
                    ],
                    "authority": "taxref",
                }
                if text == "Mouton domestique" else None
            ),
            "resolve_hierarchy": lambda self, text: None,
        })()

        _, taxa, _ = annotations.classify_keywords(
            ["Mouflon", "Mouton domestique"], [], self.thesaurus(), {},
            taxonomy=taxonomy,
        )

        self.assertEqual(len(taxa), 1)
        self.assertEqual(taxa[0]["vernacularName"], "Mouton domestique")
        self.assertEqual(taxa[0]["authority"], "taxref")

    def test_hierarchy_suppresses_ancestor_flat_keyword(self):
        wild_mouflon = {
            "scientificName": "Ovis gmelinii",
            "vernacularName": "Mouflon d'Arménie, Mouflon",
            "lineage": [
                "Animaux", "Chordés", "Mammifères", "Cétartiodactyles",
                "Bisons, Boeufs", "Mouflon d'Arménie, Mouflon",
            ],
            "authority": "taxref",
        }
        suffolk = {
            "scientificName": None,
            "vernacularName": "Mouton Suffolk",
            "lineage": [
                "Mammifères", "Artiodactyles", "Bovidés", "Mouflon", "Mouton Suffolk",
            ],
            "authority": "thesaurus",
        }
        taxonomy = type("Taxonomy", (), {
            "resolve_taxon": lambda self, text: wild_mouflon if text == "Mouflon" else None,
            "resolve_hierarchy": lambda self, text: suffolk,
        })()

        _, taxa, _ = annotations.classify_keywords(
            ["Mouflon"],
            ["Mammifères|Artiodactyles|Bovidés|Mouflon|Mouton Suffolk"],
            self.thesaurus(), {},
            taxonomy=taxonomy,
        )

        self.assertEqual([taxon["vernacularName"] for taxon in taxa], ["Mouton Suffolk"])


class AuthoritativeKeywordTests(unittest.TestCase):
    def test_ignores_derived_lightroom_keyword_fields(self):
        _, facets = rebuild.parse_record({
            "XMP": {
                "WeightedFlatSubject": ["Chardonneret élégant", "Fringillidés"],
                "LastKeywordXMP": ["Oiseaux"],
            },
        })

        self.assertEqual(facets["keywords"], [])

    def test_reads_iptc_and_dc_subject_only(self):
        _, facets = rebuild.parse_record({
            "IPTC": {"Keywords": ["Carduelis carduelis"]},
            "XMP": {
                "Subject": ["Chardonneret élégant"],
                "WeightedFlatSubject": ["Ancien mot-clé"],
            },
        })

        self.assertEqual(
            facets["keywords"],
            ["Carduelis carduelis", "Chardonneret élégant"],
        )


if __name__ == "__main__":
    unittest.main()
