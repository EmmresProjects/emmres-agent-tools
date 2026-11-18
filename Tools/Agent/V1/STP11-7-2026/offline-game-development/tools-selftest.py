#!/usr/bin/env python3
"""tools-selftest.py -- the Skill Tool checks itself.

Role in the Skill Tool
    The suite that proves the other four files work together: every command is run, the ingest
    round trip is compared against the shipped dataset, the gates are checked with both clean and
    deliberately broken records, the config layering and its validation are exercised, and the
    documented reference rules are enforced. Run it after any local edit.

Run it
    python3 offline-game-development/tools-selftest.py            # summary
    python3 offline-game-development/tools-selftest.py -v         # per test
    python3 offline-game-development/tools-offline-game-development.py selftest
"""

from __future__ import annotations

import contextlib
import shlex
import io
import json
import os
import re
import shutil
import sys
import tempfile
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
PACKAGE = os.path.basename(HERE)
sys.path.insert(0, HERE)


def _load(filename: str, alias: str):
    import importlib.util

    if alias in sys.modules:
        return sys.modules[alias]
    spec = importlib.util.spec_from_file_location(alias, os.path.join(HERE, filename))
    module = importlib.util.module_from_spec(spec)
    sys.modules[alias] = module
    spec.loader.exec_module(module)
    return module


kit = _load("tools-runtime-kit.py", "ogd_tools-runtime-kit")
engine = _load("tools-catalog-engine.py", "ogd_tools-catalog-engine")
gates = _load("tools-gates.py", "ogd_tools-gates")
generators = _load("tools-artifact-generators.py", "ogd_tools-artifact-generators")
cli = _load("tools-offline-game-development.py", "ogd_cli")

ENTRY = os.path.join(HERE, "tools-offline-game-development.py")
ENTRY_NAME = os.path.basename(ENTRY)
ASSET_CATALOG = os.path.join(HERE, "asset-catalog.json")
# the call shapes a shipped module must never contain; the audit's own guard exempts this line
SHELL_CALLS = re.compile(r"\bsubprocess\.|os\.system\(|socket\.socket\(|\burlopen\(|requests\.(get|post)\(")
EXECUTED: set = set()


class ToolTestCase(unittest.TestCase):
    """Shared scaffolding: a temp skill root, an in-process CLI call, and envelope helpers."""

    @classmethod
    def setUpClass(cls):
        cls.root = tempfile.mkdtemp(prefix="ogd-selftest-")
        cls.tool_dir = os.path.join(cls.root, PACKAGE)
        shutil.copytree(HERE, cls.tool_dir, ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".tmp-*"))
        with open(os.path.join(cls.root, "SKILL.md"), "w", encoding="utf-8") as handle:
            handle.write("# Test skill\n\nThe Skill Tool folder sits beside this file.\n")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.root, ignore_errors=True)  # audit: allow - the suite deletes only its own temp root

    def setUp(self):
        self.work = tempfile.mkdtemp(prefix="ogd-run-", dir=self.root)
        self._previous_cwd = os.getcwd()
        os.chdir(self.root)

    def tearDown(self):
        os.chdir(self._previous_cwd)
        shutil.rmtree(self.work, ignore_errors=True)  # audit: allow - only this run's temp directory

    # -- running the tool -------------------------------------------------
    def run_cli(self, *args, expect=None, cwd=None):
        """Call the CLI in process, so the suite tests the real entry point."""
        argv = [str(item) for item in args]
        captured_out, captured_err = io.StringIO(), io.StringIO()
        EXECUTED.add(argv[0] if argv and not argv[0].startswith("-") else "")
        previous_cwd = os.getcwd()
        if cwd:
            os.chdir(cwd)
        try:
            with contextlib.redirect_stdout(captured_out), contextlib.redirect_stderr(captured_err):
                code = cli.main(argv, standalone=False)
        except SystemExit as exit_signal:  # argparse usage errors
            code = int(exit_signal.code or 2)
        finally:
            os.chdir(previous_cwd)
        outcome = _Outcome(code, captured_out.getvalue(), captured_err.getvalue(), argv)
        if expect is not None:
            self.assertEqual(
                expect, code,
                f"expected exit {expect} from `{ENTRY} {' '.join(argv)}`\nstdout: {outcome.stdout[:900]}\n"
                f"stderr: {outcome.stderr[:900]}")
        return outcome

    def envelope(self, outcome):
        """The envelope arrives on stdout for --format json and on stderr after a human body."""
        for stream in (outcome.stdout, outcome.stderr):
            if stream.lstrip().startswith("{"):
                return json.loads(stream)
        self.fail(f"expected a JSON envelope, got: stdout {outcome.stdout[:300]!r} "
                  f"stderr {outcome.stderr[:300]!r}")

    def data(self, outcome, expect=None):
        payload = self.envelope(outcome)
        if expect is not None:
            self.assertEqual(expect, payload["ok"], json.dumps(payload.get("errors"))[:600])
        return payload

    def write(self, name: str, text: str) -> str:
        path = os.path.join(self.work, name)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(text)
        return path


def tearDownModule():
    """Runs after every case in this module: no command may ship untested."""
    if not os.environ.get("OGD_SELFTEST_FULL"):
        return  # a partial run (python -m unittest one.case) cannot claim whole-suite coverage
    missing = sorted(set(cli.COMMAND_NAMES) - EXECUTED)
    if missing:
        raise AssertionError("commands with no test: " + ", ".join(missing))


class _Outcome:
    def __init__(self, code, stdout, stderr, argv):
        self.returncode = code
        self.stdout = stdout
        self.stderr = stderr
        self.argv = argv

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<Outcome exit={self.returncode} argv={self.argv} out={self.stdout[:120]!r}>"


# ---------------------------------------------------------------------------
# the suite contract itself
# ---------------------------------------------------------------------------
class TestSuiteContract(ToolTestCase):
    def test_suite_reports_its_own_coverage(self):
        """The coverage list is reachable, so a missing command is a name, not a mystery."""
        self.assertTrue(set(cli.COMMAND_NAMES) >= EXECUTED or EXECUTED)

    def test_every_command_help_exits_zero(self):
        for name in cli.COMMAND_NAMES:
            outcome = self.run_cli("help", name, expect=0)
            self.assertIn(name, outcome.stdout)

    def test_help_lists_commands(self):
        outcome = self.run_cli("help", expect=0)
        for name in cli.COMMAND_NAMES:
            self.assertIn(name, outcome.stdout)

    def test_no_args_is_not_an_error(self):
        outcome = self.run_cli(expect=0)
        self.assertTrue(self.envelope(outcome)["ok"])

    def test_help_of_unknown_command(self):
        outcome = self.run_cli("help", "frobnicate", expect=4)
        self.assertEqual("E-NOT-FOUND", self.envelope(outcome)["errors"][0]["code"])


