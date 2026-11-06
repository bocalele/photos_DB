import json
import unittest
from pathlib import Path
import sys
import tempfile
from types import ModuleType, SimpleNamespace
from unittest.mock import patch

from classify_photo import TaxrefIndex, find_taxref_path, prediction_details
from thesaurus import ThesaurusIndex
import server as classifier_server
from tools import preferences


CLASSIFIER_DIR = Path(__file__).resolve().parent


class TaxrefIndexTests(unittest.TestCase):
    def test_finds_downloaded_taxref_version(self):
        path = find_taxref_path(CLASSIFIER_DIR / "TAXREF")
        self.assertEqual(path.name, "TAXREFv18.txt")

    def test_maps_jackdaw_to_french_name_and_bird_lineage(self):
        taxref = TaxrefIndex(CLASSIFIER_DIR / "TAXREF" / "TAXREFv18.txt")
        scientific, vernacular, lineage = taxref.lineage("Corvus monedula")

        self.assertEqual(scientific, "Corvus monedula")
        self.assertEqual(vernacular, "Choucas des tours")
        self.assertIn("Oiseaux", lineage)
        self.assertIn("Passereaux", lineage)
        self.assertIn("Corbeaux, Corneilles, Pies, Geais, Casse-noix", lineage)
        self.assertNotIn("Corvus", lineage)
        self.assertEqual(lineage[-1], "Choucas des tours")

    def test_accepts_taxref_synonym(self):
        taxref = TaxrefIndex(CLASSIFIER_DIR / "TAXREF" / "TAXREFv18.txt")
        scientific, vernacular, _ = taxref.lineage("Coloeus monedula")

        self.assertEqual(scientific, "Corvus monedula")
        self.assertEqual(vernacular, "Choucas des tours")

    def test_searches_species_by_french_name_without_accent(self):
        taxref = TaxrefIndex(CLASSIFIER_DIR / "TAXREF" / "TAXREFv18.txt")
        results = taxref.search_species("choucas des tours")
        self.assertTrue(results)
        self.assertEqual(results[0]["scientificName"], "Corvus monedula")

    def test_resolves_vernacular_keyword_and_taxref_hierarchy(self):
        taxref = TaxrefIndex(CLASSIFIER_DIR / "TAXREF" / "TAXREFv18.txt")
        taxon = taxref.resolve_vernacular("Choucas des tours")
        self.assertEqual(taxon["scientificName"], "Corvus monedula")
        self.assertEqual(
            taxref.resolve_hierarchical_path("|".join(taxon["lineage"])),
            taxon,
        )
        self.assertIsNone(taxref.resolve_hierarchical_path("Animaux|Espèce inconnue"))

    def test_resolve_taxon_prefers_taxref_combined_vernacular(self):
        taxref = TaxrefIndex(CLASSIFIER_DIR / "TAXREF" / "TAXREFv18.txt")
        taxon = taxref.resolve_taxon("Gendarme, Pyrrhocore, Soldat, Suisse")

        self.assertEqual(taxon["scientificName"], "Pyrrhocoris apterus")
        self.assertEqual(taxon["authority"], "taxref")

    def test_resolve_taxon_prefers_mouton_domestique_over_old_thesaurus(self):
        taxref = TaxrefIndex(CLASSIFIER_DIR / "TAXREF" / "TAXREFv18.txt")
        taxon = taxref.resolve_taxon("Mouton domestique")

        self.assertEqual(taxon["scientificName"], "Ovis aries")
        self.assertEqual(taxon["authority"], "taxref")
        self.assertEqual(taxon["lineage"][-1], "Mouton domestique")

    def test_resolve_hierarchy_keeps_taxref_lineage(self):
        taxref = TaxrefIndex(CLASSIFIER_DIR / "TAXREF" / "TAXREFv18.txt")
        taxon = taxref.resolve_taxon("Mouton domestique")
        resolved = taxref.resolve_hierarchy("|".join(taxon["lineage"]))

        self.assertEqual(resolved["scientificName"], "Ovis aries")
        self.assertEqual(resolved["authority"], "taxref")

    def test_resolve_hierarchy_corrects_legacy_mouflon_label(self):
        taxref = TaxrefIndex(CLASSIFIER_DIR / "TAXREF" / "TAXREFv18.txt")
        resolved = taxref.resolve_hierarchy(
            "Mammifères|Artiodactyles|Bovidés|Mouflon"
        )

        self.assertEqual(resolved["scientificName"], "Ovis aries")
        self.assertEqual(resolved["vernacularName"], "Mouton domestique")
        self.assertEqual(resolved["authority"], "taxref")

    def test_species_search_matches_scientific_names(self):
        taxref = TaxrefIndex(CLASSIFIER_DIR / "TAXREF" / "TAXREFv18.txt")
        results = taxref.search_species("Corvus monedula")
        self.assertTrue(results)
        self.assertEqual(results[0]["scientificName"], "Corvus monedula")

    def test_species_search_matches_scientific_name_prefix(self):
        taxref = TaxrefIndex(CLASSIFIER_DIR / "TAXREF" / "TAXREFv18.txt")
        results = taxref.search_species("corvus")
        self.assertTrue(results)
        self.assertIn("Corvus monedula", [item["scientificName"] for item in results])

    def test_species_search_matches_scientific_synonym(self):
        taxref = TaxrefIndex(CLASSIFIER_DIR / "TAXREF" / "TAXREFv18.txt")
        results = taxref.search_species("Corvus caryocatactes")
        self.assertTrue(results)
        self.assertEqual(results[0]["scientificName"], "Nucifraga caryocatactes")

    def test_builds_display_result_with_french_name_and_lineage(self):
        taxref = TaxrefIndex(CLASSIFIER_DIR / "TAXREF" / "TAXREFv18.txt")
        result = prediction_details(
            {"species": "Corvus monedula", "score": 0.73},
            taxref,
        )

        self.assertEqual(result["vernacularName"], "Choucas des tours")
        self.assertEqual(result["scientificName"], "Corvus monedula")
        self.assertAlmostEqual(result["score"], 0.73)
        self.assertIn("Oiseaux", result["lineage"])

    def test_uses_scientific_name_when_french_name_is_missing(self):
        class TaxrefWithoutVernacular:
            def lineage(self, scientific_name):
                return scientific_name, None, []

        result = prediction_details(
            {"species": "Birdus example", "score": 0.5},
            TaxrefWithoutVernacular(),
        )
        self.assertEqual(result["vernacularName"], "Birdus example")

    def test_classification_job_returns_vernacular_for_photo(self):
        class FakeClassifier:
            def __init__(self, device):
                self.device = device

            def predict(self, images, rank, k, batch_size):
                return [{"species": "Corvus monedula", "score": 0.8}]

        fake_module = ModuleType("bioclip.predict")
        fake_module.Rank = SimpleNamespace(SPECIES="species")
        fake_module.TreeOfLifeClassifier = FakeClassifier
        job_id = "unit-test-classification"
        with tempfile.TemporaryDirectory() as directory:
            image_path = Path(directory) / "photo.jpg"
            image_path.write_bytes(b"test image")
            classifier_server.jobs[job_id] = {
                "total": 1,
                "done": 0,
                "complete": False,
                "status": "",
                "results": {},
                "errors": {},
            }
            handler = object.__new__(classifier_server.ClassifierHandler)
            handler.taxref = TaxrefIndex(CLASSIFIER_DIR / "TAXREF" / "TAXREFv18.txt")
            try:
                with (
                    patch.dict(sys.modules, {"bioclip.predict": fake_module}),
                    patch.object(classifier_server, "load_photo", return_value=object()),
                ):
                    handler.run_classification_job(job_id, [image_path])
                job = classifier_server.jobs[job_id]
                self.assertTrue(job["complete"])
                self.assertEqual(job["done"], 1)
                self.assertEqual(
                    job["results"]["photo.jpg"][0]["vernacularName"],
                    "Choucas des tours",
                )
            finally:
                classifier_server.jobs.pop(job_id, None)

    def test_marks_prediction_absent_from_taxref_and_thesaurus_as_custom(self):
        taxref = TaxrefIndex(CLASSIFIER_DIR / "TAXREF" / "TAXREFv18.txt")
        result = prediction_details(
            {"species": "Species inconnue du catalogue", "score": 0.42},
            taxref,
        )

        self.assertTrue(result["custom"])
        self.assertEqual(result["lineage"], [])
        self.assertEqual(result["scientificName"], "Species inconnue du catalogue")

    def test_uses_thesaurus_when_species_is_absent_from_taxref(self):
        taxref = TaxrefIndex(
            CLASSIFIER_DIR / "TAXREF" / "TAXREFv18.txt",
            CLASSIFIER_DIR / "Thesaurus",
        )
        scientific, vernacular, lineage = taxref.lineage("Cupressus abramsiana")

        self.assertEqual(scientific, "Cupressus abramsiana")
        self.assertEqual(vernacular, "Cyprès de Santa Cruz")
        self.assertEqual(lineage[0], "Arbres")
        self.assertEqual(lineage[-1], "Cyprès de Santa Cruz")

        result = prediction_details(
            {"species": "Cupressus abramsiana", "score": 0.81},
            taxref,
        )
        self.assertEqual(result["vernacularName"], "Cyprès de Santa Cruz")
        self.assertEqual(result["lineage"][-1], "Cyprès de Santa Cruz")

    def test_thesaurus_reads_scientific_label_at_same_depth(self):
        with tempfile.TemporaryDirectory() as directory:
            (Path(directory) / "thesaurus-test-utf8.txt").write_text(
                "Arbres\n"
                "\tTaxodiacées\n"
                "\t\tCyprès\n"
                "\t\t{Cupressus}\n"
                "\t\t\tCyprès de Santa Cruz\n"
                "\t\t\t\t{Cupressus abramsiana}\n",
                encoding="utf-8",
            )
            thesaurus = ThesaurusIndex(Path(directory))
            taxon = thesaurus.resolve_species("Cupressus abramsiana")
            self.assertIsNotNone(taxon)
            self.assertEqual(taxon["vernacularName"], "Cyprès de Santa Cruz")
            self.assertEqual(thesaurus.resolve_species("Cupressus")["vernacularName"], "Cyprès")

    def test_loads_initial_directory_from_preferences(self):
        with tempfile.TemporaryDirectory() as directory:
            settings = Path(directory)
            photos = settings / "photos"
            photos.mkdir()
            preferences_file = settings / "preferences.json"
            preferences_file.write_text(
                json.dumps({"initial_directory": str(photos)}),
                encoding="utf-8",
            )
            with patch.object(preferences, "PREFERENCES_PATH", preferences_file):
                self.assertEqual(
                    classifier_server.initial_directory_from_preferences(),
                    photos.resolve(),
                )

    def test_falls_back_when_preferred_directory_is_unavailable(self):
        with tempfile.TemporaryDirectory() as directory:
            settings = Path(directory)
            preferences_file = settings / "preferences.json"
            preferences_file.write_text(
                json.dumps({"initial_directory": str(settings / "missing")}),
                encoding="utf-8",
            )
            with (
                patch.object(preferences, "PREFERENCES_PATH", preferences_file),
                patch.object(Path, "home", return_value=settings),
            ):
                self.assertEqual(
                    classifier_server.initial_directory_from_preferences(),
                    settings,
                )

    def test_photo_taxonomy_reads_keyword_and_hierarchical_metadata(self):
        taxref = TaxrefIndex(CLASSIFIER_DIR / "TAXREF" / "TAXREFv18.txt")
        taxon = taxref.resolve_vernacular("Choucas des tours")
        result = classifier_server.photo_taxonomy(
            {
                "IPTC": {"Keywords": ["Choucas des tours", "Oiseaux"]},
                "XMP": {
                    "dc.subject": ["Choucas des tours"],
                    "lr.hierarchicalSubject": ["|".join(taxon["lineage"])],
                },
            },
            taxref,
        )
        self.assertEqual(result["keywordTaxon"]["scientificName"], "Corvus monedula")
        self.assertEqual(result["hierarchyTaxon"]["scientificName"], "Corvus monedula")
        self.assertTrue(result["keywordPresent"])
        self.assertTrue(result["hierarchyPresent"])
        self.assertEqual(result["keywords"], ["Choucas des tours", "Oiseaux"])

    def test_class_options_use_broad_thesaurus_classes(self):
        taxref = TaxrefIndex(CLASSIFIER_DIR / "TAXREF" / "TAXREFv18.txt")
        options = taxref.class_options()
        labels = {item["label"] for item in options}

        self.assertTrue(options)
        self.assertIn("Oiseaux", labels)
        self.assertIn("Plantes terrestres", labels)
        self.assertIn("Insectes s.l.", labels)
        self.assertLess(len(options), 50)
        self.assertTrue(taxref.is_class_name("Oiseaux"))
        self.assertTrue(taxref.is_class_name("Plantes terrestres"))
        # Les anciens taxons manuels peuvent encore porter une classe TAXREF.
        self.assertTrue(taxref.is_class_name("Aves"))
        self.assertFalse(taxref.is_class_name("Classe inventée"))

    def test_photo_taxonomy_recognizes_manual_taxon(self):
        taxref = TaxrefIndex(CLASSIFIER_DIR / "TAXREF" / "TAXREFv18.txt")
        result = classifier_server.photo_taxonomy(
            {
                "IPTC": {"Keywords": ["Oiseaux", "Petit oiseau inconnu"]},
                "XMP": {
                    "dc.subject": ["Oiseaux", "Petit oiseau inconnu"],
                    "lr.hierarchicalSubject": ["Oiseaux|Petit oiseau inconnu"],
                },
            },
            taxref,
        )

        self.assertTrue(result["customTaxon"])
        self.assertEqual(result["keywordTaxon"]["scientificName"], "Petit oiseau inconnu")
        self.assertEqual(result["keywordTaxon"]["taxonClass"], "Oiseaux")
        self.assertFalse(result["keywordPresent"])
        self.assertFalse(result["hierarchyPresent"])

    def test_custom_taxon_requires_a_known_class(self):
        taxref = TaxrefIndex(CLASSIFIER_DIR / "TAXREF" / "TAXREFv18.txt")
        handler = classifier_server.ClassifierHandler.__new__(
            classifier_server.ClassifierHandler
        )
        handler.taxref = taxref

        taxon = handler.custom_taxon({
            "scientificName": "Petit oiseau inconnu",
            "vernacularName": "Petit oiseau inconnu",
            "taxonClass": "Oiseaux",
        })
        self.assertEqual(taxon["lineage"], ["Oiseaux", "Petit oiseau inconnu"])
        self.assertTrue(taxon["custom"])

        plant = handler.custom_taxon({
            "scientificName": "Plante inconnue",
            "vernacularName": "Plante inconnue",
            "taxonClass": "Plantes terrestres",
        })
        self.assertEqual(
            plant["lineage"], ["Plantes terrestres", "Plante inconnue"]
        )

        with self.assertRaises(ValueError):
            handler.custom_taxon({
                "scientificName": "Petit oiseau inconnu",
                "taxonClass": "Classe inventée",
            })

    def test_manual_taxon_is_removed_when_a_taxref_name_is_validated(self):
        with tempfile.TemporaryDirectory() as directory:
            photo = Path(directory) / "bird.psd"
            photo.touch()
            taxref = TaxrefIndex(CLASSIFIER_DIR / "TAXREF" / "TAXREFv18.txt")
            new_taxon = taxref.resolve_species("Corvus corone")
            existing = {
                "IPTC": {"Keywords": ["Oiseaux", "Petit oiseau inconnu"]},
                "XMP": {
                    "dc.subject": ["Oiseaux", "Petit oiseau inconnu"],
                    "lr.hierarchicalSubject": ["Oiseaux|Petit oiseau inconnu"],
                },
            }
            old_taxon = classifier_server.photo_taxonomy(existing, taxref)["keywordTaxon"]
            with (
                patch("server.get_metadata_many", side_effect=[
                    {str(photo): existing},
                    {str(photo): {
                        "IPTC": {"Keywords": ["Oiseaux", "Corvus corone", "Corneille noire"]},
                        "XMP": {
                            "dc.subject": ["Oiseaux", "Corvus corone", "Corneille noire"],
                            "lr.hierarchicalSubject": ["|".join(new_taxon["lineage"])],
                        },
                    }},
                ]),
                patch("server.set_metadata") as write_metadata,
            ):
                classifier_server.write_photo_taxon(
                    photo, new_taxon, True, taxref, previous_taxon=old_taxon
                )

            write_metadata.assert_called_once_with(
                photo,
                {
                    "IPTC": {"Keywords": ["Oiseaux", "Corvus corone", "Corneille noire"]},
                    "XMP": {
                        "Subject": ["Oiseaux", "Corvus corone", "Corneille noire"],
                        "HierarchicalSubject": ["|".join(new_taxon["lineage"])],
                    },
                },
            )

    def test_clear_photo_fauna_removes_taxon_and_keeps_other_keywords(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            photo = root / "bird.psd"
            photo.touch()
            taxref = TaxrefIndex(CLASSIFIER_DIR / "TAXREF" / "TAXREFv18.txt")
            taxon = taxref.resolve_species("Corvus monedula")
            existing = {
                "IPTC": {
                    "Keywords": [
                        "Choucas des tours", "Corvus monedula", "Oiseaux",
                        "Juvénile", "Jean Dupont",
                    ],
                },
                "XMP": {
                    "dc.subject": [
                        "Choucas des tours", "Corvus monedula", "Oiseaux",
                        "Juvénile", "Jean Dupont",
                    ],
                    "lr.hierarchicalSubject": [
                        "|".join(taxon["lineage"]),
                        "Personnes|Jean Dupont",
                    ],
                },
            }
            updated = {
                "IPTC": {"Keywords": ["Juvénile", "Jean Dupont"]},
                "XMP": {
                    "dc.subject": ["Juvénile", "Jean Dupont"],
                    "lr.hierarchicalSubject": ["Personnes|Jean Dupont"],
                },
            }
            with (
                patch("server.get_metadata_many", side_effect=[
                    {str(photo): existing},
                    {str(photo): updated},
                ]),
                patch("server.set_metadata") as write_metadata,
                patch.object(
                    classifier_server.photos_db,
                    "taxa_for_path",
                    return_value=[{
                        "name": "Choucas des tours",
                        "scientific_name": "Corvus monedula",
                        "path": "/".join(taxon["lineage"]),
                    }],
                ),
                patch.object(
                    classifier_server.photos_db, "record_taxon_for_path"
                ) as clear_taxa,
                patch.object(
                    classifier_server.photos_db,
                    "record_keywords_rating_author_for_path",
                ) as record_keywords,
            ):
                result = classifier_server.clear_photo_fauna(photo, taxref, root)

            write_metadata.assert_called_once_with(
                photo,
                {
                    "IPTC": {"Keywords": ["Juvénile", "Jean Dupont"]},
                    "XMP": {
                        "Subject": ["Juvénile", "Jean Dupont"],
                        "HierarchicalSubject": ["Personnes|Jean Dupont"],
                        "WeightedFlatSubject": ["Juvénile", "Jean Dupont"],
                    },
                },
            )
            clear_taxa.assert_not_called()
            record_keywords.assert_not_called()
            self.assertIsNone(result["keywordTaxon"])
            self.assertIsNone(result["hierarchyTaxon"])
            self.assertEqual(result["keywords"], ["Juvénile", "Jean Dupont"])

    def test_photo_taxonomy_resolves_scientific_keyword_without_vernacular_flag(self):
        taxref = TaxrefIndex(CLASSIFIER_DIR / "TAXREF" / "TAXREFv18.txt")
        result = classifier_server.photo_taxonomy(
            {"IPTC": {"Keywords": ["Corvus monedula"]}},
            taxref,
        )
        self.assertEqual(result["keywordTaxon"]["scientificName"], "Corvus monedula")
        self.assertFalse(result["keywordPresent"])

    def test_confirmed_taxon_replaces_species_and_preserves_other_keywords(self):
        with tempfile.TemporaryDirectory() as directory:
            photo = Path(directory) / "bird.psd"
            photo.touch()
            taxref = TaxrefIndex(CLASSIFIER_DIR / "TAXREF" / "TAXREFv18.txt")
            old_taxon = taxref.resolve_species("Corvus monedula")
            taxon = taxref.resolve_species("Corvus corone")
            existing = {
                "IPTC": {"Keywords": ["Oiseaux", "Choucas des tours", "Corvus monedula"]},
                "XMP": {
                    "dc.subject": ["Oiseaux", "Choucas des tours", "Corvus monedula"],
                    "lr.hierarchicalSubject": ["|".join(old_taxon["lineage"])],
                },
            }
            with (
                patch("server.get_metadata_many", side_effect=[
                    {str(photo): existing},
                    {str(photo): {
                        "IPTC": {"Keywords": ["Oiseaux", "Corvus corone", "Corneille noire"]},
                        "XMP": {
                            "dc.subject": ["Oiseaux", "Corvus corone", "Corneille noire"],
                            "lr.hierarchicalSubject": ["|".join(taxon["lineage"])],
                        },
                    }},
                ]),
                patch("server.set_metadata") as write_metadata,
            ):
                updated = classifier_server.write_photo_taxon(photo, taxon, True, taxref)

            self.assertTrue(updated["keywordPresent"])
            self.assertTrue(updated["hierarchyPresent"])
            write_metadata.assert_called_once_with(
                photo,
                {
                    "IPTC": {"Keywords": ["Oiseaux", "Corvus corone", "Corneille noire"]},
                    "XMP": {
                        "Subject": ["Oiseaux", "Corvus corone", "Corneille noire"],
                        "HierarchicalSubject": ["|".join(taxon["lineage"])],
                    },
                },
            )


if __name__ == "__main__":
    unittest.main()
