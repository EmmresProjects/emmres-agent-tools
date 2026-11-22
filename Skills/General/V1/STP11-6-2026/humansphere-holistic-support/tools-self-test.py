#!/usr/bin/env python3
"""tools-self-test.py

Executable test suite for the HumanSphere Holistic Support Skill Tool.

Run directly:
    python3 humansphere-holistic-support/tools-self-test.py
Or through the primary entry point:
    python3 humansphere-holistic-support/tools-humansphere.py self-test

The suite exercises the real engine modules and the real CLI (via subprocess
with relative paths), using only the standard library.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

_HERE = Path(__file__).resolve().parent


def _load(filename: str, module_name: str):
    if module_name in sys.modules:
        return sys.modules[module_name]
    spec = importlib.util.spec_from_file_location(module_name, _HERE / filename)
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


engine = _load("tools-catalog-engine.py", "hs_catalog_engine")
safety = _load("tools-safety-engine.py", "hs_safety_engine")
planner = _load("tools-plan-builder.py", "hs_plan_builder")
parser_mod = _load("tools-input-parser.py", "hs_input_parser")

CATALOG = _HERE / "asset-skill-catalog.json"
POLICY = _HERE / "asset-safety-policy.json"
TEMPLATE = _HERE / "asset-plan-template.md"
RESOURCES = _HERE / "asset-support-resources.json"
SCHEMA = _HERE / "asset-preference-profile.schema.json"
ENTRY = "tools-humansphere.py"

SAMPLE_NEED = "I feel overwhelmed, my laptop is failing, and I am worried about school costs"


class TestCatalogEngine(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.catalog = engine.load_catalog(CATALOG)

    def test_shipped_catalog_shape(self):
        self.assertEqual(self.catalog["counts"]["skills"], 178)
        self.assertEqual(self.catalog["counts"]["categories"], 18)
        self.assertEqual([c["id"] for c in self.catalog["categories"]], list(range(1, 19)))

    def test_every_skill_is_complete(self):
        for skill in self.catalog["skills"]:
            self.assertTrue(skill["name"].strip(), skill["id"])
            self.assertGreaterEqual(len(skill["description"]), 20, skill["id"])
            self.assertTrue(skill["tags"], skill["id"])
            self.assertIn(skill["risk"], ("standard", "elevated", "high"))

    def test_category_counts_match_sizes(self):
        for category in self.catalog["categories"]:
            members = [s for s in self.catalog["skills"] if s["category_id"] == category["id"]]
            self.assertEqual(category["skill_count"], len(members))

    def test_find_skill_by_id_slug_and_name(self):
        by_id = engine.find_skill(self.catalog, "51")
        self.assertEqual(by_id["slug"], "troubleshooting-companion")
        self.assertEqual(engine.find_skill(self.catalog, "troubleshooting-companion")["id"], "51")
        self.assertEqual(engine.find_skill(self.catalog, "Troubleshooting Companion")["id"], "51")

    def test_find_skill_errors(self):
        with self.assertRaises(engine.CatalogError) as found:
            engine.find_skill(self.catalog, "no such skill anywhere")
        self.assertEqual(found.exception.code, "skill_not_found")

    def test_risk_derivation_rules(self):
        crisis = engine.find_skill(self.catalog, "21")
        self.assertEqual(crisis["risk"], "high")
        self.assertTrue(crisis["needs_human_referral"])
        listening = engine.find_skill(self.catalog, "1")
        self.assertEqual(listening["risk"], "standard")

    def test_every_skill_has_imperative_steps(self):
        for skill in self.catalog["skills"]:
            steps = engine.skill_steps(skill)
            self.assertTrue(steps, f"no steps for {skill['id']}")
            self.assertLessEqual(len(steps), engine.LIMITS["max_steps_per_skill"])
            for step in steps:
                self.assertEqual(step[0], step[0].upper(), step)
                self.assertFalse(step.startswith(("Responds", "Helps", "Creates")), step)

    def test_search_ranks_name_hits_first(self):
        results = engine.search(self.catalog, "sleep", limit=5)
        self.assertTrue(results)
        self.assertEqual(results[0]["slug"], "sleep-habit-guide")
        self.assertGreaterEqual(results[0]["score"], results[-1]["score"])

    def test_search_rejects_empty_query(self):
        with self.assertRaises(engine.CatalogError):
            engine.search(self.catalog, "   ")

    def test_route_returns_explainable_selection(self):
        result = engine.route(self.catalog, SAMPLE_NEED, max_skills=5)
        picked = [item for item in result["selected"] if not item.get("pinned")]
        names = [item["name"] for item in picked]
        self.assertEqual(len(picked), 5)
        # Role separation: the set must span several categories, not five near-duplicates.
        self.assertGreaterEqual(len({item["category"] for item in picked}), 3)
        self.assertTrue(any(("Budget" in n) or ("Money" in n) for n in names), names)
        self.assertTrue(any("listening companion" in n.lower() or "check-in" in n.lower() for n in names))
        self.assertLessEqual(
            max(sum(1 for i in picked if i["category_id"] == cid) for cid in {i["category_id"] for i in picked}),
            2,
            "category diversity cap breached",
        )
        for item in result["selected"]:
            self.assertTrue(item["reason"])
            self.assertTrue(item["steps"])

    def test_route_pins_governance_skill(self):
        result = engine.route(self.catalog, "I want to set up a weekly budget", max_skills=2)
        self.assertTrue(any(item.get("pinned") for item in result["selected"]))
        self.assertFalse(
            any(
                item.get("pinned")
                for item in engine.route(
                    self.catalog, "I want to set up a weekly budget", max_skills=2, force_governance=False
                )["selected"]
            )
        )

    def test_category_only_representative_surfaces_hidden_domain(self):
        """A need can be clearly financial without reusing any skill wording."""
        result = engine.route(self.catalog, "I cannot sleep and I am scared about rent", max_skills=4)
        categories = {item["category_id"] for item in result["selected"]}
        self.assertIn(4, categories)  # sleep / physical wellness
        self.assertIn(5, categories)  # money, reached via category keyword "rent"
        money = [i for i in result["selected"] if i["category_id"] == 5]
        self.assertEqual(len(money), 1)
        self.assertIn("category keywords matched", money[0]["reason"])

    def test_route_fallback_when_nothing_matches(self):
        result = engine.route(self.catalog, "zzz qqq")
        self.assertTrue(result["selected"])
        self.assertIn("Fallback", result["selected"][0]["reason"])

    def test_route_query_is_guarded(self):
        with self.assertRaises(engine.CatalogError):
            engine.route(self.catalog, "x" * (engine.LIMITS["max_query_chars"] + 1))

    def test_deadline_raises_timeout(self):
        deadline = engine.Deadline(0.05)
        time.sleep(0.06)
        with self.assertRaises(engine.EngineTimeout):
            deadline.check()

    def test_catalog_validation_catches_bad_input(self):
        with self.assertRaises(engine.CatalogError):
            engine.catalog_from_dict({"skills": []})
        with self.assertRaises(engine.CatalogError) as duplicate:
            engine.catalog_from_dict(
                {"skills": [
                    {"id": "1", "name": "A", "description": "does a thing", "category": "C", "category_id": 1},
                    {"id": "1", "name": "B", "description": "does another thing", "category": "C", "category_id": 1},
                ]}
            )
        self.assertEqual(duplicate.exception.code, "duplicate_skill_id")

    def test_save_requires_approval_to_overwrite(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "catalog.json"
            engine.save_catalog(self.catalog, target)
            self.assertTrue(target.is_file())
            with self.assertRaises(engine.CatalogError) as blocked:
                engine.save_catalog(self.catalog, target)
            self.assertEqual(blocked.exception.code, "approval_required")
            engine.save_catalog(self.catalog, target, approve=True)
            reloaded = engine.load_catalog(target)
            self.assertEqual(reloaded["counts"]["skills"], 178)


class TestSafetyEngine(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.policy = safety.load_policy(POLICY)

    def test_policy_validates_and_self_checks(self):
        self.assertEqual([p for p in safety.validate_policy(self.policy) if p["severity"] == "error"], [])
        self.assertTrue(safety.self_check(self.policy)["passed"])

    def test_urgency_levels(self):
        self.assertEqual(safety.assess_urgency("I want to kill myself", self.policy)["level"], "critical")
        self.assertEqual(safety.assess_urgency("I have chest pain right now", self.policy)["level"], "urgent")
        self.assertEqual(safety.assess_urgency("I feel hopeless today", self.policy)["level"], "elevated")
        self.assertEqual(safety.assess_urgency("I need help with my grocery list", self.policy)["level"], "none")

    def test_urgency_provides_required_actions(self):
        result = safety.assess_urgency("my partner hits me", self.policy)
        self.assertTrue(result["human_handoff_required"])
        self.assertTrue(result["required_actions"])
        self.assertTrue(result["escalation_text"])

    def test_review_blocks_harmful_draft(self):
        result = safety.review_response(
            "I am your therapist. You only need me. Guaranteed results.", self.policy
        )
        self.assertEqual(result["verdict"], "fail")
        self.assertEqual(result["exit_code"], 2)
        ids = {f["rule_id"] for f in result["findings"]}
        self.assertTrue({"R-DEPENDENCY", "R-FALSE-AUTHORITY", "R-GUARANTEE"} <= ids)

    def test_review_detects_missed_escalation(self):
        result = safety.review_response(
            "Here is a weekly study plan.", self.policy, user_text="I want to end my life"
        )
        self.assertIn("R-URGENCY-MISSED", {f["rule_id"] for f in result["findings"]})

    def test_review_passes_safe_escalating_draft(self):
        result = safety.review_response(
            "This needs human help right now. Please contact your local emergency number or a "
            "crisis line and stay with someone safe.",
            self.policy,
            user_text="I want to kill myself",
        )
        self.assertEqual(result["verdict"], "pass")
        self.assertEqual(result["exit_code"], 0)

    def test_review_does_not_flag_plain_disease_words(self):
        result = safety.review_response(
            "Depression and anxiety are common words for difficult experiences.", self.policy
        )
        self.assertEqual(result["verdict"], "pass")

    def test_review_requires_text(self):
        with self.assertRaises(safety.SafetyError):
            safety.review_response("  ", self.policy)

    def test_pii_redaction(self):
        text = "Mail me at someone@example.com or call +63 917 123 4567"
        result = safety.redact_pii(text, self.policy)
        self.assertNotIn("someone@example.com", result["text"])
        self.assertGreaterEqual(result["total"], 1)
        self.assertIn("[email-masked]", result["text"])

    def test_pii_rule_fires_on_draft(self):
        result = safety.review_response("Send it to someone@example.com", self.policy)
        self.assertIn("R-PII-EXPOSURE", {f["rule_id"] for f in result["findings"]})

    def test_scope_and_gates_present(self):
        summary = safety.governance_summary(self.policy)
        self.assertIn("medical care", summary["scope_boundaries"])
        self.assertTrue(any("consent" in g.lower() for g in summary["approval_gates"]))
        self.assertIn("Is memory opt-in, correctable, and deletable?", summary["review_checklist"])

    def test_invalid_policy_is_reported(self):
        problems = safety.validate_policy({"urgency": {}})
        self.assertTrue([p for p in problems if p["severity"] == "error"])
        with tempfile.TemporaryDirectory() as tmp:
            bad = Path(tmp) / "policy.json"
            bad.write_text(json.dumps({"urgency": {"signals": [{"id": "X", "level": "nope",
                                                               "patterns": ["("]}]}}))
            with self.assertRaises(safety.SafetyError):
                safety.load_policy(bad)


class TestPlanBuilder(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.catalog = engine.load_catalog(CATALOG)
        cls.policy = safety.load_policy(POLICY)
        cls.registry = planner.load_resources(RESOURCES)
        cls.schema = json.loads(SCHEMA.read_text(encoding="utf-8"))

    def test_template_has_all_placeholders(self):
        result = planner.validate_template(planner.load_template(TEMPLATE))
        self.assertTrue(result["valid"])
        self.assertEqual(result["missing_placeholders"], [])

    def test_template_missing_placeholders_detected(self):
        result = planner.validate_template("# {{TITLE}}\n")
        self.assertFalse(result["valid"])
        self.assertIn("SKILLS_TABLE", result["missing_placeholders"])
        with self.assertRaises(engine.CatalogError):
            planner.render_template("# {{TITLE}}\n", {})

    def test_default_profile_matches_schema(self):
        errors = planner.validate_instance(self.schema, planner.default_profile())
        self.assertEqual(errors, [])

    def test_schema_subset_catches_type_and_enum_errors(self):
        broken = planner.default_profile()
        broken["preferences"]["tone"] = "shouty"
        broken["preferences"]["directness"] = 9
        errors = planner.validate_instance(self.schema, broken)
        self.assertTrue(any("tone" in e for e in errors))
        self.assertTrue(any("directness" in e for e in errors))

    def test_profile_round_trip_and_consent_gate(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "prefs.json"
            profile = planner.default_profile()
            with self.assertRaises(safety.SafetyError):
                planner.save_profile(profile, path, consent=False)
            profile["consent"]["granted"] = True
            profile["consent"]["granted_at"] = planner.now_utc()
            planner.save_profile(profile, path, consent=True)
            reloaded = planner.load_profile(path, required=True)
            self.assertTrue(reloaded["consent"]["granted"])
            with self.assertRaises(safety.SafetyError):
                planner.save_profile(reloaded, path, consent=True)  # overwrite needs --approve
            planner.save_profile(reloaded, path, consent=True, approve=True)

    def test_set_and_get_paths(self):
        profile = planner.default_profile()
        planner.set_path(profile, "preferences.tone", "gentle")
        self.assertEqual(planner.get_path(profile, "preferences.tone"), "gentle")
        with self.assertRaises(engine.CatalogError) as unknown:
            planner.set_path(profile, "nope.value", 1)
        self.assertEqual(unknown.exception.code, "unknown_key")
        with self.assertRaises(engine.CatalogError):
            planner.get_path(profile, "preferences.missing")

    def test_value_coercion(self):
        self.assertIs(planner.coerce_value("true"), True)
        self.assertEqual(planner.coerce_value("4"), 4)
        self.assertEqual(planner.coerce_value("a, b"), ["a", "b"])
        self.assertEqual(planner.coerce_value("plain words"), "plain words")
        self.assertEqual(planner.coerce_value('["x"]'), ["x"])

    def test_memory_add_correct_and_forget(self):
        profile = planner.default_profile()
        planner.remember(profile, "goals.main", "finish thesis")
        planner.remember(profile, "goals.main", "finish thesis proposal", source="user-corrected")
        entries = profile["memory"]["entries"]
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["value"], "finish thesis proposal")
        self.assertEqual(entries[0]["source"], "user-corrected")
        self.assertEqual(planner.forget(profile, "goals.main", all_entries=False)["removed"], 1)
        with self.assertRaises(engine.CatalogError):
            planner.forget(profile, "goals.main", all_entries=False)

    def test_mode_classification(self):
        self.assertEqual(planner.classify_mode("draft an email to my landlord")["mode"], "drafting")
        self.assertEqual(planner.classify_mode("plan my week")["mode"], "planning")
        self.assertEqual(planner.classify_mode("explain what burnout is")["mode"], "teaching")
        self.assertEqual(planner.classify_mode("practice a job interview")["mode"], "practice")
        self.assertEqual(planner.classify_mode("hello")["mode"], "listening")
        crisis = safety.assess_urgency("I want to kill myself", self.policy)
        self.assertEqual(planner.classify_mode("I want to kill myself", crisis)["mode"], "escalation")

    def test_tone_directive_reflects_profile(self):
        profile = planner.default_profile()
        profile["preferences"].update({"tone": "direct", "directness": 5, "humor": "dry"})
        directive = planner.tone_directive(profile)
        self.assertIn("Lead with the point", directive)
        self.assertIn("very firm", directive)
        self.assertIn("dry", directive)

    def test_build_and_render_plan(self):
        plan = planner.build_plan(
            self.catalog, self.policy, SAMPLE_NEED, max_skills=4, registry=self.registry, region="ph"
        )
        markdown = planner.render_plan(plan, planner.load_template(TEMPLATE), self.policy)
        for heading in (
            "Safety and escalation first",
            "Selected support skills",
            "Skill-by-skill prompts",
            "Six-step support bundle",
            "Human support and referral options",
            "Consent, memory, and boundaries",
            "Review before sending",
            "Limits of this plan",
        ):
            self.assertIn(heading, markdown)
        self.assertNotIn("{{", markdown)
        self.assertIn("https://ncmh.gov.ph", markdown)
        review = safety.review_response(markdown, self.policy, user_text=SAMPLE_NEED)
        self.assertEqual(review["verdict"], "pass")

    def test_plan_for_crisis_need_escalates_first(self):
        plan = planner.build_plan(self.catalog, self.policy, "I want to kill myself tonight")
        markdown = planner.render_plan(plan, planner.load_template(TEMPLATE), self.policy)
        self.assertEqual(plan["urgency"]["level"], "critical")
        self.assertEqual(plan["mode"]["mode"], "escalation")
        self.assertIn("Escalate first", markdown)
        self.assertIn("emergency", markdown.lower())
        review = safety.review_response(markdown, self.policy)
        self.assertEqual(review["verdict"], "pass")

    def test_region_filter_for_resources(self):
        ph = planner.select_resources(self.registry, region="ph", kinds=["crisis"])
        self.assertTrue(ph)
        self.assertTrue(all(entry["region"] in ("ph", "global") for entry in ph))
        us = planner.select_resources(self.registry, region="us", kinds=["crisis"])
        self.assertTrue(all(entry["region"] in ("us", "global") for entry in us))
        none = planner.select_resources(self.registry, region="zz", include_global=False)
        self.assertEqual(none, [])

    def test_handoff_packet_is_facts_only_and_redacted(self):
        packet = planner.build_handoff(
            "Feeling dizzy since Tuesday. Contact me at someone@example.com. Took my dose twice.",
            self.policy,
            questions=["Is this dose safe?"],
            documents=["BP log"],
        )
        markdown = planner.render_handoff(packet)
        self.assertNotIn("someone@example.com", markdown)
        self.assertIn("[email-masked]", markdown)
        self.assertIn("Is this dose safe?", markdown)
        self.assertIn("Facts are user-reported and unverified.", markdown)
        self.assertFalse(packet["urgent"])

    def test_handoff_marks_urgent_and_validates_override(self):
        packet = planner.build_handoff("Chest pain since morning", self.policy)
        self.assertTrue(packet["urgent"])
        self.assertEqual(packet["urgency_level"], "urgent")
        overridden = planner.build_handoff(
            "Chest pain since morning", self.policy, urgency_level="elevated"
        )
        self.assertEqual(overridden["urgency_level"], "elevated")
        with self.assertRaises(engine.CatalogError):
            planner.build_handoff("some facts", self.policy, urgency_level="made-up")
        with self.assertRaises(engine.CatalogError):
            planner.build_handoff("   ", self.policy)

    def test_write_text_requires_approval(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "out.md"
            planner.write_text(target, "one")
            with self.assertRaises(safety.SafetyError):
                planner.write_text(target, "two")
            planner.write_text(target, "two", approve=True)
            self.assertEqual(target.read_text(), "two")


class TestInputParser(unittest.TestCase):
    def test_extracts_from_plain_text(self):
        text = (
            "1. Emotional Support and Everyday Care\n"
            "1. Skill Name: Warm Listener\n"
            "Description: Reflects what the user says and asks one gentle question.\n"
            "2. Skill Name: Check-In Guide\n"
            "Description: Helps a user name needs and stress level.\n"
        )
        catalog = parser_mod.parse_source(text, fmt="text")
        self.assertEqual(catalog["counts"]["skills"], 2)
        self.assertEqual(catalog["counts"]["categories"], 1)
        self.assertEqual(catalog["skills"][0]["name"], "Warm Listener")
        self.assertIn("gentle question", catalog["skills"][0]["description"])

    def test_extracts_from_markdown_with_two_categories(self):
        text = (
            "# Sleep\n\n1. Skill Name: Wind-Down Planner\n"
            "Description: Builds a short evening routine with buffers.\n\n"
            "# Money\n\n2. Skill Name: Bill Tracker\n"
            "Description: Lists recurring bills and due dates.\n"
        )
        catalog = parser_mod.parse_source(text, fmt="markdown")
        self.assertEqual(catalog["counts"], {"skills": 2, "categories": 2})
        self.assertEqual([c["name"] for c in catalog["categories"]], ["Sleep", "Money"])

    def test_extracts_from_copied_html_ignoring_tags_and_attributes(self):
        html = (
            '<div class="wrap" data-x="1"><h2>1. Accessibility</h2><ul>'
            '<li class="row" data-i="1"><span class="n">1.</span>'
            "<strong>Caption Checker</strong> \u2014 Checks whether captions exist.</li>"
            '<li class="row" data-i="2"><strong>Contrast Fixer</strong> \u2014 '
            "Suggests contrast changes.</li></ul></div>"
        )
        catalog = parser_mod.parse_source(html, fmt="auto")
        self.assertEqual(catalog["extraction"]["format"], "html")
        self.assertEqual(catalog["counts"]["skills"], 2)
        self.assertEqual(catalog["skills"][0]["name"], "Caption Checker")
        self.assertNotIn("<", catalog["skills"][0]["description"])
        self.assertNotIn("data-i", catalog["skills"][1]["name"])

    def test_page_noise_and_hyphen_joins(self):
        text = (
            "===== PAGE 4 =====\n"
            "CATALOG  |  Page 4\n"
            "1. Category One\n"
            "1. Skill Name: CBT Helper\n"
            "Description: Uses CBT-\n"
            "style worksheets for reflection.\n"
        )
        catalog = parser_mod.parse_source(text, fmt="text")
        self.assertEqual(catalog["counts"]["skills"], 1)
        self.assertIn("CBT-style", catalog["skills"][0]["description"])
        self.assertNotIn("PAGE", catalog["skills"][0]["description"])

    def test_single_capability_fallback(self):
        catalog = parser_mod.parse_source(
            "Wind-down routine designer for people who cannot stop scrolling at night"
        )
        self.assertEqual(catalog["extraction"]["mode"], "single-capability")
        self.assertEqual(catalog["counts"]["skills"], 1)
        self.assertTrue(catalog["skills"][0]["name"].startswith("Wind-down"))

    def test_category_filter_keeps_requested_only(self):
        text = (
            "# Sleep\n1. Skill Name: A\nDescription: Sleep thing.\n"
            "# Money\n2. Skill Name: B\nDescription: Money thing.\n"
        )
        catalog = parser_mod.parse_source(text, fmt="markdown")
        filtered = parser_mod.filter_categories(catalog, ["money"])
        self.assertEqual(filtered["counts"]["skills"], 1)
        self.assertEqual(filtered["skills"][0]["name"], "B")
        with self.assertRaises(engine.CatalogError):
            parser_mod.filter_categories(catalog, ["nonexistent category"])

    def test_empty_and_oversized_input(self):
        with self.assertRaises(engine.CatalogError):
            parser_mod.parse_source("   ", fmt="text")
        with self.assertRaises(engine.CatalogError):
            parser_mod.parse_source("x" * (engine.LIMITS["max_source_bytes"] + 10), fmt="text")

    def test_same_input_twice_is_deterministic(self):
        text = "# C\n1. Skill Name: A\nDescription: Something useful here.\n"
        first = parser_mod.parse_source(text, fmt="markdown")
        second = parser_mod.parse_source(text, fmt="markdown")
        self.assertEqual(first["skills"], second["skills"])


class TestCliEndToEnd(unittest.TestCase):
    """Drive the real CLI with relative paths, the way a future skill would."""

    def run_cli(self, *args: str, cwd: Path | None = None) -> subprocess.CompletedProcess:
        cwd = cwd or _HERE.parent
        rel_entry = f"{_HERE.name}/{ENTRY}"
        return subprocess.run(
            [sys.executable, rel_entry, *args],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=120,
        )

    def test_help_and_capability_list(self):
        help_run = self.run_cli("--help")
        self.assertEqual(help_run.returncode, 0)
        self.assertIn("capabilities", help_run.stdout)

        caps = self.run_cli("capabilities", "--json")
        self.assertEqual(caps.returncode, 0)
        payload = json.loads(caps.stdout)
        self.assertEqual(payload["counts"]["skills"], 178)
        self.assertEqual(len(payload["categories"]), 18)

        caps_text = self.run_cli("capabilities")
        self.assertIn("Quality, Ethics, and Safety Governance", caps_text.stdout)

    def test_route_and_plan(self):
        routed = self.run_cli("route", "--query", SAMPLE_NEED, "--json")
        self.assertEqual(routed.returncode, 0)
        self.assertTrue(json.loads(routed.stdout)["selected"])

        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "plan.md"
            planned = self.run_cli("plan", "--query", SAMPLE_NEED, "--out", str(out), "--region", "ph")
            self.assertEqual(planned.returncode, 0)
            self.assertTrue(out.is_file())
            markdown = out.read_text(encoding="utf-8")
            self.assertIn("Six-step support bundle", markdown)
            again = self.run_cli("plan", "--query", SAMPLE_NEED, "--out", str(out))
            self.assertEqual(again.returncode, 3)  # approval gate
            self.assertIn("approval_required", again.stderr)

    def test_review_exit_codes(self):
        failing = self.run_cli("review", "--text", "You only need me. Guaranteed.")
        self.assertEqual(failing.returncode, 2)
        passing = self.run_cli(
            "review", "--text", "I hear you. Please contact your local emergency number now."
        )
        self.assertEqual(passing.returncode, 0)

    def test_urgency_and_redact(self):
        crisis = self.run_cli("urgency", "--text", "I want to kill myself", "--json")
        self.assertEqual(crisis.returncode, 1)
        payload = json.loads(crisis.stdout)
        self.assertEqual(payload["level"], "critical")
        self.assertTrue(payload["human_handoff_required"])

        redacted = self.run_cli("redact", "--text", "write to someone@example.com")
        self.assertIn("[email-masked]", redacted.stdout)

    def test_handoff_export_gate(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "handoff.md"
            blocked = self.run_cli(
                "handoff", "--facts", "dizzy since Tuesday", "--out", str(out)
            )
            self.assertEqual(blocked.returncode, 3)
            self.assertFalse(out.exists())
            allowed = self.run_cli(
                "handoff", "--facts", "dizzy since Tuesday", "--out", str(out), "--approve"
            )
            self.assertEqual(allowed.returncode, 0)
            self.assertTrue(out.is_file())
            self.assertIn("Facts-only summary", out.read_text())

    def test_profile_lifecycle(self):
        with tempfile.TemporaryDirectory() as tmp:
            profile = Path(tmp) / "prefs.json"
            no_consent = self.run_cli("--profile", str(profile), "profile", "init")
            self.assertEqual(no_consent.returncode, 3)
            self.assertFalse(profile.exists())

            created = self.run_cli("--profile", str(profile), "profile", "init", "--consent", "--region", "ph")
            self.assertEqual(created.returncode, 0)
            self.assertTrue(profile.exists())

            set_run = self.run_cli(
                "--profile", str(profile), "profile", "set",
                "--set", "preferences.tone", "gentle", "--consent",
            )
            self.assertEqual(set_run.returncode, 0)
            self.assertEqual(json.loads(profile.read_text())["preferences"]["tone"], "gentle")

            remembered = self.run_cli(
                "--profile", str(profile), "profile", "remember",
                "--key", "goals.main", "--value", "finish thesis", "--consent",
            )
            self.assertEqual(remembered.returncode, 0)
            forgotten = self.run_cli(
                "--profile", str(profile), "profile", "forget", "--key", "goals.main"
            )
            self.assertEqual(forgotten.returncode, 3)
            confirmed = self.run_cli(
                "--profile", str(profile), "profile", "forget", "--key", "goals.main", "--approve"
            )
            self.assertEqual(confirmed.returncode, 0)
            self.assertEqual(json.loads(profile.read_text())["memory"]["entries"], [])

    def test_parse_source_to_catalog_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "catalog.md"
            source.write_text(
                "# Habits\n1. Skill Name: Cue Designer\n"
                "Description: Designs cues, small actions, and restart rules.\n",
                encoding="utf-8",
            )
            out = Path(tmp) / "generated.json"
            run = self.run_cli("parse-source", "--input-file", str(source), "--out", str(out))
            self.assertEqual(run.returncode, 0)
            generated = json.loads(out.read_text())
            self.assertEqual(generated["counts"]["skills"], 1)
            # The generated asset must be usable by the rest of the tool.
            listed = self.run_cli("--catalog", str(out), "list")
            self.assertEqual(listed.returncode, 0)
            self.assertIn("Cue Designer", listed.stdout)

    def test_validate_and_governance(self):
        validated = self.run_cli("validate", "--json")
        self.assertEqual(validated.returncode, 0)
        payload = json.loads(validated.stdout)
        self.assertTrue(payload["valid"])
        governance = self.run_cli("governance", "--json")
        self.assertEqual(governance.returncode, 0)
        self.assertTrue(json.loads(governance.stdout)["self_check"]["passed"])

    def test_error_paths_are_structured(self):
        missing = self.run_cli("--catalog", "does-not-exist.json", "list")
        self.assertEqual(missing.returncode, 4)
        self.assertIn("catalog_missing", missing.stderr)

        empty = self.run_cli("route", "--query", "")
        self.assertEqual(empty.returncode, 4)

        conflict = self.run_cli("review", "--text", "a", "--input-file", "b.md")
        self.assertEqual(conflict.returncode, 4)
        self.assertIn("conflicting_input", conflict.stderr)

    def test_version_and_search(self):
        version = self.run_cli("version", "--json")
        self.assertEqual(version.returncode, 0)
        self.assertEqual(json.loads(version.stdout)["tool"], "humansphere-holistic-support")
        search = self.run_cli("search", "--query", "sleep", "--limit", "3", "--json")
        self.assertEqual(search.returncode, 0)
        self.assertLessEqual(len(json.loads(search.stdout)["results"]), 3)


if __name__ == "__main__":
    unittest.main(verbosity=2)