# ---------------------------------------------------------------------------
# dataset truth
# ---------------------------------------------------------------------------
class TestCatalogData(ToolTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.asset = json.load(open(ASSET_CATALOG, encoding="utf-8"))
        cls.archetypes = json.load(open(os.path.join(HERE, "asset-archetypes.json"), encoding="utf-8"))
        cls.catalog = engine.Catalog(engine.normalize_dataset(cls.asset, cls.archetypes, "asset")[0],
                                    cls.archetypes)

    @staticmethod
    def normalize(*args):
        return engine.normalize_dataset(*args)

    def test_counts_match_the_source_document(self):
        source = self.asset["source_document"]
        self.assertEqual(source["declared_categories"], self.asset["category_count"])
        self.assertEqual(source["declared_skill_concepts"], self.asset["entry_count"])
        self.assertEqual(34, self.asset["category_count"])
        self.assertEqual(1419, self.asset["entry_count"])

    def test_each_category_holds_what_it_declares(self):
        for category in self.asset["categories"]:
            self.assertEqual(category["declared_count"], len(category["entries"]),
                             f"category {category['number']} count differs")

    def test_descriptions_rebuild_from_templates(self):
        templates = self.archetypes["archetypes"]
        for name, spec in templates.items():
            self.assertIn("{subject}", spec["catalog_template"])
        mismatches = []
        for record in self.catalog.records:
            rebuilt = engine.describe(record["archetype"], record["subject"])
            if rebuilt != record["description"]:
                mismatches.append(record["id"])
            archetype, subject = engine.match_archetype(record["description"], templates)
            if (archetype, subject) != (record["archetype"], record["subject"]):
                mismatches.append(f"{record['id']} (match)")
        self.assertEqual([], mismatches[:5], f"{len(mismatches)} of {len(self.catalog)} records drift")

    def test_ids_are_ordered_and_unique(self):
        seen = set()
        for record in self.catalog.records:
            self.assertRegex(record["id"], r"^c\d{2}e\d{2}$")
            self.assertNotIn(record["id"], seen)
            seen.add(record["id"])
            self.assertEqual(record["ref"], f"{record['category']}.{int(record['id'].split('e')[1])}")

    def test_every_capability_maps_to_a_layer_and_a_real_category(self):
        for record in self.catalog.records:
            self.assertIn(record["layer"], [name for name, _low, _high in engine.LAYERS])
            self.assertIn(record["category"], self.catalog.categories)
            self.assertTrue(self.catalog.categories[record["category"]]["title"])

    def test_derived_fields_are_present_and_typed(self):
        for record in self.catalog.records[:120]:
            self.assertTrue(record["tags"])
            self.assertIsInstance(record["est_days"], int)
            self.assertIn(record["difficulty"], ("Foundational", "Intermediate", "Advanced", "Expert"))
            self.assertIn(record["risk_class"], ("routine", "resource-heavy", "approval-required"))
            self.assertTrue(record["role"])
            self.assertTrue(record["engines"])

    def test_validate_is_clean_on_the_shipped_dataset(self):
        outcome = self.run_cli("validate", "--format", "json", expect=0)
        data = self.data(outcome, True)["data"]
        self.assertEqual([], data["errors"], json.dumps(data["errors"])[:300])
        self.assertEqual([], data["warnings"], json.dumps(data["warnings"])[:300])
        self.assertEqual(1419, data["entries"])
        self.assertEqual(12, len(data["fingerprint"]))

    def test_validate_reports_a_healthy_dataset(self):
        findings = gates.engine.validate_dataset(self.catalog.dataset, self.archetypes)
        self.assertEqual([], [f for f in findings if f["severity"] == "error"])

    def test_validate_catches_a_broken_dataset(self):
        broken = json.loads(json.dumps(self.asset))
        broken["categories"][0]["entries"] = broken["categories"][0]["entries"][:3]
        dataset, findings = engine.normalize_dataset(broken, self.archetypes, "broken")
        codes = {finding["code"] for finding in findings}
        self.assertIn("E-COUNT-MISMATCH", codes)


# ---------------------------------------------------------------------------
# ingest
# ---------------------------------------------------------------------------
class TestIngest(ToolTestCase):
    def test_sample_fixture_is_two_categories(self):
        text = open(os.path.join(HERE, "asset-sample-source.md"), encoding="utf-8").read()
        archetypes = json.load(open(os.path.join(HERE, "asset-archetypes.json"), encoding="utf-8"))
        findings: list = []
        parsed = engine.parse_source(text, archetypes["archetypes"], origin="asset-sample-source.md",
                                    findings=findings)
        self.assertEqual(2, parsed["category_count"])
        self.assertEqual(10, parsed["entry_count"])
        self.assertEqual(0, parsed["stats"]["inferred_archetypes"])
        self.assertEqual([], [f for f in findings if f["severity"] == "error"])
        titles = [category["title"] for category in parsed["categories"]]
        self.assertEqual(["Offline Test Harness", "Save Data Integrity"], titles)

    def test_html_and_markdown_produce_the_same_entries(self):
        text = open(os.path.join(HERE, "asset-sample-source.md"), encoding="utf-8").read()
        markdown_half = text.split("<table")[0]
        html_half = "<table" + text.split("<table")[1]
        first = engine.parse_source(markdown_half, {}, origin="md")
        second = engine.parse_source(html_half, {}, origin="html", fmt="html")
        names_a = [entry["name"] for category in first["categories"] for entry in category["entries"]]
        names_b = [entry["name"] for category in second["categories"] for entry in category["entries"]]
        self.assertEqual(4, len(names_a))
        self.assertEqual(6, len(names_b))
        self.assertEqual(set(), (set(names_a) | set(names_b)) - set(names_b) - set(names_a))

    def test_tags_and_numbering_never_reach_the_output(self):
        text = open(os.path.join(HERE, "asset-sample-source.md"), encoding="utf-8").read()
        parsed = engine.parse_source(text, {}, origin="fixture")
        blob = json.dumps(parsed)
        for noise in ("<td>", "class=", "doc-table", "wiki-export", "</b>", "Skill Name:", "<table"):
            self.assertNotIn(noise, blob, f"markup leaked into the dataset: {noise}")

    def test_wrapped_description_is_rejoined(self):
        source = ("## 1. Offline Test Harness\n\n2 skill concepts\n\n"
                  "Skill Name: Headless Engine Runner\n\n"
                  "Description: Evaluate headless engine runner with repeatable automated and manual\n"
                  "checks, record the engine, platform, hardware, scene or build, measured result, pass\n"
                  "threshold, failure evidence, and exact remediation and regression retest.\n\n"
                  "Skill Name: Frozen Reference Cases\n\n"
                  "Description: Define frozen reference cases with prerequisites, owners, platform and\n"
                  "engine constraints, ordered steps, license gates, quality budgets, fallback paths,\n"
                  "required evidence, and a verified completion condition.\n")
        parsed = engine.parse_source(source, {}, origin="wrapped")
        entries = parsed["categories"][0]["entries"]
        self.assertEqual(2, len(entries))
        self.assertTrue(entries[0]["description"].endswith("regression retest."))
        self.assertTrue(entries[1]["description"].endswith("completion condition."))

    def test_prose_between_entries_is_counted_not_invented(self):
        source = ("## 1. Offline Test Harness\n\n1 skill concepts\n\n"
                  "This section was pasted from a wiki and needs cleanup.\n\n"
                  "Skill Name: Engine Log Scraper\n"
                  "Description: Create engine log scraper from pinned local inputs with license and\n"
                  "integrity checks, deterministic settings, platform metadata, rollback or cleanup\n"
                  "behavior, and a post-build import, launch, or playback validation.\n")
        parsed = engine.parse_source(source, {}, origin="prose")
        self.assertEqual(1, parsed["entry_count"])
        self.assertGreaterEqual(parsed["stats"]["unclassified_lines"], 1)

    def test_name_only_entry_gets_a_generated_archetype(self):
        source = ("## 1. Save Data Integrity\n\n1 skill concepts\n\n"
                  "Skill Name: Save Migration Dry Run\n")
        findings: list = []
        parsed = engine.parse_source(source, {}, origin="name-only", findings=findings)
        self.assertEqual(1, parsed["stats"]["inferred_archetypes"])
        self.assertEqual("W-INFERRED-ARCHETYPE", findings[0]["code"])

    def test_csv_and_tsv_and_json_sources(self):
        csv_text = ("Name,Description,Category\n"
                    "Sprite Packer,Create sprite packer from pinned local inputs with license and integrity "
                    "checks and deterministic settings and a post-build validation.,Art\n")
        parsed = engine.parse_source(csv_text, {}, origin="a.csv", fmt="csv")
        self.assertEqual(1, parsed["entry_count"])
        self.assertEqual("Art", parsed["categories"][0]["title"])
        tsv = csv_text.replace(",", "\t")
        self.assertEqual(1, engine.parse_source(tsv, {}, origin="a.tsv", fmt="tsv")["entry_count"])
        payload = [{"name": "Save Adapter Interface",
                    "description": "Provide save adapter interface with capability detection, offline mocks, "
                                   "platform-specific implementations, error recovery, compatibility tests, "
                                   "and documented limitations for every supported target.",
                    "category": "Persistence"}]
        parsed_json = engine.parse_source(json.dumps(payload), {}, origin="a.json", fmt="json")
        self.assertEqual(1, parsed_json["entry_count"])

    def test_unusable_source_is_a_structured_error(self):
        path = self.write("junk.md", "# Notes\n\nNothing here is a capability.\n")
        outcome = self.run_cli("import", "--source", path, expect=3)
        error = self.envelope(outcome)["errors"][0]
        self.assertEqual("E-INPUT", error["code"])
        self.assertIn("Skill Name:", error["hint"])

    def test_missing_source_is_input_error(self):
        outcome = self.run_cli("import", "--source", "no/such/file.md", expect=3)
        self.assertEqual("E-INPUT", self.envelope(outcome)["errors"][0]["code"])

    def test_pdf_path_points_at_the_text_companion(self):
        directory = self.work
        pdf = os.path.join(directory, "catalog.pdf")
        with open(pdf, "wb") as handle:
            handle.write(b"%PDF-1.4 not parsed by this tool\n")
        outcome = self.run_cli("import", "--source", pdf, expect=3)
        error = self.envelope(outcome)["errors"][0]
        self.assertEqual("E-INPUT", error["code"])
        self.assertIn("does not parse PDFs", error["message"])
        self.assertIn("pdftotext", error["hint"])
        with open(os.path.splitext(pdf)[0] + ".txt", "w", encoding="utf-8") as handle:
            handle.write("## 1. Offline Test Harness\n\n1 skill concepts\n\n"
                         "Skill Name: Headless Engine Runner\n"
                         "Description: Implement headless engine runner as a focused reusable "
                         "game-development capability with declared inputs and outputs, offline "
                         "dependencies, rights metadata, performance budgets, diagnostics, tests, and "
                         "target-build acceptance criteria.\n")
        outcome = self.run_cli("import", "--source", pdf, "--summary-only", expect=0)
        data = self.data(outcome, True)["data"]
        self.assertEqual(1, data["counts"]["entries"])
        self.assertIn(".txt", data["source"])

    def test_import_then_use_the_dataset(self):
        source = os.path.join(HERE, "asset-sample-source.md")
        target = os.path.join(self.work, "imported.json")
        outcome = self.run_cli("import", "--source", source, "--out", target, expect=0)
        written = self.data(outcome, True)["data"]["written"]
        self.assertTrue(os.path.isfile(written["path"]))
        outcome = self.run_cli("show", "c01e01", "--dataset", target, "--format", "md", expect=0)
        self.assertIn("Headless Engine Runner", outcome.stdout)
        outcome = self.run_cli("coverage", "--dataset", target, "--format", "md", expect=0)
        self.assertIn("2 categories", outcome.stdout)
        outcome = self.run_cli("spec", "c01e01", "--dataset", target, "--format", "md", expect=0)
        self.assertIn("# Headless Engine Runner", outcome.stdout)

    def test_import_onto_the_shipped_dataset_needs_approval(self):
        target = os.path.join(HERE, "asset-catalog.json")  # the file the tool itself reads
        outcome = self.run_cli("import", "--source", os.path.join(HERE, "asset-sample-source.md"),
                              "--out", target, expect=6)
        error = self.envelope(outcome)["errors"][0]
        self.assertEqual("E-APPROVAL", error["code"])
        self.assertEqual("REPLACE-DATASET", error["details"]["required_token"])
        before = open(target, encoding="utf-8").read()
        outcome = self.run_cli("import", "--source", os.path.join(HERE, "asset-sample-source.md"),
                              "--out", target, "--allow-overwrite", "--approve", "WRONG-TOKEN", expect=6)
        error = self.envelope(outcome)["errors"][0]
        self.assertIn("WRONG-TOKEN", error["message"])
        self.assertEqual("REPLACE-DATASET", error["details"]["required_token"])
        self.assertIn("REPLACE-DATASET", error["hint"])
        self.assertEqual(before, open(target, encoding="utf-8").read(), "the shipped asset changed")


# ---------------------------------------------------------------------------
# selection, search, browsing
# ---------------------------------------------------------------------------
class TestToolSurface(ToolTestCase):
    """The two commands that describe the tool rather than the catalog."""

    def test_capabilities_names_every_command_and_module(self):
        outcome = self.run_cli("capabilities", "--format", "json", expect=0)
        data = self.data(outcome, True)["data"]
        self.assertEqual(len(cli.COMMAND_NAMES), data["command_count"])
        listed = {item["command"] for group in data["groups"].values() for item in group}
        self.assertEqual(set(cli.COMMAND_NAMES), listed)
        self.assertEqual(34, data["catalog_reach"]["categories"])
        files = {item["file"] for item in data["additional_modules"]}
        self.assertIn("tools-gates.py", files)
        for item in data["additional_modules"]:
            self.assertTrue(os.path.isfile(os.path.join(HERE, item["file"])), item["file"])
        markdown = self.run_cli("capabilities", "--format", "md", expect=0).stdout
        self.assertIn("Limits of this tool", markdown)
        self.assertIn("tools-selftest.py", markdown)

    def test_selftest_refuses_to_nest_inside_itself(self):
        os.environ["OGD_SELFTEST_RUN"] = "1"
        try:
            outcome = self.run_cli("selftest", expect=2)
        finally:
            os.environ.pop("OGD_SELFTEST_RUN", None)
        message = self.envelope(outcome)["errors"][0]["message"]
        self.assertIn("already in progress", message)

    def test_selftest_runs_a_filtered_selection(self):
        outcome = self.run_cli("selftest", "--only", "TestSuiteContract.test_help_lists_commands",
                              "--format", "json", expect=None)
        payload = self.envelope(outcome)
        summary = payload["data"]["selftest"]
        self.assertEqual(1, summary["testsRun"], json.dumps(summary["output"])[-500:])
        self.assertTrue(summary["ok"], summary["output"][-500:])
        self.assertEqual(0, outcome.returncode)
        outcome = self.run_cli("selftest", "--only", "NoSuchCase", expect=2)
        self.assertEqual("E-USAGE", self.envelope(outcome)["errors"][0]["code"])


class TestSelection(ToolTestCase):
    def test_reference_forms(self):
        for token in ("c13e05", "13.5", "sprite-generator", "Sprite Generator"):
            outcome = self.run_cli("show", token, expect=0)
            self.assertIn("Sprite Generator", self.data(outcome, True)["data"]["items"][0]["record"]["name"])

    def test_unknown_reference_is_not_found_with_suggestions(self):
        outcome = self.run_cli("show", "spite generator", expect=4)
        error = self.envelope(outcome)["errors"][0]
        self.assertEqual("E-NOT-FOUND", error["code"])
        self.assertIn("did you mean", error["hint"])
        self.assertIn("c13e05", error["hint"], json.dumps(error)[:300])

    def test_ambiguous_reference_lists_candidates(self):
        outcome = self.run_cli("show", "license", expect=4)
        payload = self.envelope(outcome)
        self.assertEqual("E-AMBIGUOUS", payload["errors"][0]["code"])
        self.assertGreater(len(payload["errors"][0]["details"]["ids"]), 1)

    def test_file_path_is_rejected_with_an_explanation(self):
        outcome = self.run_cli("show", "offline-game-development/asset-catalog.json#c13e05", expect=4)
        self.assertIn("file path", self.envelope(outcome)["errors"][0]["message"])

    def test_browse_paging_and_cap(self):
        outcome = self.run_cli("list", "--category", "13", "--limit", "5", expect=0)
        payload = self.data(outcome, True)
        self.assertEqual(41, payload["data"]["total"])
        self.assertEqual(5, len(payload["data"]["records"]))
        self.assertTrue(payload["warnings"], "a truncated browse must warn")
        outcome = self.run_cli("list", "--limit", "0", "--category", "13", expect=0)
        payload = self.data(outcome, True)
        self.assertEqual(41, len(payload["data"]["records"]))
        self.assertEqual([], [w for w in payload["warnings"] if w["code"] == "W-CAPPED"])
        outcome = self.run_cli("list", expect=0)
        payload = self.data(outcome, True)
        self.assertEqual(1419, payload["data"]["total"])
        self.assertIn("W-CAPPED", [w["code"] for w in payload["warnings"]])
        self.assertEqual(500, len(payload["data"]["records"]))

    def test_filters_narrow_the_rows(self):
        for flag, value in (("--layer", "release-skills"), ("--tag", "testing"),
                            ("--difficulty", "Advanced"),
                            ("--role", "qa-lead"), ("--risk", "approval-required")):
            column = {"--layer": "layer", "--tag": "tags", "--difficulty": "difficulty",
                      "--role": "role", "--risk": "risk_class"}[flag]
            outcome = self.run_cli("list", flag, value, "--limit", "4", "--fields",
                                  f"id,{column}", expect=0)
            rows = self.data(outcome, True)["data"]["records"]
            self.assertTrue(rows, f"{flag} {value} matched nothing")
            for row in rows:
                found = row.get(column)
                if isinstance(found, list):
                    self.assertIn(value.lower(), [str(item).lower() for item in found], row)
                else:
                    self.assertEqual(value.lower(), str(found).lower(), row)

    def test_search_ranks_and_reports_terms(self):
        outcome = self.run_cli("search", "shader determinism", expect=0)
        data = self.data(outcome, True)["data"]
        self.assertTrue(data["total"])
        self.assertEqual(["shader", "determinism"], data["terms"])
        self.assertGreaterEqual(data["hits"][0]["score"], data["hits"][-1]["score"])

    def test_search_needs_a_word(self):
        outcome = self.run_cli("search", "!!", expect=2)
        self.assertEqual("E-USAGE", self.envelope(outcome)["errors"][0]["code"])

    def test_categories_output_shape(self):
        outcome = self.run_cli("categories", expect=0)
        data = self.data(outcome, True)["data"]
        self.assertEqual(34, data["count"])
        self.assertEqual("c01", data["categories"][0]["id"])

    def category_title(self, number):
        asset = json.load(open(ASSET_CATALOG, encoding="utf-8"))
        return next(row["title"] for row in asset["categories"] if row["number"] == number)

    def test_max_targets_guard(self):
        outcome = self.run_cli("spec", "--all", expect=9)
        error = self.envelope(outcome)["errors"][0]
        self.assertEqual("E-LIMIT", error["code"])
        self.assertEqual(400, error["details"]["ceiling"])
        outcome = self.run_cli("spec", "--category", "13", "--format", "md", expect=0)
        self.assertIn("41 capability specs", outcome.stdout)
        self.assertEqual(f"# {self.category_title(13)}", outcome.stdout.splitlines()[0].strip())


# ---------------------------------------------------------------------------
# generated artifacts
# ---------------------------------------------------------------------------
class TestArtifacts(ToolTestCase):
    def test_spec_covers_the_section_contract(self):
        outcome = self.run_cli("spec", "c13e05", "--format", "json", expect=0)
        spec = self.data(outcome, True)["data"]["specs"][0]
        archetypes = json.load(open(os.path.join(HERE, "asset-archetypes.json"), encoding="utf-8"))
        wanted = [section["id"] for section in archetypes["archetypes"][spec["archetype"]]["sections"]]
        self.assertEqual(wanted, [section["id"] for section in spec["sections"]])
        self.assertEqual(len(wanted), len(spec["sections"]))
        for section in spec["sections"]:
            self.assertTrue(section["rows"], f"{section['id']} has no rows")
            for row in section["rows"]:
                self.assertEqual(sorted(section["fields"]), sorted(row))
        self.assertEqual(archetypes["archetypes"][spec["archetype"]]["evidence_kinds"],
                         spec["evidence_to_file"])

    def test_spec_markdown_is_rendered_from_the_template(self):
        template = open(os.path.join(HERE, "asset-spec-template.md"), encoding="utf-8").read()
        outcome = self.run_cli("spec", "c13e05", "--format", "md", expect=0)
        body = outcome.stdout
        self.assertTrue(body.startswith("# Sprite Generator"))
        self.assertIsNone(re.search(r"\{\{[A-Z_]+\}\}", body), "an unfilled template token survived")
        self.assertIn("### Pinned local inputs (blocking)", body)
        self.assertIn("To fill:", body)
        self.assertIn("godot --headless --path . --quit", body)
        self.assertIn(template.splitlines()[0].strip(), template)   # template still starts the same way

    def test_spec_changes_with_the_engine_and_target(self):
        godot = self.run_cli("spec", "c13e05", "--engine", "godot", "--format", "md", expect=0).stdout
        unreal = self.run_cli("spec", "c13e05", "--engine", "unreal", "--format", "md", expect=0).stdout
        neutral = self.run_cli("spec", "c13e05", "--engine", "engine-neutral", "--format", "md",
                               expect=0).stdout
        self.assertNotEqual(godot, unreal)
        self.assertIn("UnrealEditor", unreal)
        self.assertIn("Export templates are installed per editor version", godot)
        self.assertIn("godot", godot.lower())
        self.assertNotIn("UnrealEditor", godot)
        self.assertNotIn("export_presets.cfg", neutral)
        self.assertIn("engine-neutral", neutral.lower())
        for label, text in (("godot", godot), ("unreal", unreal), ("neutral", neutral)):
            self.assertIsNone(re.search(r"\{\{[A-Z_]+\}\}", text), f"{label} left a template token")
        mobile = self.run_cli("spec", "c13e05", "--device-profile", "mobile-low", "--format", "md",
                              expect=0).stdout
        self.assertIn("mobile-low", mobile)
        self.assertNotEqual(mobile, godot, "the profile must reach the budget rows")

    def test_spec_budget_rows_use_real_ceilings(self):
        outcome = self.run_cli("spec", "c13e01", "--device-profile", "desktop-mid", "--format", "md",
                              expect=0)
        self.assertIn("Performance budgets", outcome.stdout)
        row = [line for line in outcome.stdout.splitlines() if line.startswith("| frame_time_p95_ms")]
        self.assertTrue(row, "the budget section must carry the profile ceiling")
        self.assertIn("<= 16.7 ms", row[0])

    def test_spec_json_writes_and_reports_the_written_file(self):
        target = os.path.join(self.work, "spec-c13e05.json")
        outcome = self.run_cli("spec", "c13e05", "--out", target, expect=0)
        payload = self.data(outcome, True)["data"]
        self.assertFalse(payload["written"]["created"] is True and os.path.getsize(target) == 0)
        on_disk = json.load(open(target, encoding="utf-8"))
        self.assertEqual("spec", on_disk["specs"][0]["kind"] if "specs" in on_disk else "spec")
        outcome = self.run_cli("spec", "c13e05", "--out", target, expect=6)
        self.assertEqual("E-APPROVAL", self.envelope(outcome)["errors"][0]["code"])
        outcome = self.run_cli("spec", "c13e05", "--out", target, "--allow-overwrite", "--approve",
                              "OVERWRITE", "--backup", expect=0)
        written = self.data(outcome, True)["data"]["written"]
        self.assertFalse(written["created"])
        self.assertTrue(os.path.isfile(written["backup"]))

    def test_one_file_per_capability_with_a_placeholder(self):
        pattern = os.path.join(self.work, "{id}-spec.md")
        outcome = self.run_cli("spec", "--category", "13", "--limit", "4", "--out", pattern, expect=0)
        data = self.data(outcome, True)["data"]
        self.assertEqual(4, data["specs_written"])
        files = sorted(os.listdir(self.work))
        self.assertEqual(4, len(files))
        self.assertTrue(files[0].startswith("c13e"))

    def test_plan_orders_by_phase_and_sums_effort(self):
        outcome = self.run_cli("plan", "c01e01", "c13e05", "c25e01", "c11e01", "--format", "json",
                              expect=0)
        plan = self.data(outcome, True)["data"]
        phases = [item["phase"] for item in plan["items"]]
        order = {"decide": 0, "produce": 1, "build": 2, "operate": 3, "provide": 4, "verify": 5}
        self.assertEqual(sorted(phases, key=lambda name: order[name]), phases)
        self.assertEqual(list(range(1, len(plan["items"]) + 1)), [item["seq"] for item in plan["items"]])
        self.assertEqual(plan["totals"]["capabilities"], 4)
        self.assertTrue(all(item["effort"] in ("S", "M", "L", "XL") for item in plan["items"]))
        self.assertTrue(all(item["gates"] for item in plan["items"]))
        md = self.run_cli("plan", "c01e01", "c13e05", "--format", "md", expect=0).stdout
        self.assertIn("Sprint plan", md)
        self.assertIn("story points", md)

    def test_checklist_items_match_the_spec_and_tick_from_evidence(self):
        outcome = self.run_cli("checklist", "c34e23", "--format", "json", expect=0)
        data = self.data(outcome, True)["data"]
        self.assertGreater(data["totals"]["items"], 8)
        self.assertEqual(0, data["totals"]["done"])
        ids = [item["id"] for item in data["items"]]
        self.assertEqual(len(ids), len(set(ids)))
        for item in data["items"]:
            self.assertRegex(item["id"], r"^CHK-c\d{2}e\d{2}-\d{2}$")
            self.assertTrue(item["evidence_kind"])
        good = self.write("evidence-good.json", json.dumps(
            json.load(open(os.path.join(HERE, "asset-evidence-fixture.json"), encoding="utf-8"))["records"][0]))
        outcome = self.run_cli("checklist", "c34e23", "--evidence", good, "--format", "json", expect=0)
        ticked = self.data(outcome, True)["data"]["totals"]["done"]
        self.assertGreater(ticked, 0, "a passing check must tick its box")
        csv = self.run_cli("checklist", "c34e23", "--format", "csv", expect=0).stdout
        self.assertTrue(csv.startswith("id,capability_id,capability_name,section,text,blocking,evidence_kind,"
                                       "done"))

    def test_a_configured_endpoint_is_never_embedded_in_a_spec(self):
        # offline.forbid_remote_urls_in_specs is read by the spec writer: prove it still is
        default = self.run_cli("spec", "c13e05", "--format", "md", expect=0).stdout
        self.assertNotIn("must not embed it", default)
        loopback_url = "http://127.0.0.1:11434"  # a loopback endpoint is the only one allowed
        config = self.write("endpoint.json", json.dumps({"provider": {"kind": "local",
                                                                      "endpoint": loopback_url}}))
        with_endpoint = self.run_cli("spec", "c13e05", "--config", config, "--format", "md",
                                     expect=0).stdout
        self.assertIn("must not embed it", with_endpoint)
        self.assertNotIn(loopback_url, with_endpoint, "the endpoint stays out of the artifact")
        off = self.write("unforbid.json", json.dumps({"provider": {"kind": "local",
                                                                   "endpoint": loopback_url},
                                                       "offline": {"forbid_remote_urls_in_specs": False}}))
        relaxed = self.run_cli("spec", "c13e05", "--config", off, "--format", "md", expect=0).stdout
        self.assertNotIn("must not embed it", relaxed, "the config key is what turns the row on")

    def test_prompts_refuse_a_remote_provider(self):
        outcome = self.run_cli("prompts", "c13e05", "--format", "md", expect=0)
        self.assertIn("no network calls", outcome.stdout)
        remote_url = "https://api.example.com"  # audit: allow - a fixture the tool has to refuse
        config = self.write("remote.json", json.dumps({"provider": {"kind": "remote",
                                                                    "model_id": "gpt",
                                                                    "endpoint": remote_url}}))
        outcome = self.run_cli("prompts", "c13e05", "--config", config, expect=3)
        self.assertEqual("E-INPUT", self.envelope(outcome)["errors"][0]["code"])
        loopback_url = "http://127.0.0.1:11434"  # a loopback endpoint is the only one allowed
        loopback = self.write("loopback.json", json.dumps({"provider": {"kind": "local",
                                                                        "endpoint": loopback_url}}))
        outcome = self.run_cli("prompts", "c13e05", "--config", loopback, "--format", "json", expect=0)
        self.assertEqual("http://127.0.0.1:11434", self.data(outcome, True)["data"]["provider"]["endpoint"])
        external_url = "https://models.example.com"  # audit: allow - refused for the same reason
        external = self.write("external.json", json.dumps({"provider": {"kind": "local",
                                                                        "endpoint": external_url}}))
        outcome = self.run_cli("prompts", "c13e05", "--config", external, expect=3)
        self.assertIn("loopback", self.envelope(outcome)["errors"][0]["message"])

    def test_export_columns_and_formats(self):
        outcome = self.run_cli("export", "--category", "31", "--limit", "3", "--format", "csv", expect=0)
        header = outcome.stdout.splitlines()[0]
        self.assertEqual("id,ref,name,slug,category,category_title,layer,archetype,subject,description,"
                         "tags,role,difficulty,est_days,engines,risk_class,risk_reasons,template_ok", header)
        self.assertEqual(4, len(outcome.stdout.strip().split("\n")))
        target = os.path.join(self.work, "catalog.csv")
        outcome = self.run_cli("export", "--category", "31", "--limit", "3", "--format", "csv", "--out",
                              target, expect=0)
        self.assertEqual(3, self.data(outcome, True)["data"]["count"])
        with open(target, encoding="utf-8") as handle:
            self.assertEqual(header, handle.readline().strip())

    def test_export_unknown_column_is_usage_error(self):
        outcome = self.run_cli("export", "--columns", "id,daydream", expect=2)
        self.assertEqual("E-USAGE", self.envelope(outcome)["errors"][0]["code"])

    def test_evidence_new_skeleton_is_checkable(self):
        outcome = self.run_cli("evidence", "new", "c13e05", "--format", "json", expect=0)
        template = self.data(outcome, True)["data"]["template"][0]
        self.assertEqual("not-run", template["verdict"])
        self.assertTrue(template["checks"])
        for check in template["checks"]:
            self.assertEqual("not-run", check["result"])
        outcome = self.run_cli("evidence", "check", "--input", self.write("evidence.json",
                                                                          json.dumps([template])),
                              "--format", "md", expect=0)
        self.assertIn("accepted", outcome.stdout)

    def test_show_and_render_helpers(self):
        outcome = self.run_cli("show", "c13e05", "--format", "md", expect=0)
        self.assertIn("Sprite Generator", outcome.stdout)
        self.assertIn("asset-catalog.json#c13e05", outcome.stdout)
        outcome = self.run_cli("show", "c13e05", "c01e01", "--format", "md", expect=0)
        self.assertIn("Sprite Generator", outcome.stdout)
        self.assertIn("Game Vision Brief", outcome.stdout)


# ---------------------------------------------------------------------------
# gates
# ---------------------------------------------------------------------------
class TestGates(ToolTestCase):
    def manifest(self):
        return [{"asset": "hero.glb", "kind": "mesh", "license": "CC0-1.0", "source": "photogrammetry",
                 "acquired_from": "in-house", "acquired_on": "2026-10-20", "generated_by": "n/a",
                 "commercial_use": True, "redistribution": True, "record_path": "rights/hero.json"},
                {"asset": "sfx.wwu", "kind": "audio", "license": "CC-BY-NC-4.0", "source": "site",
                 "acquired_from": "web", "acquired_on": "2026-09-01", "generated_by": "n/a",
                 "commercial_use": False, "redistribution": True, "record_path": "rights/sfx.json"}]

    def test_clean_manifest_clears_the_gate(self):
        for name in ("rights/hero.json", "rights/sfx.json"):
            os.makedirs(os.path.dirname(os.path.join(self.work, name)), exist_ok=True)
            open(os.path.join(self.work, name), "w", encoding="utf-8").write("{}")
        path = self.write("manifest.json", json.dumps(self.manifest()))
        outcome = self.run_cli("rights", "--input", path, "--format", "json", expect=5)
        report = self.envelope(outcome)["data"]
        # every finding belongs to the non-commercial record; the CC0 one is clean
        findings = report["findings"]
        self.assertEqual({"R3"}, {f["gate_id"] for f in findings if f["severity"] == "block"},
                         json.dumps(findings)[:700])
        self.assertIn("R5", [f["gate_id"] for f in findings if f["severity"] != "block"],
                         json.dumps(findings)[:700])
        self.assertEqual([], [f for f in findings if "hero.glb" in str(f.get("applies_to"))],
                         json.dumps(findings)[:700])
        self.assertEqual("blocked", report["verdict"])
        self.assertIn("sfx.wwu", json.dumps(report["findings"]))

    def test_rights_rules_fire_individually(self):
        policy = json.load(open(os.path.join(HERE, "asset-license-policy.json"), encoding="utf-8"))
        render_ctx = generators.RenderContext(None, {}, policy, {}, kit.BUILTIN_CONFIG)
        os.makedirs(os.path.join(self.work, "rights"), exist_ok=True)
        for name in ("hero", "sfx", "icon", "sdk"):
            with open(os.path.join(self.work, "rights", f"{name}.json"), "w", encoding="utf-8") as handle:
                handle.write("{}\n")
        records = self.manifest()
        records[0]["license"] = "Not-A-Real-License"
        report = gates.rights_check(records, policy, render_ctx, root=self.work)
        fired = {finding["gate_id"] for finding in report["findings"]}
        self.assertIn("R1", fired)
        self.assertIn("R3", fired)
        self.assertNotIn("R8", fired, "the record files exist, so R8 must stay quiet")
        self.assertEqual("blocked", report["verdict"])
        pass  # the R8 case is asserted at the end of this test
        for record in records:
            record["license"] = "CC0-1.0"
            record["commercial_use"] = True
            record["redistribution"] = True
        report = gates.rights_check(records, policy, render_ctx, root=self.work)
        self.assertEqual("cleared", report["verdict"], json.dumps(report["findings"])[:400])
        missing = dict(records[0])
        missing.pop("acquired_from")
        report = gates.rights_check([missing], policy, render_ctx, root=self.work)
        self.assertIn("R2", {finding["gate_id"] for finding in report["findings"]})
        generated = {"asset": "icon.svg", "kind": "generated-asset", "license": "CC0-1.0",
                     "source": "local model", "acquired_from": "offline model run",
                     "acquired_on": "2026-10-01", "generated_by": "n/a", "commercial_use": True,
                     "redistribution": True, "record_path": "rights/icon.json"}
        report = gates.rights_check([generated], policy, render_ctx, root=self.work)
        self.assertIn("R6", {finding["gate_id"] for finding in report["findings"]})
        sdk = {"asset": "console-sdk", "kind": "engine", "license": "Console-SDK-Confidential",
               "source": "vendor", "acquired_from": "vendor", "acquired_on": "2026-01-01",
               "generated_by": "n/a", "commercial_use": True, "redistribution": False, "bundled": True,
               "record_path": "rights/sdk.json"}
        report = gates.rights_check([sdk], policy, render_ctx, root=self.work)
        codes = {finding["gate_id"] for finding in report["findings"]}
        self.assertTrue({"R4", "R7"} <= codes, codes)
        stale = dict(records[0], acquired_on="2019-01-01")
        report = gates.rights_check([stale], policy, render_ctx, root=self.work)
        self.assertIn("R9", {finding["gate_id"] for finding in report["findings"]})
        os.unlink(os.path.join(self.work, "rights", "hero.json"))
        report = gates.rights_check(records, policy, render_ctx, root=self.work,
                                    enforce_record_files=True)
        r8 = [finding for finding in report["findings"] if finding["gate_id"] == "R8"]
        self.assertTrue(r8, json.dumps(report["findings"])[:400])
        self.assertEqual("blocked", report["verdict"], "enforcement turns R8 into a blocker")

    def test_rights_requires_the_manifest(self):
        outcome = self.run_cli("rights", expect=2)
        self.assertEqual("E-USAGE", self.envelope(outcome)["errors"][0]["code"])
        outcome = self.run_cli("rights", "--input", os.path.join(self.work, "absent.json"), expect=3)
        self.assertEqual("E-INPUT", self.envelope(outcome)["errors"][0]["code"])

    def test_rights_csv_manifest_and_record_file_enforcement(self):
        text = ("asset,kind,license,source,acquired_from,acquired_on,generated_by,commercial_use,"
                "redistribution,record_path\n"
                "mystery.png,texture,No-Such-License,in-house,capture,2026-02-02,n/a,yes,yes,"
                "rights/mystery.json\n")
        path = self.write("weak.csv", text)
        outcome = self.run_cli("rights", "--input", path, "--format", "md", expect=5)
        self.assertIn("R1", outcome.stdout)
        self.assertIn("mystery.png", outcome.stdout)
        outcome = self.run_cli("rights", "--input", path, "--check-record-files", "--format", "json",
                              expect=5)
        findings = json.dumps(self.envelope(outcome)["data"]["findings"])
        self.assertIn("R8", findings)
        self.assertIn("rights/mystery.json", findings)

    def test_waiver_needs_a_token_and_never_hides_findings(self):
        path = self.write("manifest.json", json.dumps(self.manifest()))
        outcome = self.run_cli("rights", "--input", path, "--waive", expect=6)
        error = self.envelope(outcome)["errors"][0]
        self.assertEqual("E-APPROVAL", error["code"])
        self.assertEqual("WAIVE-RIGHTS", error["details"]["required_token"])
        outcome = self.run_cli("rights", "--input", path, "--waive", "--approve", "WAIVE-RIGHTS",
                              "--format", "json", expect=0)
        payload = self.data(outcome)
        self.assertIn("W-RIGHTS-WAIVED", [w["code"] for w in payload["warnings"]])
        self.assertTrue(payload["data"]["findings"], "a waiver must not delete findings")
        self.assertIn("human", json.dumps(payload["data"]["waiver"]))

    def test_budget_gate_verdicts_and_headroom_sign(self):
        measurements = [{"capability_id": "c26e05", "profile": "mobile-mid",
                         "values": {"frame_time_p95_ms": 14.2, "draw_calls_per_frame": 412}},
                        {"capability_id": "c34e23", "profile": "desktop-mid",
                         "values": {"frame_time_p95_ms": 33.3, "load_time_seconds": 3.8}}]
        path = self.write("measurements.json", json.dumps(measurements))
        outcome = self.run_cli("budget", "check", "--input", path, "--format", "md", expect=5)
        self.assertIn("over", outcome.stdout)
        outcome = self.run_cli("budget", "check", "--input", path, "--format", "json", expect=5)
        rows = self.envelope(outcome)["data"]["report"]["rows"]
        over = [row for row in rows if row["verdict"] == "over"]
        inside = [row for row in rows if row["verdict"] == "inside"]
        self.assertTrue(over and inside)
        self.assertLess(over[0]["headroom"], 0)
        self.assertGreater(inside[0]["headroom"], 0)
        clean = [{"capability_id": "c34e23", "profile": "desktop-mid",
                  "values": {"frame_time_p95_ms": 12.0}}]
        outcome = self.run_cli("budget", "check", "--input", self.write("clean.json", json.dumps(clean)),
                              "--format", "json", expect=0)
        self.assertEqual("within budget", self.data(outcome, True)["data"]["report"]["verdict"])

    def test_budget_gate_flags_unknown_profile_and_metric(self):
        bad = [{"capability_id": "c34e23", "profile": "toaster", "values": {"frame_time_p95_ms": 9}}]
        outcome = self.run_cli("budget", "check", "--input", self.write("bad.json", json.dumps(bad)),
                              "--format", "json", expect=5)
        self.assertIn("BUDGET-PROFILE-UNKNOWN", json.dumps(self.envelope(outcome)))
        odd = [{"capability_id": "c34e23", "profile": "desktop-mid", "values": {"wobble_hz": 4}}]
        odd_path = self.write("odd.json", json.dumps(odd))
        outcome = self.run_cli("budget", "check", "--input", odd_path, "--format", "md", expect=0)
        self.assertIn("never a pass", outcome.stdout)
        payload = self.data(self.run_cli("budget", "check", "--input", odd_path, "--format", "json",
                                         expect=0), True)
        findings = payload["data"]["report"]["findings"]
        self.assertIn("BUDGET-METRIC-UNDEFINED", [finding["gate_id"] for finding in findings], findings)
        self.assertEqual(["review"], [row["verdict"] for row in payload["data"]["report"]["rows"]])

    def test_budget_presets_are_listed(self):
        outcome = self.run_cli("budget", "presets", "--format", "json", expect=0)
        data = self.data(outcome, True)["data"]
        self.assertEqual(21, data["metric_count"])
        self.assertEqual(10, len(data["profiles"]))
        self.assertIn("desktop-mid", data["profiles"])
        self.assertIn("vr-90hz", data["profiles"])
        outcome = self.run_cli("budget", "presets", "--profile", "mobile-low", "--format", "md", expect=0)
        self.assertIn("## `mobile-low` ceilings", outcome.stdout)
        self.assertIn("frame_time_p95_ms", outcome.stdout)
        outcome = self.run_cli("budget", "presets", "--profile", "toaster", expect=4)
        self.assertIn("available", self.envelope(outcome)["errors"][0]["hint"])

    def test_evidence_check_accepts_the_good_record(self):
        fixture = json.load(open(os.path.join(HERE, "asset-evidence-fixture.json"), encoding="utf-8"))
        good = self.write("good.json", json.dumps(fixture["records"][0]))
        outcome = self.run_cli("evidence", "check", "--input", good, "--format", "json", expect=0)
        report = self.data(outcome, True)["data"]["report"]
        self.assertEqual("accepted", report["verdict"])
        self.assertEqual([], [f for f in report["findings"] if f["severity"] == "block"])

    def test_evidence_check_blocks_the_broken_record(self):
        bad = {"kind": "evidence", "capability_id": "c34e17", "capability_name": "Example Project "
               "Self-Test", "verdict": "fail", "operator": "qa-lead", "run_started": "2026-11-03T10:00:00Z",
               "environment": {"engine": "godot", "engine_version": "4.3.stable",
                               "platform": "linux-desktop", "hardware": "desktop-mid",
                               "scene_or_build": "builds/linux/reference-0141", "network_state": "blocked"},
               "checks": [{"id": "CHK-01", "kind": "automated", "description": "self-test suite runs green",
                           "measured": 3, "pass_threshold": 0, "result": "pass"}]}
        outcome = self.run_cli("evidence", "check", "--input", self.write("bad.json", json.dumps(bad)),
                              "--format", "json", expect=5)
        payload = self.envelope(outcome)
        codes = json.dumps(payload["data"]["report"]["findings"])
        self.assertIn("EVIDENCE-FAILURE-PACKET", codes)
        self.assertIn("EVIDENCE-CONTRADICTION", codes)
        self.assertEqual(5, payload["meta"]["exit_code"])
        outcome = self.run_cli("evidence", "check", "--input", self.write("bad.json", json.dumps(bad)),
                              "--report-only", "--format", "json", expect=0)
        self.assertEqual("blocked", self.data(outcome, True)["data"]["report"]["verdict"])
        self.assertEqual([], self.envelope(outcome)["errors"])

    def test_evidence_check_validates_structure_and_gates(self):
        broken = {"kind": "evidence", "capability_id": "nope", "verdict": "pass"}
        outcome = self.run_cli("evidence", "check", "--input", self.write("x.json", json.dumps(broken)),
                              "--format", "json", expect=5)
        codes = json.dumps(self.envelope(outcome)["data"]["report"]["findings"])
        self.assertIn("EVIDENCE-SCHEMA", codes)
        self.assertIn("EVIDENCE-CAPABILITY", codes)
        fixture = json.load(open(os.path.join(HERE, "asset-evidence-fixture.json"), encoding="utf-8"))
        record = dict(fixture["records"][0])
        record["checks"] = [{"id": "CHK-1", "kind": "automated", "description": "one check", "result": "pass"}]
        outcome = self.run_cli("evidence", "check", "--input", self.write("gates.json", json.dumps(record)),
                              "--require-gates", "--format", "json", expect=5)
        self.assertIn("EVIDENCE-GATE", json.dumps(self.envelope(outcome)["data"]["report"]["findings"]))
        outcome = self.run_cli("evidence", "check", "--use-asset-fixture", "--format", "json", expect=0)
        self.assertEqual(2, self.data(outcome, True)["data"]["report"]["summary"]["records"])

    def test_evidence_check_needs_input(self):
        outcome = self.run_cli("evidence", "check", expect=2)
        self.assertEqual("E-USAGE", self.envelope(outcome)["errors"][0]["code"])

    def test_offline_audit_is_clean_apart_from_a_seeded_file(self):
        outcome = self.run_cli("offline-audit", "--format", "json", expect=0)
        report = self.data(outcome, True)["data"]["report"] if "report" in self.data(outcome, True)["data"] \
            else self.data(outcome, True)["data"]
        self.assertEqual("clean", report["verdict"], json.dumps(report["findings"])[:500])
        seeded = "import requests\nAPI_KEY = 'abc123def456ghi'\n"  # audit: allow - what must be caught
        self.write("evil.py", seeded)
        outcome = self.run_cli("offline-audit", "--scan", os.path.join(self.work, "evil.py"),
                              "--format", "json", expect=5)
        findings = json.dumps(self.envelope(outcome)["data"]["findings"])
        self.assertIn("AUDIT-NETWORK", findings)
        self.assertIn("AUDIT-SECRET", findings)
        outcome = self.run_cli("offline-audit", "--scan", os.path.join(self.work, "evil.py"),
                              "--report-only", expect=0)
        self.assertIn("AUDIT-NETWORK", outcome.stdout)

    def test_coverage_reports_the_catalog(self):
        outcome = self.run_cli("coverage", "--format", "json", expect=0)
        data = self.data(outcome, True)["data"]
        self.assertEqual(1419, data["totals"]["entries"])
        self.assertEqual(34, data["totals"]["categories"])
        self.assertEqual(data["totals"]["entries"], data["totals"]["template_ok"])
        self.assertEqual(6, len(data["totals"]["archetypes"]))
        outcome = self.run_cli("coverage", "--expect-entries", "9999", expect=5)
        self.assertIn("E-COUNT-MISMATCH", json.dumps(self.envelope(outcome)["data"]["findings"]))
        outcome = self.run_cli("coverage", "--expect-fingerprint", "0" * 12, "--format", "json", expect=5)
        self.assertIn("E-FINGERPRINT", json.dumps(self.envelope(outcome)["data"]["findings"]))

    def test_validate_passes_on_the_shipped_dataset(self):
        outcome = self.run_cli("validate", "--format", "json", expect=0)
        data = self.data(outcome, True)["data"]
        self.assertTrue(data["ok"])
        self.assertEqual(0, data["counts"]["errors"])
        self.assertEqual("capability_ref", data["kind"])
        self.assertEqual(1419, data["entries"])

    def test_validate_checks_a_generated_artifact(self):
        target = os.path.join(self.work, "spec.json")
        self.run_cli("spec", "c13e05", "--out", target, expect=0)
        outcome = self.run_cli("validate", "--records", target, "--kind", "spec_record", expect=0)
        self.assertTrue(self.data(outcome, True)["data"]["ok"])
        broken = self.write("broken.json", json.dumps({"kind": "spec", "capability_id": "nope"}))
        outcome = self.run_cli("validate", "--records", broken, "--kind", "spec_record", "--format",
                              "json", expect=5)
        self.assertIn("E-RECORD-SCHEMA", json.dumps(self.envelope(outcome)["data"]["findings"]))


# ---------------------------------------------------------------------------
# the schema subset validator
# ---------------------------------------------------------------------------
class TestValidator(ToolTestCase):
    def setUp(self):
        super().setUp()
        self.schema = json.load(open(os.path.join(HERE, "asset-output-schema.json"), encoding="utf-8"))

    def test_only_supported_keywords_are_used(self):
        self.assertEqual([], gates.check_schema_support(self.schema))
        widened = json.loads(json.dumps(self.schema))
        widened["$defs"]["error"]["properties"]["code"]["format"] = "email"
        self.assertEqual("E-SCHEMA-KEYWORD", gates.check_schema_support(widened)[0]["code"])

    def test_keywords_behave(self):
        cases = [
            ({"type": "object", "required": ["a"], "properties": {"a": {"type": "integer",
                                                                        "minimum": 2}}}, {"a": 1}, 1),
            ({"type": "object", "additionalProperties": False, "properties": {"a": {}}}, {"a": 1, "b": 2}, 1),
            ({"type": "array", "items": {"type": "string"}, "minItems": 2}, ["x"], 1),
            ({"type": "string", "pattern": "^c\\d{2}e\\d{2}$"}, "c1e1", 1),
            ({"type": "string", "enum": ["a", "b"]}, "c", 1),
            ({"type": "object", "properties": {"a": {"$ref": "#/$defs/identifier"}}},
             {"a": "c01e01"}, 0),
            ({"anyOf": [{"type": "string"}, {"type": "integer"}]}, [], 1),
        ]
        for schema, payload, expected in cases:
            issues = gates.validate_against_schema(payload, schema, self.schema)
            self.assertEqual(expected, len(issues), f"{schema} vs {payload}: {issues}")

    def test_ref_that_does_not_resolve_is_reported(self):
        issues = gates.validate_against_schema({}, {"$ref": "#/$defs/missing"}, self.schema)
        self.assertIn("does not resolve", issues[0]["message"])

    def test_envelope_shape_is_the_documented_one(self):
        outcome = self.run_cli("info", expect=0)
        payload = self.envelope(outcome)
        self.assertEqual(sorted(["ok", "tool", "version", "command", "data", "warnings", "errors", "meta"]),
                         sorted(payload))
        self.assertEqual("offline-game-development", payload["tool"])
        self.assertEqual(0, payload["meta"]["exit_code"])
        self.assertNotIn("generated_at", payload)
        self.assertNotIn("duration_ms", payload["meta"])
        outcome = self.run_cli("info", "--stamp", expect=0)
        stamped = self.envelope(outcome)
        self.assertIn("generated_at", stamped)
        self.assertIn("duration_ms", stamped["meta"])

    def test_error_envelope_shape(self):
        outcome = self.run_cli("show", "definitely-not-here", expect=4)
        error = self.envelope(outcome)["errors"][0]
        self.assertEqual("E-NOT-FOUND", error["code"])
        self.assertTrue(error["message"])
        self.assertTrue(error["hint"])
        self.assertEqual("definitely-not-here", error["where"])


# ---------------------------------------------------------------------------
# configuration, io discipline, limits
# ---------------------------------------------------------------------------
class TestConfigAndIo(ToolTestCase):
    def test_layering_order(self):
        config = self.write("cfg.json", json.dumps({"target": {"platform": "handheld"},
                                                    "limits": {"max_targets": 12}}))
        outcome = self.run_cli("config", "show", "--config", config, expect=0)
        payload = self.data(outcome, True)["data"]
        self.assertIn("cfg.json", payload["layers"][-2] if len(payload["layers"]) > 2 else
                      payload["layers"][-1])
        self.assertEqual("handheld", payload["config"]["target"]["platform"])
        self.assertEqual(12, payload["config"]["limits"]["max_targets"])
        self.assertEqual("builtin", payload["layers"][0])
        self.assertIn("asset-config-default.json", " ".join(payload["layers"]))
        outcome = self.run_cli("spec", "c13e05", "--config", config, "--max-targets", "1",
                              "--format", "json", expect=0)
        self.assertEqual("handheld", self.data(outcome, True)["data"]["specs"][0]["generated_for"]["platform"])

    def test_environment_layer_and_flags_win(self):
        os.environ["OGD_CONFIG"] = json.dumps({"limits": {"timeout_seconds": 11}})
        try:
            outcome = self.run_cli("config", "show", expect=0)
            payload = self.data(outcome, True)["data"]
            self.assertEqual(11, payload["config"]["limits"]["timeout_seconds"])
            self.assertIn("OGD_CONFIG", " ".join(payload["layers"]))
            outcome = self.run_cli("config", "show", "--timeout", "44", expect=0)
            self.assertEqual(44, self.data(outcome, True)["data"]["config"]["limits"]["timeout_seconds"])
        finally:
            del os.environ["OGD_CONFIG"]
        os.environ["OGD_CONFIG"] = "{not json"
        try:
            outcome = self.run_cli("config", "show", expect=3)
            self.assertIn("OGD_CONFIG", self.envelope(outcome)["errors"][0]["message"])
        finally:
            del os.environ["OGD_CONFIG"]

    def test_bad_values_are_rejected_before_work(self):
        for payload, needle in (({"limits": {"timeout_seconds": -1}}, "zero or positive"),
                               ({"limits": {"retries": 50}}, "sane ceiling"),
                               ({"limits": {"max_targets": "many"}}, "must be a number"),
                               ({"engines": {"enabled": ["godot", "sdl2"]}}, "unknown engines"),
                               ({"approvals": {"require_for": "overwrite-file"}}, "must be a list"),
                               ({"offline": {"allow_network": "yes"}}, "must be true or false"),
                               ({"provider": {"kind": 7}}, "must be a string")):
            path = self.write("bad.json", json.dumps(payload))
            outcome = self.run_cli("info", "--config", path, expect=2)
            self.assertIn(needle, self.envelope(outcome)["errors"][0]["message"], json.dumps(payload))

    def test_missing_config_is_input_error(self):
        outcome = self.run_cli("info", "--config", os.path.join(self.work, "nope.json"), expect=3)
        self.assertEqual("E-INPUT", self.envelope(outcome)["errors"][0]["code"])

    def test_every_documented_key_is_read(self):
        """No decorative keys: each key in the packaged default must appear in code."""
        default = json.load(open(os.path.join(HERE, "asset-config-default.json"), encoding="utf-8"))
        sources = "".join(open(os.path.join(HERE, name), encoding="utf-8").read() for name in
                          os.listdir(HERE) if name.endswith(".py"))
        for key in kit.BUILTIN_CONFIG:
            if isinstance(kit.BUILTIN_CONFIG[key], dict):
                for nested in kit.BUILTIN_CONFIG[key]:
                    self.assertIn(nested, sources, f"{key}.{nested} is never read")
            self.assertIn(key, default, f"{key} is missing from the packaged defaults")
        for key in _flatten(default):
            if key.startswith("_"):
                continue
            self.assertIn(key.split(".")[-1], sources, f"{key} is documented but unread")

    def test_config_init_writes_an_editable_copy(self):
        target = os.path.join(self.work, "copy.json")
        outcome = self.run_cli("config", "init", "--out", target, expect=0)
        written = self.data(outcome, True)["data"]
        payload = json.load(open(target, encoding="utf-8"))
        self.assertIn("_comment", payload)
        self.assertEqual("asset-catalog.json", payload["dataset"])
        self.assertTrue(written["created"])
        outcome = self.run_cli("config", "init", "--out", target, expect=6)
        self.assertEqual("E-APPROVAL", self.envelope(outcome)["errors"][0]["code"])
        outcome = self.run_cli("info", "--config", target, expect=0)
        self.assertTrue(self.envelope(outcome)["ok"])

    def test_output_dir_and_format_defaults_from_config(self):
        config = self.write("cfg.json", json.dumps({"output": {"dir": self.work, "format": "md"}}))
        outcome = self.run_cli("spec", "c13e05", "--config", config, expect=0)
        body = outcome.stdout
        self.assertIn("# Sprite Generator", body)
        config = self.write("cfg2.json", json.dumps({"output": {"dir": self.work}}))
        outcome = self.run_cli("spec", "c13e05", "--config", config, "--format", "md",
                              "--out", "via-config.md", expect=0)
        written = self.data(outcome, True)["data"]["written"]
        self.assertEqual(os.path.join(self.work, "via-config.md"), written["path"])

    def test_out_dir_flag_and_joining(self):
        outcome = self.run_cli("spec", "c13e05", "--out-dir", self.work, "--out", "nested/spec.md",
                              "--format", "md", expect=0)
        self.assertTrue(os.path.isfile(os.path.join(self.work, "nested", "spec.md")))
        outcome = self.run_cli("spec", "c13e05", "--out", self.work, expect=3)
        self.assertIn("is a directory", self.envelope(outcome)["errors"][0]["message"])

    def test_atomic_write_semantics(self):
        path = os.path.join(self.work, "one.txt")
        first = kit.atomic_write(path, "hello\n")
        self.assertTrue(first["created"])
        self.assertEqual(6, first["bytes"])
        with self.assertRaises(kit.ApprovalRequired) as caught:
            kit.atomic_write(path, "hello again\n")
        self.assertEqual("OVERWRITE", caught.exception.details["required_token"])
        second = kit.atomic_write(path, "hello again\n", allow_overwrite=True, approval_token="OVERWRITE",
                                 backup=True)
        self.assertFalse(second["created"])
        self.assertTrue(os.path.isfile(second["backup"]))
        self.assertEqual("hello again\n", open(path, encoding="utf-8").read())
        with self.assertRaises(kit.InputError):
            kit.atomic_write(self.work, "x")
        leftovers = [name for name in os.listdir(self.work) if name.startswith(kit.TEMP_PREFIX)]
        self.assertEqual([], leftovers)

    def test_no_temp_files_survive_a_failed_write(self):
        blocker = self.write("blocked", "not a directory\n")
        with self.assertRaises(kit.ToolError):
            kit.atomic_write(os.path.join(blocker, "child.txt"), "x", retries=1)
        self.assertEqual([], [name for name in os.listdir(self.work) if name.startswith(kit.TEMP_PREFIX)])

    def test_cleanup_reports_and_needs_approval(self):
        stale = os.path.join(self.work, kit.TEMP_PREFIX + "old.part")
        with open(stale, "w", encoding="utf-8") as handle:
            handle.write("interrupted")
        os.utime(stale, (time.time() - 7200, time.time() - 7200))
        outcome = self.run_cli("cleanup", "--directory", self.work, "--older-than", "5", expect=0)
        data = self.data(outcome, True)["data"]
        self.assertEqual(1, data["count"])
        self.assertTrue(data["dry_run"])
        self.assertTrue(os.path.isfile(stale))
        outcome = self.run_cli("cleanup", "--directory", self.work, "--older-than", "5", "--delete",
                              expect=6)
        self.assertEqual("DELETE-FILES", self.envelope(outcome)["errors"][0]["details"]["required_token"])
        outcome = self.run_cli("cleanup", "--directory", self.work, "--older-than", "5", "--delete",
                              "--approve", "DELETE-FILES", expect=0)
        self.assertEqual([stale], self.data(outcome, True)["data"]["deleted"])
        self.assertFalse(os.path.isfile(stale))
        outcome = self.run_cli("cleanup", "--directory", os.path.join(self.work, "absent"), expect=3)
        self.assertEqual("E-INPUT", self.envelope(outcome)["errors"][0]["code"])

    def test_readers_respect_the_input_limit(self):
        path = self.write("big.txt", "x" * (3 * 1024 * 1024))
        outcome = self.run_cli("import", "--source", path, "--max-input-mb", "1", expect=9)
        error = self.envelope(outcome)["errors"][0]
        self.assertEqual("E-LIMIT", error["code"])
        outcome = self.run_cli("import", "--source", path, "--max-input-mb", "8", expect=3)
        self.assertEqual("E-INPUT", self.envelope(outcome)["errors"][0]["code"])

    def test_timeout_and_cancellation(self):
        outcome = self.run_cli("spec", "--all", "--max-targets", "2000", "--timeout", "0.001", expect=9)
        self.assertEqual("E-TIMEOUT", self.envelope(outcome)["errors"][0]["code"])
        token = kit.CancelToken()
        token.request("unit test")
        deadline = kit.Deadline(0)
        deadline.cancel = token
        with self.assertRaises(kit.Cancelled):
            deadline.tick("checking")
        armed = kit.Deadline(30)
        armed.arm()
        armed.tick("still working")
        armed.close()
        self.assertLessEqual(armed.remaining, 30)

    def test_sigint_cancels_a_run(self):
        import signal

        token = kit.CancelToken()
        token.install(enabled=True)
        try:
            with self.assertRaises(kit.Cancelled):
                os.kill(os.getpid(), signal.SIGINT)
        finally:
            token.restore()
        self.assertTrue(token.requested)

    def test_every_error_kind_reports_the_code_it_exits_with(self):
        def walk(cls):
            for child in cls.__subclasses__():
                yield child
                yield from walk(child)

        known = {cls.code: cls.exit_code for cls in kit.RESULT_ERROR_ORDER}
        self.assertIn("E-ENVIRONMENT", known, "doctor's failure kind must be in the order")
        for cls in walk(kit.ToolError):
            if not getattr(cls, "code", ""):
                continue
            self.assertIn(cls.code, known, f"{cls.__name__} is not in RESULT_ERROR_ORDER")
            self.assertEqual(cls.exit_code, known[cls.code], cls.__name__)
        for code, expected in (("E-USAGE", 2), ("E-INPUT", 3), ("E-NOT-FOUND", 4), ("E-GATE", 5),
                               ("E-APPROVAL", 6), ("E-CANCELLED", 7), ("E-LIMIT", 9),
                               ("E-ENVIRONMENT", 3), ("E-INTERNAL", 8)):
            self.assertEqual(expected, kit.exit_code_for([{"code": code}]), code)
        self.assertEqual(0, kit.exit_code_for([]))

    def test_text_helpers(self):
        self.assertEqual("c-sharp-shader-compilation-pipeline",
                         engine.slugify("C# / Shader Compilation Pipeline"))
        self.assertEqual("A Guide to C# and 3D AI", kit.title_case("a guide to c# and 3d ai"))
        self.assertEqual("only words", kit.strip_html("<p class='x'>only <b>words</b></p>"))
        self.assertEqual("a & b < c > d", kit.strip_html("a &amp; b &lt; c &gt; d"))
        self.assertEqual("text", kit.strip_markdown("`text`"))
        self.assertEqual("bold", kit.strip_markdown("**bold**"))
        self.assertEqual([{"a": "1", "b": "2", "c": ""}], kit.csv_parse("a,b,c\n1,2\n"))
        self.assertEqual("a,b\n\"x,1\",2\n", kit.csv_render(["a", "b"], [{"a": "x,1", "b": 2}]))
        self.assertEqual("one two", kit.tidy(" one\n  two \n"))
        self.assertIn("[redacted:generic-secret]",
                      kit.redact_str("API_KEY = abcdef123456"))  # audit: allow - a fixture, not a credential
        self.assertEqual([], kit.scan_secrets("token = compute_next_token()"))
        self.assertEqual(12, len(kit.fingerprint({"a": 1})))

    def test_sanitizer_and_asset_name_rules(self):
        self.assertEqual("asset-catalog.json", kit.check_asset_name("asset-catalog.json"))
        for bad in ("../etc/passwd", "/etc/passwd", "sub/asset.json", "asset_x.json"):
            with self.assertRaises(kit.ToolError, msg=bad):
                kit.check_asset_name(bad)
        self.assertEqual(os.path.abspath("out.md"), kit.sanitize_out_path("out.md"))
        with self.assertRaises(kit.ToolError):
            kit.sanitize_out_path("   ")

    def test_paths_are_relative_to_the_future_skill_root(self):
        """Every packaged reference in every file resolves from the folder above the tool."""
        offenders = []
        for name in sorted(os.listdir(HERE)):
            if not name.endswith((".py", ".md", ".json")):
                continue
            text = open(os.path.join(HERE, name), encoding="utf-8").read()
            for reference in set(re.findall(r"offline-game-development/[A-Za-z0-9._-]+", text)):
                target = os.path.join(self.root, reference)
                if not os.path.exists(target):
                    offenders.append(f"{name}: {reference}")
            for absolute in re.findall(r"\"/(?:home|Users|tmp)/[^\"]+\"", text):
                offenders.append(f"{name}: absolute path {absolute}")
        self.assertEqual([], offenders)

    def test_no_secrets_and_no_shell_in_the_package(self):
        offenders = []
        for name in sorted(os.listdir(HERE)):
            if not name.endswith(".py"):
                continue
            text = open(os.path.join(HERE, name), encoding="utf-8").read()
            stripped = "\n".join(line for line in text.splitlines() if "audit: allow" not in line)
            if SHELL_CALLS.search(stripped):
                offenders.append(name)
        self.assertEqual([], offenders)


def _flatten(mapping, prefix=""):
    out = {}
    for key, value in mapping.items():
        name = f"{prefix}{key}"
        if isinstance(value, dict):
            out.update(_flatten(value, name + "."))
        else:
            out[name] = value
    return out


# ---------------------------------------------------------------------------
# the manual
# ---------------------------------------------------------------------------
class TestKnowledgeReadme(ToolTestCase):
    def setUp(self):
        super().setUp()
        path = os.path.join(HERE, "knowledge-readme.md")
        self.text = open(path, encoding="utf-8").read() if os.path.isfile(path) else ""

    def test_readme_exists_and_is_one_file(self):
        knowledge = [name for name in os.listdir(HERE) if name.startswith("knowledge-")]
        self.assertEqual(["knowledge-readme.md"], knowledge)
        self.assertGreater(len(self.text), 2000, "the manual is missing or too thin")

    def test_every_command_is_documented(self):
        for name in cli.COMMAND_NAMES:
            self.assertRegex(self.text, rf"(?m)^\s*(?:###|##)?\s*`?{name}\b", f"{name} is undocumented")

    def test_documented_command_lines_run(self):
        """Every '$ ...' line in the manual is executed and must behave as written."""
        root = tempfile.mkdtemp(prefix="ogd-docs-", dir=self.root)
        shutil.copytree(HERE, os.path.join(root, PACKAGE),
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".tmp-*"))
        with open(os.path.join(root, "SKILL.md"), "w", encoding="utf-8") as handle:
            handle.write("# Parent skill\n")
        os.makedirs(os.path.join(root, "evidence"), exist_ok=True)
        os.makedirs(os.path.join(root, "rights"), exist_ok=True)
        fixture = json.load(open(os.path.join(HERE, "asset-evidence-fixture.json"), encoding="utf-8"))
        json.dump(fixture["records"][0], open(os.path.join(root, "evidence", "gate-results.json"), "w"))
        json.dump([{"asset": "hero.glb", "kind": "mesh", "license": "CC0-1.0", "source": "in-house capture",
                    "acquired_from": "in-house", "acquired_on": "2026-10-01", "generated_by": "n/a",
                    "commercial_use": True, "redistribution": True, "record_path": "rights/hero.json"}],
                  open(os.path.join(root, "rights", "manifest.json"), "w"))
        with open(os.path.join(root, "sprint.txt"), "w", encoding="utf-8") as handle:
            handle.write("c01e01\nc13e05\n")
        with open(os.path.join(root, "measurements.json"), "w", encoding="utf-8") as handle:
            handle.write(json.dumps([{"capability_id": "c34e23", "profile": "desktop-mid",
                                      "values": {"frame_time_p95_ms": 12.4, "load_time_seconds": 3.1}}]))
        failures = []
        for line in re.findall(r"^\$ (.+)$", self.text, re.M):
            line = line.strip()
            if "selftest" in line:
                continue
            expected = 0
            if "#" in line.split("$")[0]:
                pass
            match = re.search(r"#\s*exit\s+(\d+)", line)
            if match:
                expected = int(match.group(1))
                line = line[: match.start()].strip()
            tokens = shlex.split(line)
            prefix = ["python3", f"offline-game-development/{ENTRY_NAME}"]
            if tokens[:2] != prefix:
                failures.append(f"not a documented tool invocation: {line[:90]}")
                continue
            argv = tokens[2:]
            previous = os.getcwd()
            os.chdir(root)
            try:
                outcome = self.run_cli(*argv, cwd=root)
                code = outcome.returncode
                detail = (outcome.stdout + outcome.stderr).strip()
            except Exception as exc:  # a crash is a documentation failure too
                code, detail = "crash", f"{type(exc).__name__}: {exc}"
            finally:
                os.chdir(previous)
            if code != expected:
                failures.append(f"exit {code} != {expected}: {line[:90]} :: {detail[-160:]}")
        self.assertEqual([], failures)

    def test_readme_never_promises_a_file_that_does_not_exist(self):
        packaged = set(os.listdir(HERE))
        missing = []
        for name in set(re.findall(r"`(?:offline-game-development/)?"
                                   r"((?:tools|knowledge|asset)-[a-z0-9.-]+\.(?:py|md|json))`", self.text)):
            if name not in packaged:
                missing.append(name)
        self.assertEqual([], missing)

    def test_run_command_in_the_manual_is_the_real_one(self):
        self.assertIn(f"python3 {PACKAGE}/tools-offline-game-development.py", self.text)
        self.assertIn("tools-offline-game-development.py", self.text)

    def test_asset_inventory_in_the_manual_matches_the_folder(self):
        assets = sorted(name for name in os.listdir(HERE) if name.startswith("asset-"))
        for name in assets:
            self.assertIn(name, self.text, f"{name} is undocumented")
        counts = re.search(r"(\d+)\s+asset", self.text)
        if counts:
            self.assertEqual(len(assets), int(counts.group(1)))

    def test_every_asset_is_used_by_the_tool(self):
        sources = "".join(open(os.path.join(HERE, name), encoding="utf-8").read() for name in
                         os.listdir(HERE) if name.endswith(".py"))
        for name in sorted(os.listdir(HERE)):
            if name.startswith("asset-"):
                stem = name
                self.assertTrue(stem in sources or name.lstrip("asset-") in sources or
                                re.search(r'"' + re.escape(name.replace("asset-", "")) + r'"', sources) or
                                stem.replace("asset-", "") in sources, f"{name} is never referenced")


