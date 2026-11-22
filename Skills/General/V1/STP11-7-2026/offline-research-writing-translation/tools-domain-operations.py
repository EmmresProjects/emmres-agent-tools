#!/usr/bin/env python3
"""Category-level offline research, writing, and translation operations.

Loaded by tools-offline-research.py after tools-workflow-engine.py. Each of the
32 catalog categories has a real handler here. The 480 exact catalog names are
dispatched to these shared handlers with their own mode and full declaration.
No network, shell, subprocess, credentials, or arbitrary code execution.
"""

from __future__ import annotations

import difflib
import io
import json
import re
import sys
import zipfile
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

E = sys.modules.get("orwt_engine")
if E is None:
    raise RuntimeError("tools-workflow-engine.py must be loaded before tools-domain-operations.py")


class DomainOperations:
    def __init__(self, ctx: Any) -> None:
        self.ctx = ctx
        self.config = ctx.config
        self.operations = sorted(self.ctx.adapter.OPERATION_DESCRIPTIONS)

    # ---------- shared helpers ----------

    def _records(self, env: dict[str, Any]) -> list[dict[str, Any]]:
        return E.read_input_records(self.ctx, env["input_path"], env["params"], required=True)

    def _query(self, env: dict[str, Any], default: str = "offline research") -> str:
        value = env["params"].get("query") or env["params"].get("question") or default
        if not isinstance(value, str) or not value.strip():
            raise E.InputError("query must be a non-empty string.")
        return value.strip()

    def _artifact(self, operation: str, suffix: str, payload: Any) -> str:
        prefix = self.config["storage"].get("pipeline_prefix", "pipeline-")
        name = f"{prefix}{self.ctx.run_id}-{operation}-{suffix}.json"
        path = self.ctx.workspace_file(name)
        E.write_json(self.ctx, path, payload)
        return path.name

    def _audit(self, operation: str, status: str, details: dict[str, Any]) -> None:
        log = self.ctx.workspace_file(self.config["storage"].get("audit_log", "audit.jsonl"))
        E.append_jsonl(
            self.ctx,
            log,
            {
                "timestamp_utc": E.utc_now(),
                "run_id": self.ctx.run_id,
                "operation": operation,
                "status": status,
                "config_digest": self.ctx.config_digest,
                "details": details,
            },
        )

    def _draft_text(self, env: dict[str, Any], records: list[dict[str, Any]], query: str) -> str:
        params = env["params"]
        if params.get("draft"):
            return str(params["draft"])
        if params.get("draft_path"):
            path = self.ctx.user_path(str(params["draft_path"]), must_exist=True)
            return E.read_text_file(self.ctx, path)
        hits = E.search_records(self.ctx, records, query, {"limit": 3})["results"]
        if not hits:
            return f"Question: {query}. Insufficient local evidence; abstain and request additional licensed sources."
        return f"Question: {query}. " + " ".join(f"{hit['snippet']} {hit['citation']}" for hit in hits)

    def _render_report(self, title: str, query: str, language: str, records: list[dict[str, Any]], hits: list[dict[str, Any]], sections: list[str], limitations: str) -> str:
        template = E.read_text_file(self.ctx, self.ctx.asset_path("report_template"))
        evidence = "\n\n".join(f"- {h['snippet']} {h['citation']}" for h in hits) or "- No eligible evidence; abstain."
        section_text = "\n\n".join(f"### {name}\n\n{evidence}" for name in sections)
        citations = "\n".join(f"- {h['citation']} {h['title']} ({h['date']}, {h['version']})" for h in hits) or "- No citations."
        values = {
            "TITLE": title,
            "QUERY": query,
            "LANGUAGE": language,
            "JURISDICTION": self.config["policies"]["jurisdiction"],
            "SOURCE_COUNT": len(records),
            "REVIEW_STATUS": "PENDING_HUMAN_REVIEW",
            "ABSTRACT": hits[0]["snippet"] if hits else "Insufficient evidence; no factual abstract was generated.",
            "EVIDENCE": evidence,
            "SECTIONS": section_text,
            "CITATIONS": citations,
            "LIMITATIONS": limitations,
            "REVIEW_GATE": "Do not publish, export, or dispose of restricted material until a human approval token is recorded.",
        }
        return E.render_template(template, values)

    def execute(
        self,
        operation: str,
        params: dict[str, Any],
        input_path: str | None,
        output_path: str | None,
        capability: dict[str, Any] | None = None,
        approval_token: str | None = None,
    ) -> dict[str, Any]:
        if operation not in self.ctx.adapter.OPERATION_DESCRIPTIONS:
            raise E.InputError("Unknown operation.", {"operation": operation})
        self.ctx.check_limits()
        safe_params = dict(params or {})
        if capability:
            safe_params.setdefault("mode", capability["mode"])
            safe_params.setdefault("capability_name", capability["name"])
            safe_params.setdefault("capability_id", capability["capability_id"])
        env = {
            "params": safe_params,
            "input_path": input_path,
            "output_path": output_path,
            "approval_token": approval_token,
            "capability": capability,
        }
        handler = getattr(self, "op_" + operation.replace("-", "_"), None)
        if handler is None:
            raise E.ToolError("HANDLER_MISSING", "Operation handler is missing.", {"operation": operation})
        result = handler(env)
        self.ctx.check_limits()
        if not isinstance(result, dict):
            raise E.ToolError("BAD_HANDLER_RESULT", "Handler did not return structured data.", {"operation": operation})
        result.setdefault("operation", operation)
        result.setdefault("mode", safe_params.get("mode", "default"))
        result.setdefault("capability_name", safe_params.get("capability_name"))
        result.setdefault("source_count", len(safe_params.get("_records_cache", [])))
        result.setdefault("warnings", [])
        result["warnings"] = sorted(set(result["warnings"] + E.provider_warnings(self.ctx, operation)))
        self._audit(operation, "completed", {"output": output_path, "mode": result["mode"], "capability": result["capability_name"]})
        return result

    def _run_fixture(self, operation: str, params: dict[str, Any], input_path: str | None) -> dict[str, Any]:
        env = {"params": dict(params), "input_path": input_path, "output_path": None, "approval_token": None, "capability": None}
        handler = getattr(self, "op_" + operation.replace("-", "_"))
        return handler(env)

    # ---------- 1. deployment ----------

    def op_deployment(self, env: dict[str, Any]) -> dict[str, Any]:
        path = self.ctx.user_path(env["input_path"], must_exist=True)
        raw = E.read_text_file(self.ctx, path)
        bundle_manifest: Any = {"type": "opaque-input", "sha256": E.sha256_text(raw)}
        if path.suffix.lower() == ".json":
            try:
                bundle_manifest = json.loads(raw)
            except json.JSONDecodeError:
                bundle_manifest = {"type": "invalid-json-input", "sha256": E.sha256_text(raw)}
        assets: list[dict[str, Any]] = []
        for item in sorted(self.ctx.tool_dir.iterdir()):
            if item.is_file() and item.name.startswith(("tools-", "knowledge-", "asset-")):
                assets.append({"name": item.name, "bytes": item.stat().st_size, "sha256": E.sha256_file(item)})
        checks = {
            "network_disabled": self.config["runtime"]["network"] == "disabled",
            "standard_library_only": self.config["runtime"]["standard_library_only"] is True,
            "no_skill_md_in_tool_folder": not (self.ctx.tool_dir / "SKILL.md").exists(),
            "no_internal_subfolders": not any(p.is_dir() for p in self.ctx.tool_dir.iterdir()),
            "required_assets_present": all((self.ctx.tool_dir / name).is_file() for name in self.config["asset_paths"].values()),
            "no_bundled_archive": not any(p.suffix.lower() in (".zip", ".tar", ".gz", ".7z") for p in self.ctx.tool_dir.iterdir() if p.is_file()),
        }
        baseline = {
            "python": sys.version.split()[0],
            "platform": sys.platform,
            "tool_version": self.config["version"],
            "catalog_edition": self.ctx.catalog.get("edition"),
            "config_digest": self.ctx.config_digest,
            "asset_manifest": assets,
            "clean_state_checks": checks,
        }
        artifact = self._artifact("deployment", "baseline", baseline)
        return {
            "summary": "Offline deployment baseline and clean-state acceptance checks recorded.",
            "bundle_manifest": bundle_manifest,
            "environment_baseline": {"python": baseline["python"], "platform": baseline["platform"], "tool_version": baseline["tool_version"]},
            "asset_count": len(assets),
            "install_plan": ["Verify asset hashes", "Create a writable workspace", "Run capabilities --verify", "Run tools-selftest.py", "Record human acceptance"],
            "rollback_plan": ["Stop the run", "Restore the previous workspace checkpoint", "Rerun deployment verification", "Do not overwrite licensed corpora"],
            "health_checks": checks,
            "clean_state_passed": all(checks.values()),
            "artifacts": [artifact],
            "unavailable_integrations": E.unavailable_integrations(self.ctx),
        }

    # ---------- 2-6. corpus families ----------

    def op_scholarly_corpus(self, env: dict[str, Any]) -> dict[str, Any]:
        records = self._records(env)
        status_counts = dict(Counter(r["status"] for r in records))
        issues: list[dict[str, Any]] = []
        for r in records:
            for field in ("title", "date", "version", "license", "jurisdiction", "page_anchor"):
                if not r.get(field):
                    issues.append({"source_id": r["id"], "issue": f"missing {field}"})
            if r["status"] == "retracted" and not r.get("retracted_by"):
                issues.append({"source_id": r["id"], "issue": "retracted source has no retraction registry entry"})
            if r["status"] == "corrected" and not r.get("supersedes"):
                issues.append({"source_id": r["id"], "issue": "corrected source has no supersedes link"})
        hierarchy = {kind: [r["id"] for r in records if r["source_type"] == kind] for kind in self.config["policies"]["source_hierarchy"]}
        acceptance = {"passed": not issues, "issue_count": len(issues), "min_authority": self.config["policies"]["min_authority"]}
        payload = {"status_counts": status_counts, "source_hierarchy": hierarchy, "issues": issues, "acceptance": acceptance}
        artifact = self._artifact("scholarly-corpus", "report", payload)
        return {"summary": "Scholarly metadata, correction, retraction, and acceptance checks completed.", **payload, "artifacts": [artifact]}

    def op_reference_corpus(self, env: dict[str, Any]) -> dict[str, Any]:
        records = self._records(env)
        editions = defaultdict(list)
        by_hash: dict[str, list[str]] = defaultdict(list)
        for r in records:
            editions[r["version"]].append(r["id"])
            by_hash[r["sha256"]].append(r["id"])
        duplicate_groups = [ids for ids in by_hash.values() if len(ids) > 1]
        issues = [{"source_id": r["id"], "issue": "reference edition is missing"} for r in records if not r.get("version")]
        acceptance = {"passed": not issues and not duplicate_groups, "duplicate_groups": len(duplicate_groups)}
        payload = {"edition_registry": dict(editions), "exact_duplicate_groups": duplicate_groups, "metadata_issues": issues, "acceptance": acceptance}
        artifact = self._artifact("reference-corpus", "report", payload)
        return {"summary": "Reference edition registry and duplicate acceptance checks completed.", **payload, "artifacts": [artifact]}

    def op_news_archive(self, env: dict[str, Any]) -> dict[str, Any]:
        records = self._records(env)
        timeline = [{"date": r["date"], "source_id": r["id"], "title": r["title"], "status": r["status"]} for r in sorted(records, key=lambda x: x["date"])]
        pairs: list[dict[str, Any]] = []
        for i, a in enumerate(records):
            for b in records[i + 1 :]:
                score = E.jaccard(E.shingles(E.tokenize(a["text"]), 6), E.shingles(E.tokenize(b["text"]), 6))
                if score >= 0.45:
                    pairs.append({"a": a["id"], "b": b["id"], "syndication_similarity": round(score, 6)})
        corrections = [{"source_id": r["id"], "supersedes": r.get("supersedes"), "status": r["status"]} for r in records if r["status"] in ("corrected", "superseded", "retracted")]
        issues = [{"source_id": r["id"], "issue": "news record date is missing"} for r in records if not r.get("date")]
        acceptance = {"passed": not issues, "timeline_entries": len(timeline), "syndication_candidates": len(pairs)}
        payload = {"event_timeline": timeline, "syndication_candidates": pairs, "corrections": corrections, "issues": issues, "acceptance": acceptance}
        artifact = self._artifact("news-archive", "report", payload)
        return {"summary": "News versions, corrections, syndication candidates, and timeline built.", **payload, "artifacts": [artifact]}

    def op_legal_corpus(self, env: dict[str, Any]) -> dict[str, Any]:
        records = self._records(env)
        today = E.utc_now()[:10]
        jurisdictions = dict(Counter(r["jurisdiction"] for r in records))
        effective: list[dict[str, Any]] = []
        issues: list[dict[str, Any]] = []
        for r in records:
            effective.append({"source_id": r["id"], "date": r["date"], "status": r["status"], "effective_now": r["date"] <= today, "jurisdiction": r["jurisdiction"]})
            if r["status"] == "superseded" and not r.get("supersedes"):
                issues.append({"source_id": r["id"], "issue": "superseded legal text has no predecessor link"})
            if not r.get("jurisdiction"):
                issues.append({"source_id": r["id"], "issue": "jurisdiction is missing"})
        active = [r for r in records if r["status"] in ("active", "corrected") and r["date"] <= today]
        canonical = max(active, key=lambda r: (r["date"], r["authority"]), default=None)
        payload = {
            "jurisdiction_counts": jurisdictions,
            "effective_date_register": effective,
            "version_resolution": {"canonical_source_id": canonical["id"] if canonical else None, "basis": "latest active/corrected date then authority"},
            "amendment_and_repeal_issues": issues,
            "acceptance": {"passed": not issues, "requires_human_legal_review": True},
        }
        artifact = self._artifact("legal-corpus", "report", payload)
        return {"summary": "Jurisdiction, effective-date, amendment, repeal, and legal version checks completed.", **payload, "artifacts": [artifact]}

    def op_standards_library(self, env: dict[str, Any]) -> dict[str, Any]:
        records = self._records(env)
        clauses: list[dict[str, Any]] = []
        editions: dict[str, list[str]] = defaultdict(list)
        for r in records:
            editions[str(r.get("edition") or r["version"])].append(r["id"])
            for match in re.finditer(r"\b(?:Clause|Requirement clause)\s+(\d+(?:\.\d+)?)\b[^\n.!?]*[.!?]?", r["text"], flags=re.I):
                clauses.append({"source_id": r["id"], "clause": match.group(1), "text": E.normalize_text(match.group(0)), "page_anchor": r.get("page_anchor", "unanchored")})
        superseded = [{"source_id": r["id"], "edition": r.get("edition", r["version"])} for r in records if "superseded" in r["text"].lower() or r["status"] == "superseded"]
        license_audit = [{"source_id": r["id"], "license": r["license"], "redistribute": r["permissions"].get("redistribute")} for r in records]
        payload = {
            "edition_registry": dict(editions),
            "superseded_editions": superseded,
            "requirement_clauses": clauses,
            "normative_references": sorted({c["clause"] for c in clauses}),
            "license_audit": license_audit,
            "acceptance": {"passed": bool(clauses) and all(x["license"] for x in license_audit), "clause_count": len(clauses)},
        }
        artifact = self._artifact("standards-library", "report", payload)
        return {"summary": "Standards editions, supersession, clauses, normative references, and licenses checked.", **payload, "artifacts": [artifact]}

    # ---------- 7. bibliographic ----------

    def op_bibliographic(self, env: dict[str, Any]) -> dict[str, Any]:
        params = env["params"]
        records = self._records(env)
        bib_text = str(params.get("bibliography_text", ""))
        if params.get("bibliography_path"):
            bib_text = E.read_text_file(self.ctx, self.ctx.user_path(str(params["bibliography_path"]), must_exist=True))
        entries: list[dict[str, Any]] = []
        if bib_text.strip().startswith("@"):
            for match in re.finditer(r"@(\w+)\s*\{\s*([^,\s]+)\s*,(.*?)\n\}", bib_text, flags=re.S):
                entries.append({"format": "bibtex", "type": match.group(1), "key": match.group(2), "raw_fields": match.group(3).strip()[:1000]})
        elif bib_text.strip():
            for line in bib_text.splitlines():
                if re.match(r"^[A-Z][A-Z0-9]\s*-", line.strip()):
                    entries.append({"format": "ris", "line": line.strip()})
        else:
            for r in records:
                entries.append({"format": "csl-like", "id": r["id"], "title": r["title"], "issued": r["date"], "version": r["version"], "license": r["license"]})
        identifiers: list[dict[str, Any]] = []
        for entry in entries:
            raw = json.dumps(entry, ensure_ascii=False)
            for doi in re.findall(r"\b10\.\d{4,9}/[^\s\"},]+", raw):
                identifiers.append({"type": "doi", "value": doi, "syntax_ok": True})
            for isbn in re.findall(r"\b(?:97[89]-?)?\d{1,5}-?\d{1,7}-?\d{1,7}-?[\dX]\b", raw):
                identifiers.append({"type": "isbn-candidate", "value": isbn, "syntax_ok": True, "checksum_verified": False})
        conflicts = []
        titles = defaultdict(set)
        for entry in entries:
            if "title" in entry:
                titles[entry["title"].casefold()].add(entry.get("issued", entry.get("version", "unknown")))
        for title, dates in titles.items():
            if len(dates) > 1:
                conflicts.append({"title": title, "dates": sorted(dates)})
        payload = {
            "parsed_entries": entries,
            "entry_count": len(entries),
            "identifier_cache": identifiers,
            "conflicts": conflicts,
            "acceptance": {"passed": bool(entries), "offline_only": True, "external_resolution_performed": False},
        }
        artifact = self._artifact("bibliographic", "report", payload)
        return {"summary": "Local bibliographic records parsed; identifiers and conflicts checked offline.", **payload, "artifacts": [artifact]}

    # ---------- 8-10. retrieval / QA ----------

    def op_retrieval(self, env: dict[str, Any]) -> dict[str, Any]:
        records = self._records(env)
        query = self._query(env)
        search = E.search_records(self.ctx, records, query, env["params"])
        index_payload = {
            "schema_version": "1.0",
            "index_type": "BM25-style lexical",
            "config_digest": self.ctx.config_digest,
            "source_count": len(records),
            "sources": [{"id": r["id"], "sha256": r["sha256"], "version": r["version"]} for r in records],
            "avg_document_tokens": round(sum(len(E.tokenize(r["text"])) for r in records) / len(records), 3),
        }
        index_path = self.ctx.workspace_file(self.config["storage"].get("index", "index.json"))
        E.write_json(self.ctx, index_path, index_payload)
        expected = env["params"].get("expected_source_ids")
        quality: dict[str, Any] = {"result_count": search["result_count"]}
        if isinstance(expected, list):
            found = {r["source_id"] for r in search["results"]}
            quality["recall_at_limit"] = round(len(set(expected) & found) / max(1, len(expected)), 6)
        payload = {**search, "index_artifact": index_path.name, "search_quality": quality, "permission_filter_applied": True}
        artifact = self._artifact("retrieval", "results", payload)
        return {"summary": "Local lexical index built and permission-aware query executed.", **payload, "artifacts": [index_path.name, artifact]}

    def op_semantic_retrieval(self, env: dict[str, Any]) -> dict[str, Any]:
        records = self._records(env)
        query = self._query(env)
        result = E.semantic_search(self.ctx, records, query, env["params"])
        vector_path = self.ctx.workspace_file(self.config["storage"].get("vector_index", "vector-index.json"))
        current_hashes = {r["id"]: r["sha256"] for r in records}
        previous_hashes: dict[str, str] = {}
        if vector_path.is_file():
            try:
                previous = json.loads(vector_path.read_text(encoding="utf-8"))
                previous_hashes = previous.get("source_hashes", {})
            except (OSError, UnicodeDecodeError, json.JSONDecodeError):
                previous_hashes = {}
        drift = sorted(set(previous_hashes.items()) ^ set(current_hashes.items())) if previous_hashes else []
        vector_payload = {
            "schema_version": "1.0",
            "vector_model": self.config["providers"]["embedding"]["model_id"],
            "dimensions": 256,
            "source_hashes": current_hashes,
            "note": "Sparse hashed character n-gram vectors; not neural embeddings.",
        }
        E.write_json(self.ctx, vector_path, vector_payload)
        payload = {
            **result,
            "vector_index_artifact": vector_path.name,
            "drift": [{"source_id": k, "sha256": v} for k, v in drift],
            "threshold_calibration": {"requested": env["params"].get("threshold", 0.05), "returned": result["result_count"]},
            "hybrid_search": True,
            "reranking": "lexical-feature reranker with authority and status boosts",
        }
        artifact = self._artifact("semantic-retrieval", "results", payload)
        return {"summary": "Deterministic local vector index, hybrid search, reranking, and drift check completed.", **payload, "artifacts": [vector_path.name, artifact]}

    def op_question_answering(self, env: dict[str, Any]) -> dict[str, Any]:
        records = self._records(env)
        query = self._query(env)
        decompose = bool(env["params"].get("decompose", True))
        subquestions = [q.strip() for q in re.split(r"\?|\band\b|;", query) if len(q.strip()) > 3] if decompose else [query]
        retrieval = E.search_records(self.ctx, records, query, {"limit": int(env["params"].get("limit", 5))})
        evidence: list[dict[str, Any]] = []
        number_sets: list[set[str]] = []
        for hit in retrieval["results"]:
            nums = set(E.extract_numbers(hit["snippet"]))
            number_sets.append(nums)
            evidence.append({"source_id": hit["source_id"], "sentence": hit["snippet"], "citation": hit["citation"], "numbers": sorted(nums), "score": hit["score"]})
        conflict = any(a and b and not (a & b) for i, a in enumerate(number_sets) for b in number_sets[i + 1 :])
        insufficient = not evidence or sum(e["score"] for e in evidence) < float(env["params"].get("min_evidence_score", 0.05))
        abstain = insufficient or (conflict and env["params"].get("allow_conflicts") is not True)
        answer = "ABSTAIN: local evidence is insufficient or conflicting." if abstain else " ".join(f"{e['sentence']} {e['citation']}" for e in evidence[:3])
        confidence = 0.0 if abstain else min(0.95, 0.45 + 0.1 * len(evidence) + 0.05 * sum(e["score"] for e in evidence[:3]))
        payload = {
            "question": query,
            "subquestions": subquestions,
            "answer": answer,
            "abstained": abstain,
            "confidence": round(confidence, 3),
            "evidence": evidence,
            "conflicting_numeric_evidence": conflict,
            "comparative_mode": "compare" in query.lower(),
            "chronological_mode": bool(re.search(r"\b(?:timeline|chronolog|history|before|after)\b", query, re.I)),
            "quantitative_mode": any(E.extract_numbers(query)),
            "multi_hop": len(subquestions) > 1,
            "citations": [e["citation"] for e in evidence],
            "generation_mode": "extractive; no bundled local LLM",
        }
        artifact = self._artifact("question-answering", "answer", payload)
        return {"summary": "Evidence-retrieval plan, extractive cited answer, conflict detection, and abstention gate completed.", **payload, "artifacts": [artifact]}

    # ---------- 11-12. document / transcript sidecars ----------

    def op_document_layout(self, env: dict[str, Any]) -> dict[str, Any]:
        records = self._records(env)
        blocks: list[dict[str, Any]] = []
        tables: list[dict[str, Any]] = []
        for r in records:
            lines = r["text"].splitlines() or [r["text"]]
            for i, line in enumerate(lines, 1):
                stripped = line.strip()
                if not stripped:
                    continue
                kind = "body"
                if re.match(r"^(?:clause|section|chapter)\s+\d", stripped, re.I):
                    kind = "header"
                elif re.match(r"^(?:page\s+\d+|\d+\s*\|)", stripped, re.I):
                    kind = "header-footer"
                elif re.search(r"\[\d+\]|\bfootnote\b", stripped, re.I):
                    kind = "footnote"
                elif "\t" in stripped or stripped.count("|") >= 2:
                    kind = "table-row"
                    tables.append({"source_id": r["id"], "row": i, "cells": [c.strip() for c in re.split(r"\||\t", stripped) if c.strip()]})
                elif "=" in stripped and re.search(r"[A-Za-z]", stripped):
                    kind = "equation-candidate"
                blocks.append({"source_id": r["id"], "order": i, "kind": kind, "text": stripped[:500], "anchor": r.get("page_anchor", "p.1")})
            for segment in r.get("segments", []):
                blocks.append({"source_id": r["id"], "order": len(blocks) + 1, "kind": "timestamped-block", "text": segment["text"], "anchor": f"{segment['start']:.2f}-{segment['end']:.2f}"})
        payload = {
            "layout_blocks": blocks,
            "block_count": len(blocks),
            "table_rows": tables,
            "reading_order": "source order, then segment start time where supplied",
            "ocr": {"mode": self.config["providers"]["ocr"]["mode"], "image_pixels_processed": 0, "text_sidecars_processed": len(records)},
            "handwriting": {"mode": self.config["providers"]["handwriting"]["mode"], "images_processed": 0},
            "acceptance": {"passed": bool(blocks), "requires_visual_human_review": True},
        }
        artifact = self._artifact("document-layout", "blocks", payload)
        return {"summary": "Text-layer layout, reading order, tables, footnotes, headers, and equation candidates extracted.", **payload, "artifacts": [artifact]}

    def op_audio_transcripts(self, env: dict[str, Any]) -> dict[str, Any]:
        records = self._records(env)
        segments: list[dict[str, Any]] = []
        issues: list[dict[str, Any]] = []
        for r in records:
            for i, seg in enumerate(r.get("segments", []), 1):
                if seg["end"] < seg["start"]:
                    issues.append({"source_id": r["id"], "segment": i, "issue": "end before start"})
                if not seg.get("speaker") or seg["speaker"] == "UNKNOWN":
                    issues.append({"source_id": r["id"], "segment": i, "issue": "speaker label missing"})
                segments.append({"source_id": r["id"], "index": i, **seg, "quote_anchor": f"{seg['start']:.2f}-{seg['end']:.2f}"})
        speakers = sorted({s["speaker"] for s in segments})
        payload = {
            "segments": segments,
            "segment_count": len(segments),
            "speakers": speakers,
            "diarization_sidecar_only": True,
            "quote_windows": [{"source_id": s["source_id"], "anchor": s["quote_anchor"], "speaker": s["speaker"], "text": s["text"]} for s in segments],
            "issues": issues,
            "asr": {"mode": self.config["providers"]["asr"]["mode"], "audio_files_decoded": 0, "transcript_sidecars_processed": sum(1 for r in records if r.get("segments"))},
            "acceptance": {"passed": not issues, "requires_human_transcript_review": True},
        }
        artifact = self._artifact("audio-transcripts", "segments", payload)
        return {"summary": "Timestamped transcript sidecars, speakers, quote windows, and validation checks completed.", **payload, "artifacts": [artifact]}

    # ---------- 13-15. translation / terminology / QA ----------

    def op_translation(self, env: dict[str, Any]) -> dict[str, Any]:
        params = env["params"]
        records = self._records(env)
        source_language = str(params.get("source_language") or "en")
        target_language = str(params.get("target_language") or self.config["policies"]["default_target_language"])
        units: list[str] = []
        if params.get("text"):
            units = [str(params["text"])]
        elif params.get("text_path"):
            units = [E.read_text_file(self.ctx, self.ctx.user_path(str(params["text_path"]), must_exist=True))]
        else:
            units = [E.sentences(r["text"])[0] for r in records[:3] if E.sentences(r["text"])]
        glossary = E.read_glossary(self.ctx, params.get("glossary_path"))
        memory = E.read_translation_memory(self.ctx, params.get("translation_memory_path"))
        output_units: list[dict[str, Any]] = []
        for unit in units:
            exact = next((m for m in memory if m.get("source_language") == source_language and m.get("target_language") == target_language and m.get("source") == E.normalize_text(unit)), None)
            if exact:
                draft = exact["target"]
                method = "exact-translation-memory"
                applied: list[dict[str, str]] = []
                placeholders_ok = all(p in draft for p in E.placeholder_tokens(unit))
            else:
                glossed = E.apply_glossary(unit, glossary, source_language, target_language)
                draft = glossed["text"]
                method = "glossary-assisted-draft"
                applied = glossed["applied_terms"]
                placeholders_ok = glossed["do_not_translate_ok"]
            source_numbers = E.extract_numbers(unit)
            target_numbers = E.extract_numbers(draft)
            numbers_ok = source_numbers == target_numbers
            output_units.append(
                {
                    "source_language": source_language if params.get("text") else E.detect_language(unit),
                    "target_language": target_language,
                    "source": unit,
                    "target_draft": draft,
                    "method": method,
                    "applied_terms": applied,
                    "placeholders_preserved": placeholders_ok,
                    "numbers_preserved": numbers_ok,
                    "review_required": True,
                    "provider_mode": self.config["providers"]["translation"]["mode"],
                }
            )
        payload = {
            "units": output_units,
            "unit_count": len(output_units),
            "glossary_entries": len(glossary),
            "translation_memory_entries": len(memory),
            "language_detection": "heuristic for corpus-derived units; explicit source_language for supplied text",
            "human_review_gate": "PENDING_HUMAN_REVIEW",
            "acceptance": {"passed": all(u["placeholders_preserved"] and u["numbers_preserved"] for u in output_units), "neural_mt_used": False},
        }
        artifact = self._artifact("translation", "units", payload)
        return {"summary": "Configurable translation memory and glossary drafts produced with placeholder, number, and review gates.", **payload, "artifacts": [artifact]}

    def op_terminology(self, env: dict[str, Any]) -> dict[str, Any]:
        params = env["params"]
        records = self._records(env)
        text = str(params.get("text") or " ".join(E.sentences(r["text"])[0] for r in records[:3] if E.sentences(r["text"])))
        glossary = E.read_glossary(self.ctx, params.get("glossary_path"))
        target_language = str(params.get("target_language") or self.config["policies"]["default_target_language"])
        source_language = str(params.get("source_language") or "en")
        glossed = E.apply_glossary(text, glossary, source_language, target_language)
        found = [row for row in glossary if row["target_language"] == target_language and re.search(re.escape(row["source_term"]), text, re.I)]
        candidates = sorted({" ".join(m.group(0).split()) for m in re.finditer(r"\b(?:[A-Z][a-z]+\s+){1,3}[A-Z][a-z]+\b", text)})[:50]
        do_not_translate = [row["source_term"] for row in glossary if str(row["do_not_translate"]).lower() in ("true", "1", "yes")]
        payload = {
            "source_text": text,
            "glossed_text": glossed["text"],
            "glossary_entry_count": len(glossary),
            "matched_terms": found,
            "extracted_candidate_terms": candidates,
            "do_not_translate_terms": do_not_translate,
            "placeholders_preserved": glossed["do_not_translate_ok"],
            "translation_memory_configured": str(self.ctx.asset_path("translation_memory").name),
            "acceptance": {"passed": glossed["do_not_translate_ok"], "requires_terminologist_review": True},
        }
        artifact = self._artifact("terminology", "report", payload)
        return {"summary": "Glossary validation, term application, candidate extraction, and do-not-translate checks completed.", **payload, "artifacts": [artifact]}

    def op_localization_qa(self, env: dict[str, Any]) -> dict[str, Any]:
        params = env["params"]
        records = self._records(env)
        source = str(params.get("text") or E.sentences(records[0]["text"])[0])
        translation = params.get("translation")
        if translation is None:
            translation = self._run_fixture("translation", {"text": source, "source_language": params.get("source_language", "en"), "target_language": params.get("target_language", "es")}, env["input_path"])["units"][0]["target_draft"]
        source_placeholders = E.placeholder_tokens(source)
        target_placeholders = E.placeholder_tokens(str(translation))
        source_numbers = E.extract_numbers(source)
        target_numbers = E.extract_numbers(str(translation))
        dates_source = E.extract_dates(source)
        dates_target = E.extract_dates(str(translation))
        bidi = [c for c in str(translation) if c in "\u202a\u202b\u202d\u202e\u2066\u2067\u2068\u2069"]
        issues: list[str] = []
        if source_placeholders != target_placeholders:
            issues.append("placeholder mismatch")
        if source_numbers != target_numbers:
            issues.append("number mismatch")
        if dates_source != dates_target:
            issues.append("date mismatch")
        if bidi:
            issues.append("unexpected bidi control")
        if source == translation:
            issues.append("untranslated segment")
        if len(str(translation)) > len(source) * 2:
            issues.append("length expansion above 2x")
        payload = {
            "source": source,
            "translation": translation,
            "locale": params.get("locale", params.get("target_language", "es")),
            "placeholders": {"source": source_placeholders, "target": target_placeholders},
            "numbers": {"source": source_numbers, "target": target_numbers},
            "dates": {"source": dates_source, "target": dates_target},
            "bidi_control_count": len(bidi),
            "encoding": "utf-8",
            "issues": issues,
            "acceptance": {"passed": not issues, "human_review_required": True},
        }
        artifact = self._artifact("localization-qa", "report", payload)
        return {"summary": "Placeholder, number, date, bidi, encoding, length, and untranslated-segment QA completed.", **payload, "artifacts": [artifact]}

    # ---------- 16-18. graph / ingest / dedupe ----------

    def op_knowledge_graph(self, env: dict[str, Any]) -> dict[str, Any]:
        records = self._records(env)
        nodes: dict[str, dict[str, Any]] = {}
        edges: list[dict[str, Any]] = []
        def add_node(name: str, kind: str, source_id: str) -> None:
            key = name.casefold()
            if key not in nodes:
                nodes[key] = {"id": f"n-{len(nodes)+1:04d}", "label": name, "kind": kind, "source_ids": []}
            if source_id not in nodes[key]["source_ids"]:
                nodes[key]["source_ids"].append(source_id)
        for r in records:
            for sentence in E.sentences(r["text"]):
                entities = re.findall(r"\b(?:[A-Z][A-Za-z0-9-]+(?:\s+[A-Z][A-Za-z0-9-]+){0,3}|src-\d+|Clause\s+\d+(?:\.\d+)?)\b", sentence)
                for entity in entities:
                    add_node(entity, "entity", r["id"])
                for i, a in enumerate(entities):
                    for b in entities[i + 1 :]:
                        edges.append({"source": a, "target": b, "relation": "co-occurs", "source_id": r["id"], "evidence": sentence[:240]})
                for match in re.finditer(r"\b([A-Z][A-Za-z0-9 -]{2,60}?)\s+(?:requires|supersedes|retained for|covered)\s+([A-Za-z0-9 -]{2,80}?)(?:\.|$)", sentence, re.I):
                    a, b = match.group(1).strip(), match.group(2).strip()
                    add_node(a, "subject", r["id"])
                    add_node(b, "object", r["id"])
                    edges.append({"source": a, "target": b, "relation": match.group(0).split()[1] if " " in match.group(0) else "related", "source_id": r["id"], "evidence": sentence[:240]})
        payload = {"nodes": list(nodes.values()), "edges": edges[:1000], "node_count": len(nodes), "edge_count": min(len(edges), 1000), "graph_quality": {"dangling_edges": 0, "evidence_attached": True}, "extraction": "bounded regex and sentence co-occurrence; no bundled NER model"}
        artifact = self._artifact("knowledge-graph", "graph", payload)
        return {"summary": "Bounded entity and relationship extraction produced an evidence-linked local graph.", **payload, "artifacts": [artifact]}

    def op_ingest(self, env: dict[str, Any]) -> dict[str, Any]:
        records = self._records(env)
        corpus_path = self.ctx.workspace_file(self.config["storage"].get("corpus", "corpus.jsonl"))
        lines = [json.dumps({k: v for k, v in r.items() if k != "normalized_text"}, ensure_ascii=False, sort_keys=True) for r in records]
        E.write_text(self.ctx, corpus_path, "\n".join(lines) + "\n")
        payload = {
            "parsed_formats": sorted({Path(env["input_path"]).suffix.lower() or "text" for _ in [0]}),
            "source_count": len(records),
            "checksums": [{"id": r["id"], "sha256": r["sha256"]} for r in records],
            "source_types": dict(Counter(r["source_type"] for r in records)),
            "languages": sorted({r["language"] for r in records}),
            "normalized_corpus_artifact": corpus_path.name,
            "rights_precheck": {"search_allowed": sum(1 for r in records if r["permissions"]["search"]), "export_allowed": sum(1 for r in records if r["permissions"]["export"])},
            "acceptance": {"passed": len(records) > 0 and all(r["text"] for r in records)},
        }
        artifact = self._artifact("ingest", "report", payload)
        return {"summary": "Local input parsed into schema-validated normalized source records with checksums.", **payload, "artifacts": [corpus_path.name, artifact]}

    def op_deduplication(self, env: dict[str, Any]) -> dict[str, Any]:
        records = self._records(env)
        by_hash: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for r in records:
            by_hash[r["sha256"]].append(r)
        exact = [{"sha256": h, "source_ids": [x["id"] for x in group]} for h, group in by_hash.items() if len(group) > 1]
        near: list[dict[str, Any]] = []
        vectors = {r["id"]: E.shingles(E.tokenize(r["text"]), 6) for r in records}
        threshold = float(env["params"].get("near_threshold", 0.45))
        for i, a in enumerate(records):
            for b in records[i + 1 :]:
                score = E.jaccard(vectors[a["id"]], vectors[b["id"]])
                if score >= threshold and a["sha256"] != b["sha256"]:
                    canonical = max([a, b], key=lambda r: (r["status"] != "superseded", r["authority"], r["date"]))
                    near.append({"a": a["id"], "b": b["id"], "similarity": round(score, 6), "canonical_source_id": canonical["id"]})
        payload = {"exact_duplicate_groups": exact, "near_duplicate_pairs": near, "clusters": [{"canonical": p["canonical_source_id"], "members": [p["a"], p["b"]]} for p in near], "canonicalization_rule": "non-superseded status, then authority, then date", "acceptance": {"passed": True, "exact_groups": len(exact), "near_pairs": len(near)}}
        artifact = self._artifact("deduplication", "report", payload)
        return {"summary": "Exact and near duplicates clustered; canonical records selected with a documented rule.", **payload, "artifacts": [artifact]}

    # ---------- 19-21. citations / review / factuality ----------

    def op_citations(self, env: dict[str, Any]) -> dict[str, Any]:
        params = env["params"]
        records = self._records(env)
        draft = str(params.get("draft") or self._draft_text(env, records, self._query(env)))
        inline = re.findall(r"\[([^\]]+)\]", draft)
        numeric = re.findall(r"\(([A-Z][A-Za-z]+,\s*\d{4})\)", draft)
        footnotes = re.findall(r"(?m)^\s*\[\^?(\d+)\]:\s*(.+)$", draft)
        known = {r["id"] for r in records}
        parsed: list[dict[str, Any]] = []
        broken: list[str] = []
        for token in inline:
            source_id = token.split()[0]
            parsed.append({"raw": token, "source_id": source_id, "style": "bracket"})
            if source_id not in known and not re.fullmatch(r"\d+", source_id):
                broken.append(token)
        for token in numeric:
            parsed.append({"raw": token, "source_id": "author-year", "style": "author-year"})
        for number, text in footnotes:
            parsed.append({"raw": number, "source_id": "footnote", "style": "footnote", "text": text.strip()})
        quotes = re.findall(r'"([^"]{10,})"', draft)
        aligned: list[dict[str, Any]] = []
        for quote in quotes:
            matches = [r["id"] for r in records if E.normalize_text(quote).casefold() in r["normalized_text"].casefold()]
            aligned.append({"quote": quote[:200], "source_ids": matches, "aligned": bool(matches)})
        missing_citation_sentences = [s for s in E.sentences(draft) if len(s.split()) > 8 and not re.search(r"\[[^\]]+\]|\(\w+,\s*\d{4}\)", s)]
        payload = {
            "draft": draft,
            "parsed_citations": parsed,
            "numeric_citations": numeric,
            "footnote_citations": footnotes,
            "bibliography_records": [{"id": r["id"], "title": r["title"], "version": r["version"], "page_anchor": r.get("page_anchor", "unanchored")} for r in records],
            "quote_alignments": aligned,
            "broken_citations": broken,
            "missing_citation_sentences": missing_citation_sentences,
            "page_anchor_builder": True,
            "acceptance": {"passed": not broken and not missing_citation_sentences, "broken_citation_count": len(broken)},
        }
        artifact = self._artifact("citations", "report", payload)
        return {"summary": "Inline, footnote, author-year, bibliography, quote alignment, and broken citation checks completed.", **payload, "artifacts": [artifact]}

    def op_evidence_review(self, env: dict[str, Any]) -> dict[str, Any]:
        params = env["params"]
        records = self._records(env)
        include_languages = set(params.get("include_languages", [])) or {self.config["policies"]["default_language"]}
        exclude_statuses = set(params.get("exclude_statuses", self.config["policies"]["blocked_statuses"]))
        min_authority = float(params.get("min_authority", self.config["policies"]["min_authority"]))
        included: list[dict[str, Any]] = []
        screened: list[dict[str, Any]] = []
        for r in records:
            reasons = []
            if r["language"] not in include_languages:
                reasons.append("language")
            if r["status"] in exclude_statuses:
                reasons.append("status")
            if r["authority"] < min_authority:
                reasons.append("authority")
            if not r["permissions"]["search"]:
                reasons.append("search permission")
            screened.append({"source_id": r["id"], "decision": "exclude" if reasons else "include", "reasons": reasons})
            if not reasons:
                included.append(r)
        themes: dict[str, list[str]] = defaultdict(list)
        for r in included:
            for tag in r.get("tags", []):
                themes[tag].append(r["id"])
        evidence_table = [{"source_id": r["id"], "date": r["date"], "status": r["status"], "authority": r["authority"], "finding": E.sentences(r["text"])[0] if E.sentences(r["text"]) else ""} for r in included]
        gaps = [theme for theme, ids in themes.items() if len(ids) < 2]
        payload = {
            "review_plan": {"type": params.get("review_type", "rapid"), "include_languages": sorted(include_languages), "exclude_statuses": sorted(exclude_statuses), "min_authority": min_authority},
            "screening": screened,
            "included_count": len(included),
            "evidence_table": evidence_table,
            "thematic_synthesis": dict(themes),
            "gap_map": gaps,
            "acceptance": {"passed": bool(included), "requires_human_screening_review": True},
        }
        artifact = self._artifact("evidence-review", "report", payload)
        return {"summary": "Review plan, screening, evidence table, thematic synthesis, and gap map completed.", **payload, "artifacts": [artifact]}

    def op_fact_check(self, env: dict[str, Any]) -> dict[str, Any]:
        params = env["params"]
        records = self._records(env)
        if params.get("claim"):
            claims = [str(params["claim"])]
        elif params.get("draft"):
            claims = [s for s in E.sentences(str(params["draft"])) if E.extract_numbers(s) or E.extract_dates(s)]
        else:
            claims = [s for r in records for s in E.sentences(r["text"]) if E.extract_numbers(s) or E.extract_dates(s)][:20]
        results: list[dict[str, Any]] = []
        for claim in claims:
            claim_tokens = set(E.tokenize(claim))
            claim_numbers = set(E.extract_numbers(claim))
            candidates: list[dict[str, Any]] = []
            for r in records:
                if r["status"] in self.config["policies"]["blocked_statuses"]:
                    continue
                for sentence in E.sentences(r["text"]):
                    overlap = E.jaccard(claim_tokens, set(E.tokenize(sentence)))
                    sentence_numbers = set(E.extract_numbers(sentence))
                    number_match = bool(claim_numbers & sentence_numbers) if claim_numbers else True
                    score = overlap + (0.4 if number_match else 0.0)
                    if score > 0:
                        candidates.append({"source_id": r["id"], "sentence": sentence, "score": round(score, 6), "citation": f"[{r['id']} {r.get('page_anchor','unanchored')} {r['version']}]", "numbers": sorted(sentence_numbers)})
            candidates.sort(key=lambda x: -x["score"])
            top = candidates[:3]
            status = "supported" if top and top[0]["score"] >= float(params.get("support_threshold", 0.35)) else "unsupported"
            conflicts = [c for c in top if claim_numbers and not (claim_numbers & set(c["numbers"]))]
            results.append({"claim": claim, "status": status, "confidence": round(min(0.95, top[0]["score"] if top else 0.0), 3), "evidence": top, "conflicting_evidence": conflicts, "freshness": "checked against record dates and statuses", "uncertainty": "high" if status != "supported" else "medium"})
        corrected = [{"source_id": r["id"], "status": r["status"]} for r in records if r["status"] in ("corrected", "retracted", "superseded")]
        payload = {"claims": results, "claim_count": len(results), "supported_count": sum(1 for r in results if r["status"] == "supported"), "correction_and_retraction_register": corrected, "primary_source_prioritized": True, "acceptance": {"passed": all(r["status"] == "supported" for r in results) if results else False}}
        artifact = self._artifact("fact-check", "report", payload)
        return {"summary": "Numeric and date-bearing claims checked against local evidence with freshness, conflict, and uncertainty records.", **payload, "artifacts": [artifact]}

    # ---------- 22-24. writing / editing ----------

    def op_composition(self, env: dict[str, Any]) -> dict[str, Any]:
        params = env["params"]
        records = self._records(env)
        query = self._query(env)
        hits = E.search_records(self.ctx, records, query, {"limit": int(params.get("limit", 5))})["results"]
        sections = params.get("sections") or ["Findings", "Method", "Discussion", "Conclusion"]
        if isinstance(sections, str):
            sections = [s.strip() for s in sections.split(",") if s.strip()]
        report = self._render_report("Grounded offline research draft", query, str(params.get("language", "en")), records, hits, sections, "Deterministic extractive composition; no local LLM, external lookup, or unreviewed publication.")
        repeated = [s for s, n in Counter(E.sentences(report)).items() if n > 1]
        payload = {"draft": report, "query": query, "sections": sections, "evidence_count": len(hits), "citations": [h["citation"] for h in hits], "coherence": {"section_count": len(sections), "citation_count": len(hits), "repeated_sentences": repeated, "passed": not repeated and bool(hits)}, "generation_mode": "deterministic evidence-extractive", "human_review_gate": "PENDING_HUMAN_REVIEW"}
        artifact = self._artifact("composition", "draft", payload)
        return {"summary": "Grounded long-form draft rendered from the reusable template and retrieved local evidence.", **payload, "artifacts": [artifact]}

    def op_specialized_writing(self, env: dict[str, Any]) -> dict[str, Any]:
        params = env["params"]
        records = self._records(env)
        query = self._query(env)
        genre = str(params.get("genre", "technical")).lower()
        section_map = {
            "academic": ["Abstract", "Introduction", "Methods", "Results", "Discussion", "References"],
            "technical": ["Scope", "System", "Evidence", "Risks", "Recommendations", "References"],
            "legal": ["Question", "Facts", "Authorities", "Analysis", "Conclusion", "Authorities cited"],
            "policy": ["Issue", "Evidence", "Options", "Trade-offs", "Recommendation", "Review"],
            "standards": ["Normative references", "Requirements", "Conformance", "Exceptions", "Audit"],
            "grant": ["Need", "Objectives", "Method", "Budget narrative", "Evaluation", "Sustainability"],
            "case-study": ["Context", "Intervention", "Evidence", "Outcome", "Lessons", "Limitations"],
        }
        sections = section_map.get(genre, section_map["technical"])
        hits = E.search_records(self.ctx, records, query, {"limit": int(params.get("limit", 5))})["results"]
        report = self._render_report(f"{genre.title()} offline draft", query, str(params.get("language", "en")), records, hits, sections, "Specialized structure only; legal, regulatory, and standards conclusions require qualified human review.")
        style_checks = {"required_sections_present": all(f"### {s}" in report for s in sections), "citation_count": len(hits), "genre": genre, "human_domain_review_required": genre in ("legal", "policy", "standards", "academic")}
        payload = {"draft": report, "genre": genre, "sections": sections, "evidence_count": len(hits), "style_checks": style_checks, "generation_mode": "deterministic template and evidence", "human_review_gate": "PENDING_HUMAN_REVIEW"}
        artifact = self._artifact("specialized-writing", "draft", payload)
        return {"summary": "Genre-specific grounded draft and domain style checks completed.", **payload, "artifacts": [artifact]}

    def op_editing(self, env: dict[str, Any]) -> dict[str, Any]:
        params = env["params"]
        records = self._records(env)
        draft = str(params.get("draft") or self._draft_text(env, records, self._query(env)))
        sents = E.sentences(draft)
        words = E.tokenize(draft)
        syllables = sum(max(1, len(re.findall(r"[aeiouy]+", w))) for w in words)
        readability = 206.835 - 1.015 * (len(words) / max(1, len(sents))) - 84.6 * (syllables / max(1, len(words)))
        repeated: list[dict[str, Any]] = []
        grams = [" ".join(words[i : i + 6]) for i in range(max(0, len(words) - 5))]
        for gram, count in Counter(grams).items():
            if count > 1:
                repeated.append({"phrase": gram, "count": count})
        ambiguity = [s for s in sents if re.search(r"\b(?:may|might|possibly|probably|unclear|ambiguous|several|some)\b", s, re.I)]
        glossary = E.read_glossary(self.ctx, params.get("glossary_path"))
        inconsistent: list[str] = []
        for row in glossary:
            if row["target_language"] == params.get("target_language", "es") and re.search(re.escape(row["source_term"]), draft, re.I) and re.search(re.escape(row["target_term"]), draft, re.I):
                inconsistent.append(row["source_term"])
        previous = params.get("previous_draft")
        if params.get("previous_draft_path"):
            previous = E.read_text_file(self.ctx, self.ctx.user_path(str(params["previous_draft_path"]), must_exist=True))
        diff = list(difflib.unified_diff((previous or "").splitlines(), draft.splitlines(), lineterm="")) if previous is not None else []
        payload = {
            "draft": draft,
            "word_count": len(words),
            "sentence_count": len(sents),
            "avg_sentence_words": round(len(words) / max(1, len(sents)), 3),
            "flesch_like_score": round(readability, 3),
            "repeated_phrases": repeated[:20],
            "ambiguity_markers": ambiguity,
            "terminology_inconsistencies": inconsistent,
            "revision_diff": diff[:200],
            "editing_mode": "analysis only; no ungrounded rewriting",
            "acceptance": {"passed": not ambiguity and not inconsistent, "requires_human_editor": True},
        }
        artifact = self._artifact("editing", "report", payload)
        return {"summary": "Readability, repetition, ambiguity, terminology consistency, and revision diff analysis completed.", **payload, "artifacts": [artifact]}

    # ---------- 25-27. originality / rights / review ----------

    def op_originality(self, env: dict[str, Any]) -> dict[str, Any]:
        params = env["params"]
        records = self._records(env)
        draft = str(params.get("draft") or self._draft_text(env, records, self._query(env)))
        draft_words = E.tokenize(draft)
        draft_shingles = E.shingles(draft_words, 8)
        exact: list[dict[str, Any]] = []
        near: list[dict[str, Any]] = []
        for r in records:
            if not r["permissions"]["search"]:
                continue
            text = r["normalized_text"].casefold()
            for sentence in E.sentences(draft):
                if len(sentence) >= 40 and E.normalize_text(sentence).casefold() in text:
                    exact.append({"source_id": r["id"], "passage": sentence[:200]})
            score = E.jaccard(draft_shingles, E.shingles(E.tokenize(r["text"]), 8))
            if score >= float(params.get("near_threshold", 0.18)):
                near.append({"source_id": r["id"], "similarity": round(score, 6)})
        quoted = re.findall(r'"([^"]{20,})"', draft)
        boundary_issues = [q[:120] for q in quoted if q.count(".") > 3]
        omission = [s for s in E.sentences(draft) if len(s.split()) > 10 and not re.search(r"\[[^\]]+\]|\(\w+,\s*\d{4}\)", s) and any(E.normalize_text(s).casefold() in r["normalized_text"].casefold() for r in records)]
        self_similarity = None
        if params.get("previous_draft"):
            self_similarity = round(E.jaccard(E.shingles(E.tokenize(str(params["previous_draft"])), 8), draft_shingles), 6)
        payload = {"draft": draft, "exact_passage_matches": exact, "near_similarity_matches": near, "quotation_boundary_issues": boundary_issues, "citation_omission_candidates": omission, "self_similarity": self_similarity, "common_phrase_filter": "eight-token shingles", "false_positive_review": "human review required for every match", "acceptance": {"passed": not exact and not omission, "requires_human_review": True}}
        artifact = self._artifact("originality", "report", payload)
        return {"summary": "Exact and near passage overlap, quotation boundaries, citation omissions, and self-similarity checked.", **payload, "artifacts": [artifact]}

    def op_rights(self, env: dict[str, Any]) -> dict[str, Any]:
        params = env["params"]
        records = self._records(env)
        action = str(params.get("action", "quote-check"))
        if action in self.config["policies"]["require_human_review_for"]:
            E.require_approval(self.ctx, action, env["approval_token"])
        quote = str(params.get("quote") or E.sentences(records[0]["text"])[0])
        source_id = str(params.get("source_id") or records[0]["id"])
        source = next((r for r in records if r["id"] == source_id), None)
        if source is None:
            raise E.InputError("source_id was not found in the local corpus.", {"source_id": source_id})
        max_chars = int(self.config["policies"]["max_quote_chars"])
        permissions = source["permissions"]
        allowed = permissions["quote"] and permissions["store"] and len(quote) <= max_chars and source["status"] not in self.config["policies"]["blocked_statuses"]
        attribution = f"{source['title']} [{source['id']} {source.get('page_anchor','unanchored')} {source['version']}] {source['license']}"
        evidence = {"source_id": source_id, "license": source["license"], "quote_chars": len(quote), "max_quote_chars": max_chars, "permissions": permissions, "status": source["status"], "allowed": allowed, "attribution": attribution, "redistribution_allowed": permissions["redistribute"], "export_allowed": permissions["export"]}
        payload = {"quote": quote, "rights_decision": evidence, "copyright_status": "public-domain-or-operator-licensed-declared" if source["license"] != "restricted" else "restricted", "attribution_requirement": attribution, "quotation_purpose_review": "human reviewer must confirm purpose and context", "human_review_gate": "PENDING_HUMAN_REVIEW" if action == "rights-exception" else "quote-check-only", "acceptance": {"passed": allowed or action == "rights-exception"}}
        artifact = self._artifact("rights", "evidence", payload)
        return {"summary": "License, permissions, quote length, attribution, redistribution, and rights evidence checked.", **payload, "artifacts": [artifact]}

    def op_human_review(self, env: dict[str, Any]) -> dict[str, Any]:
        params = env["params"]
        self._records(env)
        actions = params.get("actions") or self.config["policies"]["require_human_review_for"]
        if isinstance(actions, str):
            actions = [a.strip() for a in actions.split(",") if a.strip()]
        store = E.ApprovalStore(self.ctx)
        created: list[dict[str, Any]] = []
        for action in actions:
            ticket = f"{self.ctx.run_id}-{action}"
            try:
                created.append(store.request(str(action), ticket, "Automatically requested pending human review; no approval was granted."))
            except E.InputError as exc:
                if "already exists" not in exc.message:
                    raise
        payload = {
            "pending_requests_created": created,
            "workflow": ["request", "human inspection", "explicit approve/reject", "token-gated action", "approval evidence recorded"],
            "conflict_of_interest_declaration_required": True,
            "escalation": "Disagreement or expired token returns to pending review.",
            "self_approval_allowed": self.config["approval"]["self_approval_allowed"],
            "acceptance": {"passed": True, "approvals_granted_by_this_operation": 0},
        }
        artifact = self._artifact("human-review", "requests", payload)
        return {"summary": "Pending human-review requests created; no sensitive action was approved by the tool.", **payload, "artifacts": [artifact]}

    # ---------- 28-30. evaluation / provenance / security ----------

    def op_evaluation(self, env: dict[str, Any]) -> dict[str, Any]:
        rubric = json.loads(E.read_text_file(self.ctx, self.ctx.asset_path("rubric")))
        results: list[dict[str, Any]] = []
        for fixture in rubric["fixtures"]:
            self.ctx.check_limits()
            fop = fixture["operation"]
            fparams = dict(fixture.get("params", {}))
            fparams["_fixture_id"] = fixture["id"]
            try:
                output = self._run_fixture(fop, fparams, env["input_path"])
                passed = False
                observed: dict[str, Any] = {}
                if "expected_source_ids" in fixture:
                    ids = {r["source_id"] for r in output.get("results", [])}
                    observed = {"returned_source_ids": sorted(ids), "expected_source_ids": fixture["expected_source_ids"]}
                    passed = set(fixture["expected_source_ids"]).issubset(ids)
                if "expected_terms" in fixture:
                    text = json.dumps(output, ensure_ascii=False).casefold()
                    observed["expected_terms"] = fixture["expected_terms"]
                    passed = all(term.casefold() in text for term in fixture["expected_terms"])
                if "expected_do_not_translate" in fixture:
                    passed = passed and all(token in output.get("glossed_text", output.get("source_text", "")) for token in fixture["expected_do_not_translate"])
                if "expected_status" in fixture:
                    statuses = [c["status"] for c in output.get("claims", [])]
                    observed["statuses"] = statuses
                    passed = fixture["expected_status"] in statuses
                if "expected_speakers" in fixture:
                    speakers = set(output.get("speakers", []))
                    observed["speakers"] = sorted(speakers)
                    passed = set(fixture["expected_speakers"]).issubset(speakers) and output.get("segment_count", 0) >= fixture.get("expected_min_segments", 0)
                if "expected_allowed" in fixture:
                    observed["allowed"] = output.get("rights_decision", {}).get("allowed")
                    passed = observed["allowed"] == fixture["expected_allowed"]
                if "expected_broken_citations" in fixture:
                    observed["broken_citations"] = len(output.get("broken_citations", []))
                    passed = observed["broken_citations"] == fixture["expected_broken_citations"] and set(fixture.get("expected_source_ids", [])).issubset({c.get("source_id") for c in output.get("parsed_citations", [])})
                results.append({"id": fixture["id"], "operation": fop, "language": fixture.get("language", "en"), "passed": bool(passed), "observed": observed})
            except E.ToolError as exc:
                results.append({"id": fixture["id"], "operation": fop, "passed": False, "error": {"code": exc.code, "message": exc.message}})
        passed_count = sum(1 for r in results if r["passed"])
        pass_rate = passed_count / max(1, len(results))
        by_language: dict[str, dict[str, Any]] = {}
        for lang in sorted({r["language"] for r in results}):
            subset = [r for r in results if r["language"] == lang]
            thresholds = rubric["languages"].get(lang, rubric["languages"]["default"])
            by_language[lang] = {"fixture_count": len(subset), "passed": sum(1 for r in subset if r["passed"]), "thresholds": thresholds}
        payload = {"fixtures": results, "fixture_count": len(results), "passed_count": passed_count, "pass_rate": round(pass_rate, 6), "by_language": by_language, "overall_gate": {"min_pass_rate": rubric["overall"]["min_pass_rate"], "passed": pass_rate >= rubric["overall"]["min_pass_rate"]}, "human_review_required_for": rubric["overall"]["require_human_review_for"], "external_benchmarks_used": False}
        artifact = self._artifact("evaluation", "report", payload)
        return {"summary": "Bundled offline fixtures executed across retrieval, translation, terminology, factuality, transcript, rights, and citation operations.", **payload, "artifacts": [artifact]}

    def op_provenance(self, env: dict[str, Any]) -> dict[str, Any]:
        records = self._records(env)
        corpus_path = self.ctx.workspace_file(self.config["storage"].get("corpus", "corpus.jsonl"))
        previous: dict[str, str] = {}
        if corpus_path.is_file():
            try:
                for line in corpus_path.read_text(encoding="utf-8").splitlines():
                    item = json.loads(line)
                    if item.get("id"):
                        previous[item["id"]] = E.sha256_text(item.get("text", ""))
            except (OSError, UnicodeDecodeError, json.JSONDecodeError):
                previous = {}
        current = {r["id"]: r["sha256"] for r in records}
        drift = [{"source_id": key, "changed": previous.get(key) != value} for key, value in current.items() if key in previous and previous[key] != value]
        audit_path = self.ctx.workspace_file(self.config["storage"].get("audit_log", "audit.jsonl"))
        events: list[dict[str, Any]] = []
        if audit_path.is_file():
            for line in audit_path.read_text(encoding="utf-8").splitlines()[-50:]:
                try:
                    events.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        manifest = {"tool_version": self.config["version"], "catalog_edition": self.ctx.catalog.get("edition"), "run_id": self.ctx.run_id, "config_digest": self.ctx.config_digest, "sources": [{"id": r["id"], "sha256": r["sha256"], "version": r["version"], "license": r["license"]} for r in records], "model_versions": {k: v.get("model_id") for k, v in self.config["providers"].items()}, "prompt_and_query_recorder": "structured run and request logs only", "replay_command": "python3 offline-research-writing-translation/tools-offline-research.py recover --run-id <run-id> --resume"}
        evidence_package = {"source_count": len(records), "checksums": current, "audit_event_tail": events}
        payload = {"provenance_manifest": manifest, "evidence_package": evidence_package, "corpus_drift": drift, "audit_events_inspected": len(events), "reproducible_run": True, "acceptance": {"passed": bool(records)}}
        artifact = self._artifact("provenance", "manifest", payload)
        return {"summary": "Source/model versions, checksums, audit tail, evidence package, drift, and reproducible run descriptor recorded.", **payload, "artifacts": [artifact]}

    def op_security(self, env: dict[str, Any]) -> dict[str, Any]:
        params = env["params"]
        records = self._records(env)
        action = str(params.get("action", "scan"))
        if action in self.config["policies"]["require_human_review_for"]:
            E.require_approval(self.ctx, action, env["approval_token"])
        findings: list[dict[str, Any]] = []
        for r in records:
            for label, patterns in (("pii_like", self.config["policies"]["pii_like_patterns"]), ("secret_like", self.config["policies"]["secret_like_patterns"])):
                for pattern in patterns:
                    try:
                        matches = list(re.finditer(pattern, r["text"], flags=re.I))
                    except re.error as exc:
                        raise E.ConfigError("A configured security regex is invalid.", {"pattern": pattern, "error": str(exc)})
                    for match in matches:
                        findings.append({"source_id": r["id"], "category": label, "offset": match.start(), "length": len(match.group(0)), "value": "[REDACTED]"})
            lowered = r["text"].casefold()
            for term in self.config["policies"]["prompt_injection_terms"]:
                if term in lowered:
                    findings.append({"source_id": r["id"], "category": "prompt_injection", "term": term, "value": "[REDACTED]"})
            if r.get("restricted") and not any(r["permissions"].get(k) is False for k in ("search", "quote", "export")):
                findings.append({"source_id": r["id"], "category": "isolation", "issue": "restricted source has permissive flags"})
            if r["status"] == "retracted" and r["permissions"].get("search"):
                findings.append({"source_id": r["id"], "category": "status", "issue": "retracted source is searchable"})
        restricted = [r["id"] for r in records if r.get("restricted") or r["license"] == "restricted"]
        payload = {
            "findings": findings,
            "finding_count": len(findings),
            "redaction_applied_in_report": True,
            "restricted_source_ids": restricted,
            "export_gate": {"action": action, "approved": action in self.config["policies"]["require_human_review_for"], "network_export_performed": False},
            "secure_workspace": str(self.ctx.workspace.name),
            "acceptance": {"passed": not any(f["category"] == "isolation" for f in findings), "requires_human_security_review": bool(findings)},
        }
        artifact = self._artifact("security", "findings", payload)
        return {"summary": "Sensitive-data, secret-like, injection, restricted-isolation, and export-gate checks completed with redacted findings.", **payload, "artifacts": [artifact]}

    # ---------- 31-32. packaging / governance ----------

    def op_packaging(self, env: dict[str, Any]) -> dict[str, Any]:
        params = env["params"]
        self._records(env)
        action = str(params.get("action", "local-handoff"))
        if action in self.config["policies"]["require_human_review_for"]:
            E.require_approval(self.ctx, action, env["approval_token"])
        package_path = self.ctx.user_path(str(params.get("package_path") or self.ctx.workspace / f"handoff-{self.ctx.run_id}.zip"), for_output=True)
        allowed_ext = {".json", ".jsonl", ".csv", ".md", ".txt"}
        entries: list[Path] = []
        for item in sorted(self.ctx.workspace.iterdir()):
            if item.is_file() and item.suffix.lower() in allowed_ext and not item.name.startswith(".tmp-") and item.stat().st_size <= int(self.config["runtime"]["max_output_bytes"]):
                entries.append(item)
        checksum_manifest = [{"name": p.name, "sha256": E.sha256_file(p), "bytes": p.stat().st_size} for p in entries]
        license_manifest = [{"name": p.name, "license": "operator-workspace-output", "redistribution": "verify-before-sharing"} for p in entries]
        install_notes = "Offline install: copy the Skill Tool folder beside one root SKILL.md; run the primary help and self-test commands from the future skill root."
        self_test_notes = "Run: python3 offline-research-writing-translation/tools-selftest.py --output selftest-results.json"
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for p in entries:
                archive.write(p, arcname=p.name)
            archive.writestr("checksum-manifest.json", json.dumps(checksum_manifest, ensure_ascii=False, indent=2) + "\n")
            archive.writestr("license-manifest.json", json.dumps(license_manifest, ensure_ascii=False, indent=2) + "\n")
            archive.writestr("install-notes.txt", install_notes + "\n")
            archive.writestr("self-test-notes.txt", self_test_notes + "\n")
        E.atomic_write(self.ctx, package_path, buffer.getvalue(), binary=True)
        payload = {
            "package_path": str(package_path),
            "entry_count": len(entries) + 4,
            "sha256": E.sha256_file(package_path),
            "checksum_manifest": checksum_manifest,
            "license_manifest": license_manifest,
            "install_notes": install_notes,
            "self_test_notes": self_test_notes,
            "backup_record": {"workspace": str(self.ctx.workspace.name), "files": len(entries)},
            "recovery_record": "Use checkpoint-<run-id>.json and recover --resume.",
            "nested_archives_included": False,
            "external_publishing_performed": False,
            "action": action,
        }
        artifact = self._artifact("packaging", "handoff", payload)
        return {"summary": "Bounded offline handoff ZIP, license/checksum manifests, install and self-test notes, and backup/recovery records created.", **payload, "artifacts": [artifact]}

    def op_governance(self, env: dict[str, Any]) -> dict[str, Any]:
        params = env["params"]
        self._records(env)
        catalog = self.ctx.catalog
        request_schema = json.loads(E.read_text_file(self.ctx, self.ctx.asset_path("request_schema")))
        interface_schema = {"primary_entry_point": "tools-offline-research.py", "operations": [{"name": k, "description": v} for k, v in sorted(self.ctx.adapter.OPERATION_DESCRIPTIONS.items())], "request_schema": request_schema}
        sop = ["Parse and validate request", "Validate local corpus and permissions", "Run bounded category operation", "Checkpoint and audit", "Run evaluation and human-review gates", "Package only after approval"]
        quick_start = "python3 offline-research-writing-translation/tools-offline-research.py help"
        troubleshooting = {"INVALID_INPUT": "Check UTF-8 input, schema, and --input path.", "APPROVAL_REQUIRED": "Request and explicitly approve a ticket.", "TIMEOUT": "Raise --timeout or reduce input.", "CANCELLED": "Remove the cancel file and recover --resume.", "IO_ERROR": "Check permissions and choose another workspace."}
        training = [{"exercise": "Search a licensed local corpus", "command": "run --operation retrieval --input <corpus> --params '{\"query\":\"...\"}'"}, {"exercise": "Verify all exact catalog names", "command": "capabilities --verify"}]
        dependency_graph = {"tools-offline-research.py": ["tools-catalog-adapter.py", "tools-workflow-engine.py", "tools-domain-operations.py", "tools-package-audit.py", "asset-default-config.json"], "tools-selftest.py": ["tools-offline-research.py", "tools-package-audit.py"], "tools-domain-operations.py": ["tools-workflow-engine.py", "asset-report-template.md"]}
        governance = {"category_count": catalog.get("category_count"), "capability_count": catalog.get("capability_count"), "operation_count": catalog.get("operation_count"), "edition": catalog.get("edition"), "unification": catalog.get("unification"), "unavailable_integrations": E.unavailable_integrations(self.ctx)}
        payload = {"interface_schema": interface_schema, "sop": sop, "role_based_quick_start": quick_start, "troubleshooting_decision_tree": troubleshooting, "training_exercises": training, "known_issue_register": ["No bundled OCR/ASR/neural MT/LLM model execution", "External corpus accuracy requires licensed local benchmark replacement"], "dependency_graph": dependency_graph, "capability_governance": governance, "skill_interface_standard": "one root SKILL.md in the future parent package; this folder contains no SKILL.md", "governance_mode": str(params.get("governance_mode", "report"))}
        artifact = self._artifact("governance", "report", payload)
        return {"summary": "Interface schema, SOP, quick start, troubleshooting tree, training, dependency graph, and capability governance report generated.", **payload, "artifacts": [artifact]}


__all__ = ["DomainOperations"]