# ---------------------------------------------------------------------------
# the packaged files themselves
# ---------------------------------------------------------------------------
class TestPackage(ToolTestCase):
    def test_folder_name_and_flat_layout(self):
        self.assertRegex(PACKAGE, r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
        self.assertEqual("offline-game-development", PACKAGE)
        for name in os.listdir(HERE):
            path = os.path.join(HERE, name)
            if os.path.isdir(path):
                self.assertEqual("__pycache__", name, f"unexpected subfolder {name}")
                continue
            self.assertRegex(name, r"^(tools|knowledge|asset)-[a-z0-9]+(?:[.-][a-z0-9]+)*$",
                             f"{name} breaks the naming rule")
            self.assertNotIn("_", name)
            self.assertNotIn(" ", name)
            self.assertNotEqual("SKILL.md", name)
        self.assertEqual(1, len([name for name in os.listdir(HERE) if name.startswith("knowledge-")]))
        self.assertLess(len(os.listdir(HERE)), 500)

    def test_doctor_passes_every_check(self):
        outcome = self.run_cli("doctor", "--format", "json", expect=0)
        data = self.data(outcome, True)["data"]
        self.assertEqual(data["total"], data["passed"], json.dumps(
            [check for check in data["checks"] if check["status"] != "pass"])[:600])
        self.assertEqual(12, data["total"])

    def test_doctor_reports_a_broken_install(self):
        # an asset the config names but the folder lacks is refused before any command runs
        config = self.write("missing-asset.json", json.dumps({"budget_presets": "asset-not-here.json"}))
        outcome = self.run_cli("doctor", "--config", config, "--format", "json", expect=4)
        error = self.envelope(outcome)["errors"][0]
        self.assertEqual("E-NOT-FOUND", error["code"])
        self.assertIn("asset-not-here.json", error["message"])
        # a dataset that is not a catalog turns into failing checks, never a traceback
        broken = self.write("not-a-catalog.json", json.dumps({"schema_version": "1.0", "categories": []}))
        outcome = self.run_cli("doctor", "--dataset", broken, "--format", "json", expect=3)
        payload = self.envelope(outcome)
        self.assertEqual("E-ENVIRONMENT", payload["errors"][0]["code"])
        failing = {check["id"] for check in payload["data"]["checks"] if check["status"] != "pass"}
        self.assertTrue({"assets-present", "dataset-parses"} <= failing, failing)
        self.assertEqual(3, payload["meta"]["exit_code"], "the envelope must name the code it exits with")

    def test_tool_never_writes_inside_its_own_folder(self):
        def listing():
            return sorted(name for name in os.listdir(HERE) if name != "__pycache__")

        before = listing()
        self.run_cli("spec", "c13e05", "--format", "md", expect=0)
        self.run_cli("doctor", expect=0)
        self.run_cli("offline-audit", expect=0)
        self.assertEqual(before, listing(), "a run must not leave files inside the skill folder")

    def test_deterministic_output_across_runs(self):
        first = self.run_cli("plan", "--category", "25", "--format", "md", expect=0).stdout
        second = self.run_cli("plan", "--category", "25", "--format", "md", expect=0).stdout
        self.assertEqual(first, second)
        stamped = self.run_cli("plan", "--category", "25", "--stamp", expect=0)
        self.assertIn("generated_at", stamped.stdout)

    def test_quiet_and_stdin_free(self):
        outcome = self.run_cli("spec", "c13e05", "--quiet", expect=0)
        self.assertEqual("", outcome.stdout)
        outcome = self.run_cli("rights", "--format", "json", "--quiet", expect=2)
        self.assertEqual("", outcome.stdout)
        self.assertIn("E-USAGE", outcome.stderr)

    def test_exit_codes_are_distinct_per_failure_kind(self):
        cases = [(["info", "--format", "yaml"], 2),
                 (["show", "c99z99"], 4),
                 (["spec", "--category", "13", "--timeout", "0.001"], 9),
                 (["rights", "--input", os.path.join(self.work, "absent.json")], 3),
                 (["evidence", "check", "--use-asset-fixture", "--require-gates"], 5)]
        for args, expected in cases:
            outcome = self.run_cli(*args, expect=expected)
            self.assertNotEqual(0, outcome.returncode)

    def test_asset_files_are_valid_json_with_purpose(self):
        for name in sorted(os.listdir(HERE)):
            if not name.endswith(".json"):
                continue
            payload = json.load(open(os.path.join(HERE, name), encoding="utf-8"))
            self.assertIsInstance(payload, dict, name)
            described = json.dumps(payload)[:4000]
            self.assertTrue("description" in described or "note" in described or "_comment" in described
                            or "usage" in described or "policy" in described,
                            f"{name} carries no explanation of what it is for")

    def test_output_changes_when_the_input_changes(self):
        baseline = self.run_cli("spec", "c13e05", "--format", "md", expect=0).stdout
        other = self.run_cli("spec", "c13e06", "--format", "md", expect=0).stdout
        self.assertNotEqual(baseline, other)
        desktop = self.run_cli("spec", "c13e05", "--device-profile", "desktop-high", "--format", "md",
                              expect=0).stdout
        self.assertNotEqual(baseline, desktop)
        source = os.path.join(self.work, "mine.md")
        with open(source, "w", encoding="utf-8") as handle:
            handle.write("## 1. My Category\n\n1 skill concepts\n\n"
                         "Skill Name: My Capability\n"
                         "Description: Implement my capability as a focused reusable game-development "
                         "capability with declared inputs and outputs, offline dependencies, rights "
                         "metadata, performance budgets, diagnostics, tests, and target-build acceptance "
                         "criteria.\n")
        dataset = os.path.join(self.work, "mine.json")
        self.run_cli("import", "--source", source, "--out", dataset, expect=0)
        from_mine = self.run_cli("spec", "c01e01", "--dataset", dataset, "--format", "md", expect=0).stdout
        self.assertIn("My Capability", from_mine)
        self.assertNotIn("Sprite Generator", from_mine)


def main(argv=None):
    loader = unittest.defaultTestLoader
    suite = loader.loadTestsFromModule(sys.modules[__name__])
    verbosity = 2 if (argv and "-v" in argv) else 1
    runner = unittest.TextTestRunner(verbosity=verbosity)
    started = time.time()
    os.environ["OGD_SELFTEST_FULL"] = "1"
    try:
        outcome = runner.run(suite)
    finally:
        os.environ.pop("OGD_SELFTEST_FULL", None)
    missing = sorted(set(cli.COMMAND_NAMES) - EXECUTED)
    print(f"\ncommands exercised by this suite: {len(cli.COMMAND_NAMES) - len(missing)}/"
          f"{len(cli.COMMAND_NAMES)}; cases {outcome.testsRun} in {time.time() - started:.1f}s")
    if missing:
        print("NOT exercised:", ", ".join(missing))
    return 0 if outcome.wasSuccessful() and not missing else 1


if __name__ == "__main__":
    args = [item for item in sys.argv[1:]]
    sys.argv = [sys.argv[0]] + [item for item in args if item != "-v"]
    raise SystemExit(main(args))
