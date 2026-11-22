#!/usr/bin/env python3
"""Ultimate Translation: unified, reusable localization Skill Tool primary CLI."""
from __future__ import annotations

import argparse
import copy
import csv
import io
import json
import os
import re
import shutil
import sys
import zipfile
from collections import Counter
from pathlib import Path
from typing import Any, Sequence

BASE_DIR = Path(__file__).resolve().parent


def _load_module(alias: str, filename: str) -> Any:
    import importlib.util
    if alias in sys.modules:
        return sys.modules[alias]
    spec = importlib.util.spec_from_file_location(alias, BASE_DIR / filename)
    if not spec or not spec.loader:
        raise RuntimeError(f"Cannot load {filename}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[alias] = module
    spec.loader.exec_module(module)
    return module


core = _load_module("tools_core", "tools-core.py")
providers = _load_module("tools_providers", "tools-providers.py")
formats = _load_module("tools_formats", "tools-formats.py")
quality = _load_module("tools_quality", "tools-quality.py")
media = _load_module("tools_media", "tools-media.py")
project = _load_module("tools_project", "tools-project.py")
catalog = _load_module("tools_catalog", "tools-catalog.py")

ToolError = core.ToolError
Segment = core.Segment
Translation = core.Translation

COMMANDS = {
    "capabilities", "translate", "pseudolocalize", "batch", "detect", "transliterate", "inspect", "align", "terms",
    "memory", "qa", "review", "delta", "convert", "subtitles", "audio", "transcribe", "ocr", "visual", "font",
    "locale", "plan", "project", "repository", "secure", "web", "narrative", "adapt", "validate",
}


class Parser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise ToolError("invalid_arguments", message, hint=f"Run {self.prog} --help for exact usage.")


def output_value(value: Any, args: argparse.Namespace, *, force_json: bool = False) -> Any:
    output = getattr(args, "output", None)
    if output and not force_json:
        path = core.resolve_user_path(output)
        core.atomic_write(path, core.json_output(value))
    else:
        sys.stdout.write(core.json_output(value))
    return value


def get_config(args: argparse.Namespace) -> dict[str, Any]:
    config = core.load_config(getattr(args, "config", None), BASE_DIR)
    prompt_path = BASE_DIR / "asset-provider-prompts.json"
    if prompt_path.is_file():
        prompts = core.read_json(prompt_path)
        config["prompts"] = core.deep_merge(prompts, config.get("prompts", {}))
    return config


def selected_terms(args: argparse.Namespace, source: str, target: str) -> list[dict[str, Any]]:
    return core.load_glossary(getattr(args, "glossary", None), source_locale=source, target_locale=target)


def selected_provider(args: argparse.Namespace, config: dict[str, Any], terms: Sequence[dict[str, Any]], *, force: str | None = None) -> Any:
    return providers.make_provider(config, glossary=terms, allow_network=bool(getattr(args, "allow_network", False)), force=force or getattr(args, "provider", None))


def load_style(path: str | None) -> dict[str, Any]:
    if not path:
        return {}
    value = core.read_json(core.resolve_user_path(path, must_exist=True))
    if not isinstance(value, dict):
        raise ToolError("invalid_style", "Style guide must be a JSON object.")
    return value


def translation_options(args: argparse.Namespace) -> dict[str, Any]:
    options = core.parse_key_values(getattr(args, "set", None))
    for key in ("tone", "mode", "audience", "domain", "brand_voice", "reading_level", "channel", "character_voice", "purpose"):
        value = getattr(args, key, None)
        if value is not None:
            options[key] = value
    if getattr(args, "vernacular", False): options["vernacular"] = True
    if getattr(args, "accessibility", False): options["accessibility"] = True
    if getattr(args, "dubbing", False): options["dubbing"] = True
    if getattr(args, "lip_sync", False): options["lip_sync"] = True
    style = load_style(getattr(args, "style", None))
    if style:
        options["style_rules"] = style
        if isinstance(style.get("brand"), dict) and style["brand"].get("voice"):
            options["brand_voice"] = style["brand"]["voice"]
    for key, cast in (("pseudo_expansion", float), ("max_length", int)):
        if getattr(args, key, None) is not None:
            options[key] = cast(getattr(args, key))
    for key in ("pseudo_accents", "pseudo_brackets", "pseudo_bidi"):
        if hasattr(args, key): options[key] = getattr(args, key)
    return options


def ensure_output(output: Path, *, source: Path | None = None, approve_overwrite: bool = False) -> str | None:
    if source and output.exists() and source.resolve() == output.resolve() and not approve_overwrite:
        raise ToolError("approval_required", "In-place source replacement requires --approve-overwrite. A backup will be created.")
    if output.exists() and not approve_overwrite:
        raise ToolError("output_exists", f"Output already exists: {output}", hint="Choose a new output path or use --approve-overwrite to create a backup and replace it.")
    if output.exists() and output.is_dir():
        raise ToolError("invalid_output", "Output path is a directory.")
    backup = None
    if output.exists() and approve_overwrite:
        backup_path = Path(str(output) + ".backup")
        shutil.copyfile(output, backup_path)
        backup = str(backup_path)
    return backup


def inferred_source(document: core.Document, requested: str) -> tuple[str, dict[str, Any] | None]:
    if requested != "auto": return requested, None
    detected = core.detect_language("\n".join(segment.text for segment in document.segments))
    return str(detected["language"]), detected


def perform_translation(args: argparse.Namespace, *, input_value: str | None = None, output_value_path: str | None = None, forced_provider: str | None = None) -> dict[str, Any]:
    config = get_config(args)
    input_path = core.resolve_user_path(input_value or args.input, must_exist=True)
    output_path = core.resolve_user_path(output_value_path or args.output)
    if not getattr(args, "target", None):
        raise ToolError("missing_target", "--target is required for translation.")
    keys = split_specs(getattr(args, "keys", None))
    columns = split_specs(getattr(args, "columns", None))
    document = formats.load_document(input_path, config, encoding=getattr(args, "encoding", "auto"), keys=keys, columns=columns)
    if not document.segments:
        raise ToolError("no_translatable_content", "No translatable segments were selected.", hint="Check format-specific selection, translatable attributes, --keys/--columns, or use inspect/OCR/transcription.")
    source, detection = inferred_source(document, getattr(args, "source", "auto"))
    terms = selected_terms(args, source, args.target)
    options = translation_options(args)
    options["glossary"] = [{"source": t["source"], "target": t["target"], "notes": t.get("notes", "")} for t in terms[:200]]
    options["parallel_examples"] = config["translation"].get("parallel_examples", [])
    provider = selected_provider(args, config, terms, force=forced_provider)
    memory = core.load_memory(getattr(args, "memory", None), source_locale=source, target_locale=args.target)
    checkpoint = core.resolve_user_path(getattr(args, "checkpoint", None) or str(output_path) + config["output"]["checkpoint_suffix"])
    engine = core.TranslationEngine(provider, config, source_locale=source, target_locale=args.target, options=options, memory=memory, checkpoint_path=checkpoint, resume=bool(getattr(args, "resume", False)))
    source_data = core.bounded_read(input_path, int(config["limits"]["max_input_bytes"]))
    results = engine.run(document.segments, source_data)
    translated_text = {sid: result.text for sid, result in results.items()}
    rendered = core.safe_output_size(document.render(translated_text), config)
    backup = ensure_output(output_path, source=input_path, approve_overwrite=bool(getattr(args, "approve_overwrite", False)))
    core.atomic_write(output_path, rendered)
    report_segments = [core.translation_report_segment(segment, results[segment.id]) for segment in document.segments]
    target_segments = [Segment(segment.id, results[segment.id].text, segment.context, segment.metadata) for segment in document.segments]
    defects = []
    for segment, target_segment in zip(document.segments, target_segments):
        defects.extend(quality.pair_qa(segment, target_segment.text, source_locale=source, target_locale=args.target, terms=terms, allow_conversion=bool(getattr(args, "allow_unit_conversion", False))))
    high_risk = args.domain in set(config["translation"].get("require_human_review_domains", [])) if getattr(args, "domain", None) else False
    review_required = high_risk or any(row["review_required"] or row["uncertainties"] for row in report_segments) or bool(defects)
    report = {
        "ok": True, "operation": "pseudolocalize" if forced_provider == "pseudo" else "translate", "version": core.VERSION,
        "input": str(input_path), "output": str(output_path), "format": document.kind, "source_locale": source, "target_locale": args.target,
        "language_detection": detection, "provider": provider.name, "segments": len(document.segments),
        "words": sum(core.count_words(s.text) for s in document.segments), "reused_segments": sum(row["reused"] for row in report_segments),
        "review_required": review_required, "human_expert_review_required": high_risk,
        "defect_summary": dict(Counter(row["severity"] for row in defects)), "defects": defects,
        "uncertain_segments": [{"id": row["id"], "confidence": row["confidence"], "uncertainties": row["uncertainties"]} for row in report_segments if row["review_required"] or row["uncertainties"]],
        "translations": report_segments, "document_metadata": document.metadata, "backup": backup,
        "checkpoint": str(checkpoint), "source_sha256": core.fingerprint(source_data), "output_bytes": len(rendered), "output_sha256": core.fingerprint(rendered),
        "limitations": "Objective post-checks do not prove linguistic accuracy, rendered layout, cultural safety, or domain correctness. Resolve all review flags before approval or publication.",
    }
    report_path = core.resolve_user_path(getattr(args, "report", None) or str(output_path) + config["output"]["report_suffix"])
    core.write_json(report_path, report)
    report["report_path"] = str(report_path)
    # Persist report_path inside the report as well.
    core.write_json(report_path, report)
    return report


def split_specs(values: Any) -> list[str]:
    if not values: return []
    if isinstance(values, str): values = [values]
    result = []
    for value in values:
        result.extend(item.strip() for item in str(value).split(",") if item.strip())
    return result


def cmd_capabilities(args: argparse.Namespace) -> dict[str, Any]:
    registry = catalog.load_registry(BASE_DIR)
    result = catalog.capability_list(registry, category=args.category, query=args.query, commands=COMMANDS)
    if args.format == "text" and not args.output:
        current = None
        for row in result["capabilities"]:
            if row["category"] != current:
                current = row["category"]
                sys.stdout.write(f"\n{row['category_number']}. {current}\n")
            sys.stdout.write(f"  {row['id']}  {row['name']}\n    {row['command']} [{row['mode']}]\n    {row['description']}\n")
        sys.stdout.write(f"\nPreflight: {'PASS' if result['preflight']['passed'] else 'FAIL'}; {result['returned']} shown of 144.\n")
        return result
    return output_value(result, args)


def cmd_translate(args: argparse.Namespace) -> dict[str, Any]:
    result = perform_translation(args)
    return output_value({"ok": True, "operation": result["operation"], "output": result["output"], "report": result["report_path"], "segments": result["segments"], "provider": result["provider"], "review_required": result["review_required"], "defect_summary": result["defect_summary"]}, args, force_json=True)


def cmd_pseudolocalize(args: argparse.Namespace) -> dict[str, Any]:
    result = perform_translation(args, forced_provider="pseudo")
    return output_value({"ok": True, "operation": "pseudolocalize", "output": result["output"], "report": result["report_path"], "segments": result["segments"], "review_required": result["review_required"]}, args, force_json=True)


def cmd_batch(args: argparse.Namespace) -> dict[str, Any]:
    config = get_config(args)
    root = core.resolve_user_path(args.input, must_exist=True, kind="directory")
    output_root = core.resolve_user_path(args.output_dir)
    if output_root.resolve() == root.resolve() or root.resolve() in output_root.resolve().parents:
        raise ToolError("invalid_output", "Batch output directory must not be the input directory or its descendant.")
    output_root.mkdir(parents=True, exist_ok=True)
    records = []
    for base, dirs, names in os.walk(root, followlinks=False):
        dirs[:] = sorted(d for d in dirs if not d.startswith(".") and d not in {"node_modules", "build", "dist", "__pycache__"})
        for name in sorted(names):
            source = Path(base) / name
            if source.is_symlink() or source.suffix.lower() not in formats.SUPPORTED_EXTENSIONS: continue
            relative = source.relative_to(root)
            target = output_root / relative
            args_copy = copy.deepcopy(args)
            args_copy.input = str(source)
            args_copy.output = str(target)
            args_copy.report = str(target) + config["output"]["report_suffix"]
            args_copy.checkpoint = str(target) + config["output"]["checkpoint_suffix"]
            try:
                if args.resume and target.is_file() and Path(args_copy.report).is_file() and Path(args_copy.checkpoint).is_file():
                    previous_report = core.read_json(Path(args_copy.report))
                    if previous_report.get("source_sha256") == core.fingerprint(core.bounded_read(source, int(config["limits"]["max_input_bytes"]))) and previous_report.get("output_sha256") == core.fingerprint(core.bounded_read(target, int(config["limits"]["max_output_bytes"]))):
                        records.append({"input": relative.as_posix(), "output": str(target), "ok": True, "skipped": True, "reason": "matching completed source and output fingerprints", "report": args_copy.report})
                        continue
                result = perform_translation(args_copy)
                records.append({"input": relative.as_posix(), "output": str(target), "ok": True, "segments": result["segments"], "review_required": result["review_required"], "report": result["report_path"]})
            except Exception as exc:
                error = core.error_result(exc)["error"]
                records.append({"input": relative.as_posix(), "output": str(target), "ok": False, "error": error})
    if not records:
        raise ToolError("no_supported_files", "Batch found no supported regular files.")
    result = {"ok": all(row["ok"] for row in records), "operation": "batch", "summary": {"files": len(records), "succeeded": sum(row["ok"] for row in records), "failed": sum(not row["ok"] for row in records)}, "files": records, "recovery": "Failures are isolated. Correct the reported input/provider issue and rerun with --resume; successful outputs are not overwritten unless explicitly approved."}
    report_path = core.resolve_user_path(args.report or str(output_root / "batch-report.json"))
    core.write_json(report_path, result)
    sys.stdout.write(core.json_output({"operation": "batch", "report": str(report_path), **result["summary"], "ok": result["ok"]}))
    return result


def cmd_detect(args: argparse.Namespace) -> dict[str, Any]:
    config = get_config(args)
    path = core.resolve_user_path(args.input, must_exist=True)
    if path.suffix.lower() in {".txt", ".md", ".markdown"}:
        text = core.decode_text(core.bounded_read(path, int(config["limits"]["max_input_bytes"])), args.encoding)[0]
    else:
        document = formats.load_document(path, config, encoding=args.encoding)
        text = "\n".join(segment.text for segment in document.segments)
    segments = core.detect_segments(text, int(config["limits"]["max_segment_chars"]))
    for row in segments:
        runs = []
        for sentence_match in re.finditer(r"[^.!?\u3002\uff01\uff1f]+[.!?\u3002\uff01\uff1f]*", row["text"]):
            sentence = sentence_match.group(0).strip()
            if not sentence: continue
            detected = core.detect_language(sentence)
            runs.append({"text": sentence[:500], "language": detected["language"], "confidence": detected["confidence"], "scripts": detected["scripts"]})
        languages = {run["language"] for run in runs if run["confidence"] >= 0.5 and run["language"] != "und"}
        row["language_runs"] = runs
        row["code_switching_candidate"] = len(languages) > 1
        row["possible_untranslated_span"] = bool(args.target_locale and any(run == args.target_locale.split("-")[0] for run in languages))
        row["borrowed_words"] = "Not asserted by heuristics; mark through qualified review."
    overall = core.detect_language(text)
    result = {"ok": True, "operation": "detect", "input": str(path), "overall": overall, "segments": segments, "summary": {"segments": len(segments), "code_switching_candidates": sum(row["code_switching_candidate"] for row in segments), "review_required": sum(row["review_required"] for row in segments)}, "limitations": "Local detection uses scripts and stopwords. Dialect shifts, vernacular, borrowed words, short spans, and low-resource languages require human or configured provider confirmation."}
    return output_value(result, args)


def read_text_argument(args: argparse.Namespace) -> str:
    if getattr(args, "text", None) is not None: return args.text
    if not getattr(args, "input", None): raise ToolError("missing_input", "Supply --input or --text.")
    path = core.resolve_user_path(args.input, must_exist=True)
    config = get_config(args)
    return core.decode_text(core.bounded_read(path, int(config["limits"]["max_input_bytes"])), getattr(args, "encoding", "auto"))[0]


def cmd_transliterate(args: argparse.Namespace) -> dict[str, Any]:
    text = read_text_argument(args)
    if args.mapping:
        data = core.read_json(core.resolve_user_path(args.mapping, must_exist=True))
    else:
        data = core.read_json(BASE_DIR / "asset-transliteration.json")
    schemes = data.get("schemes", data)
    scheme = schemes.get(args.scheme)
    if not isinstance(scheme, dict) or not isinstance(scheme.get("digraphs"), dict):
        raise ToolError("invalid_mapping", "Unknown or invalid transliteration scheme.", details={"scheme": args.scheme, "available": sorted(schemes)})
    mapping = {str(k): str(v) for k, v in scheme["digraphs"].items()}
    globally_reversible = len(set(mapping.values())) == len(mapping)
    ambiguous: dict[str, list[str]] = {}
    if args.reverse:
        grouped: dict[str, list[str]] = {}
        for source, target in mapping.items(): grouped.setdefault(target, []).append(source)
        ambiguous = {target: sources for target, sources in grouped.items() if len(sources) > 1}
        mapping = {target: sources[0] for target, sources in grouped.items()}
    keys = sorted(mapping, key=len, reverse=True)

    def apply(value: str, table: dict[str, str], table_keys: Sequence[str], *, reject_ambiguous: bool = False) -> tuple[str, list[str], dict[str, str]]:
        output: list[str] = []
        unknown: list[str] = []
        used: dict[str, str] = {}
        position = 0
        while position < len(value):
            matched = next((key for key in table_keys if value.startswith(key, position)), None)
            if matched:
                if reject_ambiguous and matched in ambiguous:
                    raise ToolError("nonreversible_mapping", "Input contains a transliteration with multiple possible native-script sources.", details={"text": matched, "possible_sources": ambiguous[matched]})
                output.append(table[matched]); used[matched] = table[matched]; position += len(matched)
            else:
                char = value[position]; output.append(char)
                if re.search(r"[^\W\d_]", char, re.UNICODE) and char not in unknown: unknown.append(char)
                position += 1
        return "".join(output), unknown, used

    transliterated, unknown, used = apply(text, mapping, keys, reject_ambiguous=args.reverse)
    round_trip = None
    if len(set(used.values())) == len(used):
        inverse = {value: key for key, value in used.items()}
        rebuilt, _unknown, _used = apply(transliterated, inverse, sorted(inverse, key=len, reverse=True))
        round_trip = rebuilt == text
    reversible = globally_reversible
    result = {"ok": True, "operation": "transliterate", "scheme": args.scheme, "reverse": args.reverse, "native": text, "transliterated": transliterated, "source_script": scheme.get("target_script") if args.reverse else scheme.get("source_script"), "target_script": scheme.get("source_script") if args.reverse else scheme.get("target_script"), "unknown_characters_preserved": unknown, "mapping_is_one_to_one": reversible, "round_trip_exact": round_trip, "review_required": bool(unknown) or not reversible, "limitations": scheme.get("language_note", "Character mapping is not pronunciation guidance. Verify legal names and locale-specific standards with a qualified reviewer.")}
    if args.output and Path(args.output).suffix.lower() == ".txt": core.atomic_write(Path(args.output), transliterated + "\n")
    else: output_value(result, args)
    return result


def cmd_inspect(args: argparse.Namespace) -> dict[str, Any]:
    config = get_config(args)
    path = core.resolve_user_path(args.input, must_exist=True)
    result = formats.probe_file(path, config, encoding=args.encoding, keys=split_specs(args.keys), columns=split_specs(args.columns))
    if path.suffix.lower() == ".wav":
        try:
            audio = media.analyze_wav(path)
            result["audio"] = audio
            result["scope"]["audio_hours"] = round(audio["duration_seconds"] / 3600, 6)
        except ToolError as exc: result["risks"].append({"severity": "error", "type": exc.code, "message": exc.message})
    result["ok"] = not any(risk.get("severity") == "error" for risk in result["risks"])
    result["operation"] = "inspect"
    result["scope"]["subtitle_minutes"] = result["scope"].get("subtitle_cues", 0) * 4 / 60
    result["scope"]["screenshots"] = 1 if result["detected_family"] == "image" else 0
    return output_value(result, args)


def load_alignment_documents(args: argparse.Namespace, config: dict[str, Any]) -> tuple[core.Document, core.Document]:
    source_path = core.resolve_user_path(args.source, must_exist=True)
    target_path = core.resolve_user_path(args.target, must_exist=True)
    kwargs = {"encoding": getattr(args, "encoding", "auto"), "keys": split_specs(getattr(args, "keys", None)), "columns": split_specs(getattr(args, "columns", None))}
    source = formats.load_document(source_path, config, **kwargs)
    target = formats.load_document(target_path, config, **kwargs)
    return source, target


def cmd_align(args: argparse.Namespace) -> dict[str, Any]:
    config = get_config(args)
    source, target = load_alignment_documents(args, config)
    rows = quality.align_segments(source.segments, target.segments)
    aligned = [{"id": s.id, "source": s.text, "target": t.text if t else "", "context": s.context, "metadata": s.metadata} for s, t, _target in rows]
    result: dict[str, Any] = {"ok": True, "operation": "align", "version": 1, "source_locale": args.source_locale, "target_locale": args.target_locale, "granularity": args.granularity, "segments": aligned, "summary": {"pairs": len(aligned), "missing_targets": sum(not row["target"] for row in aligned)}}
    if args.learning:
        terms = selected_terms(args, args.source_locale, args.target_locale)
        vocabulary = []
        exercises = []
        answers = []
        for row in aligned:
            for term in terms:
                if term["source"].casefold() in row["source"].casefold():
                    vocabulary.append({"segment_id": row["id"], "source": term["source"], "target": term["target"], "notes": term.get("notes", ""), "pronunciation": term.get("pronunciation", "")})
                    if term["target"] and term["target"] in row["target"]:
                        exercises.append({"id": f"exercise-{len(exercises)+1:04d}", "type": "fill-approved-term", "difficulty": "graded-by-segment-length", "prompt": row["target"].replace(term["target"], "_____"), "segment_id": row["id"]})
                        answers.append({"exercise_id": exercises[-1]["id"], "answer": term["target"]})
        result["learning"] = {"vocabulary": vocabulary, "grammar_notes": [], "pronunciation_help": [v for v in vocabulary if v.get("pronunciation")], "graded_difficulty": [{"segment_id": row["id"], "source_words": core.count_words(row["source"]), "level": "advanced" if core.count_words(row["source"]) > 25 else "intermediate" if core.count_words(row["source"]) > 12 else "beginner"} for row in aligned], "exercises": exercises, "answer_key": answers, "grammar_note_limitation": "Use adapt learning with a configured provider and teacher review for grammar explanations; none are invented locally."}
    if args.output:
        path = core.resolve_user_path(args.output)
        suffix = path.suffix.lower()
        if suffix == ".csv":
            stream = io.StringIO(); writer = csv.DictWriter(stream, fieldnames=["id", "source", "target", "context"]); writer.writeheader()
            for row in aligned: writer.writerow({key: row[key] for key in writer.fieldnames})
            core.atomic_write(path, stream.getvalue())
        elif suffix == ".tmx": core.atomic_write(path, quality.to_tmx(quality.build_memory(aligned, source_locale=args.source_locale, target_locale=args.target_locale)))
        elif suffix in {".md", ".markdown"}:
            lines = ["# Parallel Bilingual Review", ""]
            for row in aligned: lines += [f"## {row['id']}", "", f"**Source:** {row['source']}", "", f"**Target:** {row['target']}", ""]
            core.atomic_write(path, "\n".join(lines))
        else: core.atomic_write(path, core.json_output(result))
    else: sys.stdout.write(core.json_output(result))
    return result


def cmd_terms(args: argparse.Namespace) -> dict[str, Any]:
    config = get_config(args)
    operation = args.operation
    if operation == "extract":
        document = formats.load_document(core.resolve_user_path(args.input, must_exist=True), config, encoding=args.encoding)
        result = {"ok": True, "operation": operation, "terms": quality.extract_terms(document.segments, max_terms=args.max_terms), "review_required": True}
    elif operation == "validate":
        terms = core.load_glossary(args.input)
        result = {"ok": True, "operation": operation, "valid": True, "terms": len(terms), "status_counts": dict(Counter(term["status"] for term in terms))}
    elif operation == "check":
        source, target = load_alignment_documents(args, config)
        terms = selected_terms(args, args.source_locale, args.target_locale)
        defects = []
        for s, t, target_text in quality.align_segments(source.segments, target.segments):
            defects.extend(row for row in quality.pair_qa(s, target_text, source_locale=args.source_locale, target_locale=args.target_locale, terms=terms) if row["category"] in {"terminology", "forbidden-term"})
        result = {"ok": not any(row["severity"] in {"error", "critical"} for row in defects), "operation": operation, "defects": defects, "summary": {"defects": len(defects)}, "limitations": "Literal matching flags possible missing or inflected terminology; it never blindly replaces valid variants."}
    elif operation == "style":
        style = load_style(args.style) if args.style else load_style(str(BASE_DIR / "asset-style-guide.json"))
        terms = core.load_glossary(args.glossary) if args.glossary else []
        markdown = project.style_guide_markdown(args.target_locale, brand=style.get("brand", style), terms=terms)
        if args.output: core.atomic_write(core.resolve_user_path(args.output), markdown)
        else: sys.stdout.write(markdown)
        return {"ok": True, "operation": operation, "output": args.output}
    elif operation == "pronunciation":
        terms = core.load_glossary(args.glossary or args.input)
        entries = [{"term": row["source"], "target": row["target"], "native_script": row.get("native_script", row["target"]), "phonetic_notation": row.get("pronunciation", ""), "notation_standard": row.get("pronunciation_standard", "unspecified"), "locale": row.get("target_locale", args.target_locale), "notes": row.get("notes", ""), "review_required": not bool(row.get("pronunciation"))} for row in terms]
        result = {"ok": True, "operation": operation, "entries": entries, "summary": {"entries": len(entries), "missing_pronunciation": sum(not row["phonetic_notation"] for row in entries)}, "limitations": "Only supplied pronunciation data is returned offline. Use adapt pronunciation with an approved provider and native review for additional terms; personal or fictional pronunciation is never guessed."}
    else: raise ToolError("invalid_operation", f"Unsupported terms operation: {operation}")
    return output_value(result, args)


def cmd_memory(args: argparse.Namespace) -> dict[str, Any]:
    config = get_config(args)
    if args.operation == "build":
        source, target = load_alignment_documents(args, config)
        rows = [{"id": s.id, "source": s.text, "target": t.text if t else "", "context": s.context, "metadata": s.metadata} for s, t, _ in quality.align_segments(source.segments, target.segments)]
        if args.status in {"approved", "reviewed"} and not args.confirm_sensitive: raise ToolError("approval_required", "Approved/reviewed memory status requires --confirm-sensitive after bilingual verification.")
        memory = quality.build_memory(rows, source_locale=args.source_locale, target_locale=args.target_locale, status=args.status, origin=args.origin)
        result = {"ok": True, "operation": "memory-build", "memory": memory}
        if args.output: core.atomic_write(core.resolve_user_path(args.output), core.json_output(memory))
        else: sys.stdout.write(core.json_output(result))
        return result
    value = core.read_json(core.resolve_user_path(args.input, must_exist=True))
    if args.operation == "clean":
        result = quality.clean_memory(value, source_locale=args.source_locale, target_locale=args.target_locale)
        result["ok"] = True; result["operation"] = "memory-clean"
    elif args.operation == "tmx":
        text = quality.to_tmx(value)
        if args.output: core.atomic_write(core.resolve_user_path(args.output), text)
        else: sys.stdout.write(text)
        return {"ok": True, "operation": "memory-tmx", "output": args.output}
    else: raise ToolError("invalid_operation", "Memory operation must be build, clean, or tmx.")
    return output_value(result, args)


def cmd_qa(args: argparse.Namespace) -> dict[str, Any]:
    config = get_config(args)
    source, target = load_alignment_documents(args, config)
    terms = selected_terms(args, args.source_locale, args.target_locale)
    provider_report = []
    if args.translation_report:
        value = core.read_json(core.resolve_user_path(args.translation_report, must_exist=True)); provider_report = value.get("translations", [])
    result = quality.run_qa(source.segments, target.segments, source_locale=args.source_locale, target_locale=args.target_locale, terms=terms, allow_conversion=args.allow_unit_conversion, domain=args.domain, provider_report=provider_report)
    result["ok"] = True; result["operation"] = "qa"
    if args.accessibility:
        target_text = core.bounded_read(Path(args.target), int(config["limits"]["max_input_bytes"])).decode("utf-8", "replace")
        result["accessibility"] = quality.accessibility_audit(target_text, kind=target.kind, target_locale=args.target_locale)
    return output_value(result, args)


def cmd_review(args: argparse.Namespace) -> dict[str, Any]:
    report = core.read_json(core.resolve_user_path(args.input, must_exist=True))
    decisions = core.read_json(core.resolve_user_path(args.decisions, must_exist=True))
    if isinstance(decisions, dict): decisions = decisions.get("decisions", [])
    if not isinstance(decisions, list): raise ToolError("invalid_decisions", "Review decisions must be an array.")
    by_segment = {row.get("id"): row for row in report.get("aligned_segments", report.get("translations", []))}
    applied = []
    for index, decision in enumerate(decisions):
        if not isinstance(decision, dict) or decision.get("segment_id") not in by_segment: raise ToolError("invalid_decisions", "Decision has unknown segment_id.", details={"index": index})
        if decision.get("decision") not in {"accept", "reject", "changes-requested", "escalate"} or not str(decision.get("reviewer", "")).strip(): raise ToolError("invalid_decisions", "Each decision needs reviewer and accept/reject/changes-requested/escalate.")
        row = by_segment[decision["segment_id"]]
        row.setdefault("reviews", []).append({"reviewer": decision["reviewer"], "decision": decision["decision"], "comment": decision.get("comment", ""), "evidence": decision.get("evidence", ""), "date": decision.get("date", core.utc_now())})
        row["review_status"] = decision["decision"]
        applied.append({"segment_id": decision["segment_id"], "decision": decision["decision"]})
        # Accepting a segment never silently closes objective defects.
    result = {**report, "review_workflow": {"applied": applied, "objective_defects_unchanged": True, "pending_segments": [sid for sid, row in by_segment.items() if row.get("review_status") not in {"accept", "reject"}]}, "limitations": "Reviewer decisions are recorded but do not erase objective defects or constitute release approval. Use project update approval.add with --confirm-sensitive for sign-off."}
    return output_value(result, args)


def exchange_or_document_rows(path: Path, config: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
    if path.suffix.lower() == ".json":
        try:
            value = core.read_json(path)
            if isinstance(value, dict) and isinstance(value.get("segments"), list) and all(isinstance(row, dict) and ("source" in row or "text" in row) for row in value["segments"]): return value["segments"], value
        except ToolError: pass
    document = formats.load_document(path, config)
    return [{"id": s.id, "source": s.text, "context": s.context, "metadata": s.metadata} for s in document.segments], None


def cmd_delta(args: argparse.Namespace) -> dict[str, Any]:
    config = get_config(args)
    previous, _ = exchange_or_document_rows(core.resolve_user_path(args.previous, must_exist=True), config)
    current, package = exchange_or_document_rows(core.resolve_user_path(args.current, must_exist=True), config)
    memory = core.read_json(core.resolve_user_path(args.memory, must_exist=True)) if args.memory else None
    result = quality.change_delta(previous, current, memory)
    result["ok"] = True; result["operation"] = "delta"
    if args.reused_output:
        by_key = {row["key"]: row for row in result["changes"]}
        rows = []
        for index, row in enumerate(current):
            key = quality.change_key(row)
            change = by_key.get(key, {})
            rows.append({"id": row.get("id", f"segment-{index+1}"), "source": row.get("source", row.get("text", "")), "target": change.get("reused_target", ""), "context": row.get("context", ""), "metadata": row.get("metadata", {})})
        reused_package = {"version": 1, "source_fingerprint": (package or {}).get("source_fingerprint", core.fingerprint(core.bounded_read(Path(args.current), int(config["limits"]["max_input_bytes"])))), "source_locale": args.source_locale, "target_locale": args.target_locale, "kind": (package or {}).get("kind", "document"), "segments": rows}
        core.write_json(core.resolve_user_path(args.reused_output), reused_package)
        result["reused_output"] = args.reused_output
    return output_value(result, args)


def cmd_convert(args: argparse.Namespace) -> dict[str, Any]:
    config = get_config(args)
    input_path = core.resolve_user_path(args.input, must_exist=True)
    source_data = core.bounded_read(input_path, int(config["limits"]["max_input_bytes"]))
    document = formats.load_document(input_path, config, encoding=args.encoding, keys=split_specs(args.keys), columns=split_specs(args.columns))
    if args.operation in {"extract", "xliff"}:
        package = formats.exchange_package(document, source_data, source_locale=args.source_locale, target_locale=args.target_locale)
        if args.operation == "xliff": core.atomic_write(core.resolve_user_path(args.output), quality.to_xliff(package))
        else: core.atomic_write(core.resolve_user_path(args.output), core.json_output(package))
        result = {"ok": True, "operation": args.operation, "output": args.output, "segments": len(package["segments"]), "source_fingerprint": package["source_fingerprint"]}
    elif args.operation == "apply":
        package = core.read_json(core.resolve_user_path(args.exchange, must_exist=True))
        translations = formats.apply_exchange(document, package, source_data)
        if any(not value.strip() for value in translations.values()): raise ToolError("incomplete_exchange", "Exchange contains empty targets; complete or explicitly review them before applying.")
        rendered = core.safe_output_size(document.render(translations), config)
        ensure_output(core.resolve_user_path(args.output), source=input_path, approve_overwrite=args.approve_overwrite)
        core.atomic_write(core.resolve_user_path(args.output), rendered)
        result = {"ok": True, "operation": "apply", "output": args.output, "segments": len(translations), "review_required": True}
    else: raise ToolError("invalid_operation", "Convert operation must be extract, xliff, or apply.")
    return output_value(result, args, force_json=True)


def cmd_subtitles(args: argparse.Namespace) -> dict[str, Any]:
    config = get_config(args)
    path = core.resolve_user_path(args.input, must_exist=True)
    kind = args.format or path.suffix.lower().lstrip(".")
    raw = core.decode_text(core.bounded_read(path, int(config["limits"]["max_input_bytes"])), args.encoding)[0]
    cues = media.parse_timed_cues(raw, kind)
    if args.operation == "adjust":
        shots = []
        if args.shot_changes:
            shots_value = core.read_json(core.resolve_user_path(args.shot_changes, must_exist=True)); shots = shots_value.get("shot_changes_ms", shots_value) if isinstance(shots_value, dict) else shots_value
        output, report = media.adjust_subtitles(raw, kind, config, offset_ms=args.offset_ms, shot_changes_ms=shots, snap_threshold_ms=args.snap_threshold_ms, min_gap_ms=args.min_gap_ms)
        ensure_output(core.resolve_user_path(args.output), source=path, approve_overwrite=args.approve_overwrite)
        core.atomic_write(core.resolve_user_path(args.output), output)
        result = {"ok": True, "operation": "subtitles-adjust", "output": args.output, **report}
    elif args.operation == "qa":
        target_cues = None
        if args.target:
            target_path = core.resolve_user_path(args.target, must_exist=True)
            target_raw = core.decode_text(core.bounded_read(target_path, int(config["limits"]["max_input_bytes"])), args.encoding)[0]
            target_cues = media.parse_timed_cues(target_raw, args.format or target_path.suffix.lower().lstrip("."))
        result = {"ok": True, "operation": "subtitles-qa", **media.subtitle_qa(cues, target_cues=target_cues, config=config)}
    elif args.operation == "package":
        if not args.target: raise ToolError("missing_target", "subtitles package needs --target translated-subtitle-file.")
        target_path = core.resolve_user_path(args.target, must_exist=True)
        target_raw = core.decode_text(core.bounded_read(target_path, int(config["limits"]["max_input_bytes"])), args.encoding)[0]
        target_cues = media.parse_timed_cues(target_raw, target_path.suffix.lower().lstrip("."))
        character_map = core.read_json(core.resolve_user_path(args.characters, must_exist=True)) if args.characters else {}
        pronunciation = []
        if args.glossary: pronunciation = [{"term": row["source"], "target": row["target"], "pronunciation": row.get("pronunciation", ""), "notes": row.get("notes", "")} for row in core.load_glossary(args.glossary)]
        result = {"ok": True, "operation": "subtitles-package", **media.voiceover_package(cues, target_cues, locale=args.target_locale, title=args.title, character_map=character_map, pronunciation=pronunciation)}
    else: raise ToolError("invalid_operation", "Subtitles operation must be adjust, qa, or package.")
    return output_value(result, args, force_json=args.operation == "adjust")


def cmd_audio(args: argparse.Namespace) -> dict[str, Any]:
    config = get_config(args)
    path = core.resolve_user_path(args.input, must_exist=True)
    if args.operation == "assess": result = {"ok": True, "operation": "audio-assess", **media.analyze_wav(path, silence_db=args.silence_db)}
    elif args.operation == "prepare":
        if not args.output_dir: raise ToolError("missing_output", "audio prepare needs --output-dir.")
        result = {"ok": True, "operation": "audio-prepare", **media.prepare_wav(path, core.resolve_user_path(args.output_dir), config, chunk_seconds=args.chunk_seconds, normalize=args.normalize, trim_silence=args.trim_silence, silence_db=args.silence_db)}
    else: raise ToolError("invalid_operation", "Audio operation must be assess or prepare.")
    return output_value(result, args)


def provider_for_special_endpoint(args: argparse.Namespace, config: dict[str, Any], kind: str) -> providers.HTTPProvider:
    special = copy.deepcopy(config)
    prefix = "ULTIMATE_TRANSLATION_" + kind.upper() + "_"
    special["provider"]["endpoint_env"] = getattr(args, "endpoint_env", None) or prefix + "ENDPOINT"
    special["provider"]["model_env"] = getattr(args, "model_env", None) or prefix + "MODEL"
    return providers.HTTPProvider(special, allow_network=args.allow_network)


def cmd_transcribe(args: argparse.Namespace) -> dict[str, Any]:
    config = get_config(args)
    path = core.resolve_user_path(args.input, must_exist=True)
    if path.suffix.lower() == ".json":
        transcript = media.normalize_transcript(core.read_json(path), source_name=path.name)
        transcription_provider = "normalized-manual-sidecar"
    else:
        provider = provider_for_special_endpoint(args, config, "transcription")
        transcript = media.normalize_transcript(provider.transcribe(path, language=args.source, diarize=args.diarize, verbatim=args.verbatim), source_name=path.name)
        transcription_provider = "http"
    translation_summary = None
    if args.target:
        if not transcript["segments"]: raise ToolError("invalid_transcript", "Speech translation needs timestamped transcript segments; request verbose transcription or edit the JSON sidecar.")
        segments = [Segment(row["id"], row["text"], context=f"{row['speaker']} {row['start']}-{row['end']}", metadata={"speaker": row["speaker"], "start": row["start"], "end": row["end"]}) for row in transcript["segments"]]
        terms = selected_terms(args, args.source, args.target)
        translation_provider = selected_provider(args, config, terms, force=args.provider)
        engine = core.TranslationEngine(translation_provider, config, source_locale=args.source, target_locale=args.target, options=translation_options(args), checkpoint_path=core.resolve_user_path(args.output + ".translation-checkpoint.json"), resume=args.resume)
        translated = engine.run(segments, core.json_output(transcript))
        for row in transcript["segments"]:
            value = translated[row["id"]]
            row["source_text"] = row["text"]; row["text"] = value.text; row["translation_confidence"] = value.confidence; row["translation_uncertainties"] = value.uncertainties; row["review_required"] = True
        transcript["translated_text"] = "\n".join(row["text"] for row in transcript["segments"])
        transcript["target_locale"] = args.target
        translation_summary = {"provider": translation_provider.name, "segments": len(segments), "review_required": True}
    if args.format == "srt": payload = media.transcript_srt(transcript)
    elif args.format == "md": payload = media.transcript_markdown(transcript, title=args.title)
    elif args.format == "vtt": payload = media.transcript_vtt(transcript)
    else: payload = core.json_output({"ok": True, "operation": "transcribe", "transcript": transcript, "transcription_provider": transcription_provider, "translation": translation_summary})
    core.atomic_write(core.resolve_user_path(args.output), payload)
    result = {"ok": True, "operation": "transcribe", "output": args.output, "format": args.format, "segments": len(transcript["segments"]), "speakers": transcript["speakers"], "translation": translation_summary, "review_required": True}
    sys.stdout.write(core.json_output(result)); return result


def cmd_ocr(args: argparse.Namespace) -> dict[str, Any]:
    config = get_config(args)
    path = core.resolve_user_path(args.input, must_exist=True)
    provider = provider_for_special_endpoint(args, config, "vision")
    result = provider.ocr_image(path, target_locale=args.target)
    for block in result.get("blocks", []):
        if args.target and block.get("translation"):
            missing, added = quality.compare_quantities(block["text"], block["translation"])
            if missing or added:
                block.setdefault("uncertainties", []).append("OCR translation quantity mismatch requires review.")
                block["quantity_qa"] = {"missing": missing, "added": added}
    result["ok"] = True; result["operation"] = "ocr"
    return output_value(result, args)


def cmd_visual(args: argparse.Namespace) -> dict[str, Any]:
    config = get_config(args)
    path = core.resolve_user_path(args.input, must_exist=True)
    ocr = core.read_json(path)
    errors = core.validate_json_schema(ocr, core.read_json(BASE_DIR / "asset-ocr-schema.json"))
    if errors: raise ToolError("invalid_ocr", "OCR sidecar does not match asset-ocr-schema.json.", details=errors[:100])
    if args.target and args.translate_blocks:
        segments = [Segment(str(row.get("id", f"block-{i+1}")), row["text"], context=str(row.get("kind", "visual")), metadata={"bbox": row["bbox"], "kind": row.get("kind")}) for i, row in enumerate(ocr["blocks"])]
        terms = selected_terms(args, args.source, args.target)
        provider = selected_provider(args, config, terms)
        engine = core.TranslationEngine(provider, config, source_locale=args.source, target_locale=args.target, options=translation_options(args), checkpoint_path=core.resolve_user_path(str(args.output or "visual-plan.json") + ".checkpoint.json"), resume=args.resume)
        translated = engine.run(segments, core.json_output(ocr))
        for row in ocr["blocks"]:
            row["translation"] = translated[str(row.get("id", f"block-{ocr['blocks'].index(row)+1}"))].text
    result = {"ok": True, "operation": "visual", **media.visual_plan(ocr, target_locale=args.target, max_expansion_ratio=args.max_expansion_ratio), "domain": args.domain}
    return output_value(result, args)


def cmd_font(args: argparse.Namespace) -> dict[str, Any]:
    if args.text is not None:
        text = args.text
    elif args.input_text:
        text_path = core.resolve_user_path(args.input_text, must_exist=True)
        text = core.decode_text(core.bounded_read(text_path, int(get_config(args)["limits"]["max_input_bytes"])))[0]
    else:
        raise ToolError("missing_input", "font needs --text or --text-file with representative localized content.")
    result = {"ok": True, "operation": "font", **media.font_audit(core.resolve_user_path(args.input, must_exist=True), text, locales=split_specs(args.locales))}
    return output_value(result, args)


def cmd_locale(args: argparse.Namespace) -> dict[str, Any]:
    if args.operation == "plan": result = {"ok": True, "operation": "locale-plan", **project.locale_coverage(split_specs(args.locales), core.read_json(BASE_DIR / "asset-locales.json"))}
    elif args.operation == "plural": result = {"ok": True, "operation": "locale-plural", **project.plural_test_suite(args.locale, split_specs(args.values))}
    elif args.operation == "message":
        text = read_text_argument(args)
        result = {"ok": True, "operation": "locale-message", **project.validate_message_format(text), **project.icu_test_suite(text, args.locale)}
        if args.values_json:
            try: arguments = json.loads(args.values_json)
            except json.JSONDecodeError as exc: raise ToolError("invalid_json", "--values-json must be a JSON object.", details=str(exc)) from exc
            if not isinstance(arguments, dict): raise ToolError("invalid_json", "--values-json must be a JSON object.")
            result["arguments"] = arguments
            result["rendered"] = project.render_icu_message(text, args.locale, arguments)
            result["rendering_limitations"] = "Variables, plural, select, selectordinal (en/other-only), #, and nesting are supported. Locale date/time/number formatting is delegated to the host ICU runtime."
    else: raise ToolError("invalid_operation", "Locale operation must be plan, plural, or message.")
    return output_value(result, args)


def cmd_plan(args: argparse.Namespace) -> dict[str, Any]:
    config = get_config(args)
    if args.input:
        path = core.resolve_user_path(args.input, must_exist=True)
        if path.suffix.lower() == ".json":
            try:
                scope = core.read_json(path)
                if not isinstance(scope, dict) or not any(key in scope for key in {"scope", "words", "segments"}): scope = formats.probe_file(path, config)
            except ToolError: scope = formats.probe_file(path, config)
        else: scope = formats.probe_file(path, config)
    else: scope = {"words": args.words or 0, "segments": args.segments or 0, "subtitle_minutes": args.subtitle_minutes, "audio_hours": args.audio_hours, "screenshots": args.screenshots}
    locales = split_specs(args.locales)
    if not locales: raise ToolError("missing_locales", "--locales is required.")
    result = {"ok": True, "operation": "plan", **project.project_plan(scope, locales, deadline=args.deadline, domain=args.domain, content_type=args.content_type, vendors=split_specs(args.vendors))}
    if args.capability:
        registry = catalog.load_registry(BASE_DIR); selected = catalog.find_capability(registry, args.capability); result["selected_capability"] = selected
    return output_value(result, args)


def cmd_project(args: argparse.Namespace) -> dict[str, Any]:
    operation = args.operation
    if operation == "init":
        path = core.resolve_user_path(args.project)
        if path.exists(): raise ToolError("output_exists", "Project state already exists.")
        if not args.name: raise ToolError("missing_input", "project init needs --name and --locales.")
        state = project.new_project(args.name, split_specs(args.locales), owner=args.owner)
        core.write_json(path, state)
        result = {"ok": True, "operation": operation, "project": str(path), "revision": 0}
    else:
        path = core.resolve_user_path(args.project, must_exist=operation not in {"release", "sample", "feedback"})
        state = core.read_json(path) if path.exists() else None
        if operation == "update":
            event = core.read_json(core.resolve_user_path(args.event, must_exist=True))
            state = project.apply_project_event(state, event, expected_revision=args.expected_revision, confirm_sensitive=args.confirm_sensitive)
            backup = Path(str(path) + ".backup"); shutil.copyfile(path, backup); core.write_json(path, state)
            result = {"ok": True, "operation": operation, "revision": state["revision"], "backup": str(backup), "status": state["status"]}
        elif operation == "status":
            result = {"ok": True, "operation": operation, "project": {key: value for key, value in state.items() if key != "events"}, "metrics": project.project_metrics(state)}
        elif operation == "metrics": result = {"ok": True, "operation": operation, **project.project_metrics(state)}
        elif operation == "feedback":
            feedback = state.get("feedback", []) if state else core.read_json(core.resolve_user_path(args.input, must_exist=True))
            result = {"ok": True, "operation": operation, **project.consolidate_feedback(feedback)}
        elif operation == "sample":
            records = state.get("assets", []) if state else core.read_json(core.resolve_user_path(args.input, must_exist=True))
            config = get_config(args); review = config["review"]
            result = {"ok": True, "operation": operation, **project.sampling_plan(records, sample_rate=args.sample_rate or review["sample_rate"], high_risk_rate=review["high_risk_sample_rate"], seed=review["seed"])}
        elif operation == "handoff":
            files = [core.resolve_user_path(value, must_exist=True) for value in args.files or []]
            result = {"ok": True, "operation": operation, **project.build_handoff(core.resolve_user_path(args.output), project=state, files=files, glossary=Path(args.glossary) if args.glossary else None, style_guide=Path(args.style) if args.style else None, instructions=args.instructions, acceptance=split_specs(args.acceptance), archive=args.archive)}
        elif operation == "release":
            manifest_path = core.resolve_user_path(args.manifest, must_exist=True)
            manifest = core.read_json(manifest_path)
            result = {"ok": True, "operation": operation, **project.release_audit(manifest, base_dir=manifest_path.parent)}
        else: raise ToolError("invalid_operation", f"Unsupported project operation: {operation}")
    return output_value(result, args, force_json=operation == "handoff")


def cmd_repository(args: argparse.Namespace) -> dict[str, Any]:
    config = get_config(args)
    result = {"ok": True, "operation": "repository", **project.scan_repository(core.resolve_user_path(args.input, must_exist=True, kind="directory"), config, include_patterns=split_specs(args.include))}
    return output_value(result, args)


def cmd_secure(args: argparse.Namespace) -> dict[str, Any]:
    config = get_config(args)
    if args.operation in {"scan", "redact"}:
        path = core.resolve_user_path(args.input, must_exist=True)
        raw = core.decode_text(core.bounded_read(path, int(config["limits"]["max_input_bytes"])), args.encoding)[0]
        if args.operation == "scan": findings = project.secret_scan(raw, include_email=not args.exclude_email); result = {"ok": not any(row["severity"] == "critical" for row in findings), "operation": "secure-scan", "input": str(path), "findings": findings, "summary": {"findings": len(findings), "critical": sum(row["severity"] == "critical" for row in findings)}, "limitations": "Pattern scanning does not prove absence of secrets or personal data. Values are fingerprinted, not reproduced."}
        else:
            redacted, findings = project.redact_text(raw, include_email=args.include_email)
            output = core.resolve_user_path(args.output)
            if output.resolve() == path.resolve(): raise ToolError("approval_required", "Secure redaction never replaces the source; choose a separate output path.")
            ensure_output(output, approve_overwrite=args.approve_overwrite); core.atomic_write(output, redacted)
            result = {"ok": True, "operation": "secure-redact", "output": str(output), "redactions": len(findings), "findings": findings, "review_required": True}
    elif args.operation == "cleanup":
        if not args.paths: raise ToolError("missing_input", "secure cleanup needs at least one --paths file.")
        result = {"ok": True, "operation": "secure-cleanup", **project.secure_cleanup([core.resolve_user_path(value) for value in args.paths], approve_delete=args.approve_delete)}
    else: raise ToolError("invalid_operation", "Secure operation must be scan, redact, or cleanup.")
    return output_value(result, args, force_json=args.operation in {"redact", "cleanup"})


def cmd_web(args: argparse.Namespace) -> dict[str, Any]:
    mappings = []
    for value in args.localized:
        if "=" not in value: raise ToolError("invalid_option", "--localized expects locale=directory.")
        locale, directory = value.split("=", 1); mappings.append((locale, core.resolve_user_path(directory, must_exist=True, kind="directory")))
    result = {"ok": True, "operation": "web", **project.build_static_site_package(core.resolve_user_path(args.input, must_exist=True, kind="directory"), mappings, core.resolve_user_path(args.output_dir), default_locale=args.default_locale)}
    return output_value(result, args)


def cmd_narrative(args: argparse.Namespace) -> dict[str, Any]:
    config = get_config(args)
    script = core.read_json(core.resolve_user_path(args.input, must_exist=True))
    result = {"ok": True, "operation": "narrative", **project.narrative_audit(script)}
    if args.provider_review:
        provider = selected_provider(args, config, [], force="http")
        response = provider.chat_json(config["prompts"]["narrative-review"], json.dumps({"script": script, "target_locale": args.target_locale, "local_audit": result["defects"]}, ensure_ascii=False), operation="narrative-review")
        if not isinstance(response.get("findings"), list): raise ToolError("provider_response_invalid", "Narrative review needs a findings array.")
        result["provider_review"] = response; result["review_required"] = True
    return output_value(result, args)


ADAPT_REQUIRED = {
    "transcreate": "variants", "campaign": "variants", "cultural-review": "findings", "inclusive-review": "findings",
    "back-translate": "back_translation", "gloss": "segments", "learning": "exercises", "sign-language": "interpreted_script",
    "dubbing": "lines", "lip-sync": "lines", "meeting": "action_items", "pronunciation": "entries", "narrative-review": "findings", "bilingual-review": "assessments",
}


def cmd_adapt(args: argparse.Namespace) -> dict[str, Any]:
    config = get_config(args)
    operation = args.operation
    if operation not in ADAPT_REQUIRED: raise ToolError("invalid_operation", f"Unsupported adapt operation. Choose: {', '.join(sorted(ADAPT_REQUIRED))}")
    input_path = core.resolve_user_path(args.input, must_exist=True)
    raw = core.decode_text(core.bounded_read(input_path, int(config["limits"]["max_input_bytes"])), args.encoding)[0]
    try: content = json.loads(raw) if input_path.suffix.lower() == ".json" else raw
    except json.JSONDecodeError as exc: raise ToolError("invalid_json", "Adapt JSON input is invalid.", details=str(exc)) from exc
    original = None
    if args.original:
        original_path = core.resolve_user_path(args.original, must_exist=True)
        original = core.decode_text(core.bounded_read(original_path, int(config["limits"]["max_input_bytes"])), args.encoding)[0]
    provider = selected_provider(args, config, [], force="http")
    prompt_key = "transcreate" if operation == "campaign" else operation
    request = {"operation": operation, "content": content, "original_source": original, "source_locale": args.source_locale, "target_locale": args.target_locale, "audience": args.audience, "domain": args.domain, "variants": args.variants, "style": load_style(args.style) if args.style else load_style(str(BASE_DIR / "asset-style-guide.json")), "channel": args.channel, "max_length": args.max_length, "review_policy": "Return explicit uncertainty and require qualified human review; never claim publication readiness."}
    response = provider.chat_json(config["prompts"].get(prompt_key, config["prompts"]["transcreate"]), json.dumps(request, ensure_ascii=False), operation=operation)
    required = ADAPT_REQUIRED[operation]
    if required not in response: raise ToolError("provider_response_invalid", f"{operation} response lacks required {required} field.")
    if required in {"variants", "findings", "segments", "exercises", "lines", "action_items", "entries", "assessments"} and not isinstance(response[required], list):
        raise ToolError("provider_response_invalid", f"{operation} field {required} must be an array.")
    if required in {"back_translation", "interpreted_script"} and (not isinstance(response[required], str) or not response[required].strip()):
        raise ToolError("provider_response_invalid", f"{operation} field {required} must be a non-empty string.")
    if operation in {"transcreate", "campaign"}:
        if not isinstance(response[required], list) or not response[required]: raise ToolError("provider_response_invalid", "Transcreation variants must be a non-empty array.")
        source_text = original if isinstance(original, str) else raw
        for index, variant in enumerate(response[required]):
            if not isinstance(variant, dict) or not isinstance(variant.get("text"), str): raise ToolError("provider_response_invalid", "Each variant needs text.")
            if args.max_length and len(variant["text"]) > args.max_length: variant["length_defect"] = {"characters": len(variant["text"]), "limit": args.max_length}
            missing, added = quality.compare_quantities(source_text, variant["text"])
            variant["quantity_differences"] = {"source_missing_in_variant": missing, "variant_added": added, "review_required": bool(missing or added)}
    result = {"ok": True, "operation": operation, "provider": "http", "result": response, "review_required": True, "limitations": "Provider generation/review is not independent human approval. Validate meaning, claims, cultural fit, pedagogy, timing, or narrative evidence as applicable before use."}
    return output_value(result, args)


def cmd_validate(args: argparse.Namespace) -> dict[str, Any]:
    operation = args.operation
    if operation == "config":
        config = get_config(args); result = {"ok": True, "operation": operation, "valid": True, "provider": config["provider"]["name"], "network_allowed": config["translation"]["allow_network"]}
    elif operation == "schema":
        instance = core.read_json(core.resolve_user_path(args.input, must_exist=True)); schema = core.read_json(core.resolve_user_path(args.schema, must_exist=True))
        errors = core.validate_json_schema(instance, schema); result = {"ok": not errors, "operation": operation, "valid": not errors, "errors": errors, "supported_subset": "type, required, properties, items, enum, additionalProperties, min/max items/length, pattern, minimum/maximum"}
    elif operation == "exchange":
        value = core.read_json(core.resolve_user_path(args.input, must_exist=True)); errors = core.validate_json_schema(value, core.read_json(BASE_DIR / "asset-exchange-schema.json")); result = {"ok": not errors, "operation": operation, "valid": not errors, "errors": errors}
    elif operation == "handoff":
        path = core.resolve_user_path(args.input, must_exist=True)
        try:
            with zipfile.ZipFile(path) as archive:
                names = archive.namelist(); bad = archive.testzip(); nested = [name for name in names if Path(name).suffix.lower() in {".zip", ".7z", ".rar", ".tar", ".gz", ".bz2", ".xz"}]
                unsafe = [name for name in names if name.startswith(("/", "\\")) or ".." in Path(name).parts]
                manifest = json.loads(archive.read("handoff.json")) if "handoff.json" in names else None
        except (zipfile.BadZipFile, KeyError, json.JSONDecodeError) as exc: raise ToolError("invalid_handoff", "Could not validate handoff archive.", details=str(exc)) from exc
        result = {"ok": not bad and not nested and not unsafe and manifest is not None, "operation": operation, "entries": len(names), "crc_error": bad, "nested_archives": nested, "unsafe_paths": unsafe, "manifest_present": manifest is not None}
    elif operation == "ci":
        report = core.read_json(core.resolve_user_path(args.input, must_exist=True))
        summary = report.get("summary", {})
        blocking = int(summary.get("blocking_defects", summary.get("blocking", 0)))
        reviews = int(summary.get("review_findings", 0))
        release_ready = summary.get("release_ready")
        failed = blocking > 0 or (args.fail_on_review and reviews > 0) or (args.require_release_ready and release_ready is not True)
        result = {"ok": not failed, "operation": operation, "passed": not failed, "blocking_defects": blocking, "review_findings": reviews, "release_ready": release_ready, "gate": {"fail_on_review": args.fail_on_review, "require_release_ready": args.require_release_ready}}
        if failed: raise ToolError("ci_gate_failed", "Localization CI gate failed.", details=result, hint="Correct blocking defects and required reviews, then rerun the real QA/release report.")
    else: raise ToolError("invalid_operation", "Validate operation must be config, schema, exchange, handoff, or ci.")
    return output_value(result, args)


def add_config(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--config", help="JSON configuration override; defaults to sibling asset-default-config.json")


def add_output(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--output", help="Output file path; omit for stdout where supported")


def add_document_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--encoding", default="auto")
    parser.add_argument("--keys", action="append", help="Comma-separated JSON/YAML dotted paths or glob patterns")
    parser.add_argument("--columns", action="append", help="Comma-separated CSV/TSV column names or zero-based indices")


def add_locales(parser: argparse.ArgumentParser, *, target_required: bool = False) -> None:
    parser.add_argument("--source", default="auto", help="Source locale or auto")
    parser.add_argument("--target", required=target_required, help="Target locale, for example es-MX")


def add_provider(parser: argparse.ArgumentParser, *, default: str | None = None) -> None:
    parser.add_argument("--provider", choices=["glossary", "http", "pseudo"], default=default)
    parser.add_argument("--glossary", help="Approved CSV or JSON glossary/termbase")
    parser.add_argument("--allow-network", action="store_true", help="Approve bounded transmission to the configured provider")


def add_translation_options(parser: argparse.ArgumentParser) -> None:
    add_config(parser); add_locales(parser, target_required=True); add_provider(parser)
    parser.add_argument("--memory", help="Approved JSON translation memory")
    parser.add_argument("--style", help="JSON brand/style policy")
    parser.add_argument("--tone", choices=["literal", "natural", "formal", "casual", "academic", "technical", "legal", "friendly", "persuasive", "child-friendly", "easy-language"])
    parser.add_argument("--mode", choices=["literal", "natural", "audience-adapted"])
    parser.add_argument("--audience"); parser.add_argument("--domain", default="general"); parser.add_argument("--brand-voice")
    parser.add_argument("--reading-level"); parser.add_argument("--channel"); parser.add_argument("--character-voice"); parser.add_argument("--purpose")
    parser.add_argument("--vernacular", action="store_true"); parser.add_argument("--accessibility", action="store_true")
    parser.add_argument("--dubbing", action="store_true"); parser.add_argument("--lip-sync", action="store_true")
    parser.add_argument("--set", action="append", metavar="KEY=VALUE", help="Additional bounded provider translation option")
    parser.add_argument("--allow-unit-conversion", action="store_true")
    parser.add_argument("--report"); parser.add_argument("--checkpoint"); parser.add_argument("--resume", action="store_true")
    parser.add_argument("--approve-overwrite", action="store_true", help="Approve output replacement; a .backup is created")
    add_document_options(parser)


def build_parser() -> Parser:
    parser = Parser(prog="tools-translate.py", description="Unified translation, localization, media-preparation, QA, review, and governance tool for all 144 catalog capabilities.", epilog="Examples:\n  bash -lc 'python3 ultimate-translation/tools-translate.py capabilities --format text'\n  bash -lc 'python3 ultimate-translation/tools-translate.py translate input.md output.md --target es --glossary ultimate-translation/asset-glossary.csv'\n  bash -lc 'python3 ultimate-translation/tools-translate.py pseudolocalize input.json output.json --target qps-ploc'\n", formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--version", action="version", version=f"%(prog)s {core.VERSION}")
    parser.add_argument("--debug", action="store_true", help="Show traceback for unexpected errors")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("capabilities", help="List and preflight all 144 supplied capabilities")
    p.add_argument("--category"); p.add_argument("--query"); p.add_argument("--format", choices=["json", "text"], default="json"); add_output(p); p.set_defaults(handler=cmd_capabilities)

    for name, handler, help_text in (("translate", cmd_translate, "Translate a supported editable file with checkpoints and QA"), ("pseudolocalize", cmd_pseudolocalize, "Accented/expanded/bracketed/BIDI stress testing")):
        p = sub.add_parser(name, help=help_text); p.add_argument("input"); p.add_argument("output"); add_translation_options(p)
        if name == "pseudolocalize":
            p.add_argument("--pseudo-expansion", type=float, default=0.35); p.add_argument("--pseudo-accents", action=argparse.BooleanOptionalAction, default=True); p.add_argument("--pseudo-brackets", action=argparse.BooleanOptionalAction, default=True); p.add_argument("--pseudo-bidi", action="store_true")
        p.set_defaults(handler=handler)

    p = sub.add_parser("batch", help="Resumable mixed-file batch pipeline")
    p.add_argument("input", help="Input directory"); p.add_argument("--output-dir", required=True); add_translation_options(p); p.set_defaults(handler=cmd_batch)

    p = sub.add_parser("detect", help="Script/language detection and translation-ready segmentation")
    p.add_argument("input"); p.add_argument("--encoding", default="auto"); p.add_argument("--target-locale"); add_config(p); add_output(p); p.set_defaults(handler=cmd_detect)

    p = sub.add_parser("transliterate", help="Configurable native-script transliteration and reversibility checks")
    p.add_argument("--input"); p.add_argument("--text"); p.add_argument("--scheme", default="cyrillic-latin-basic"); p.add_argument("--mapping"); p.add_argument("--reverse", action="store_true"); p.add_argument("--encoding", default="auto"); add_config(p); add_output(p); p.set_defaults(handler=cmd_transliterate)

    p = sub.add_parser("inspect", help="Universal file intake and localization scope estimate")
    p.add_argument("input"); add_config(p); add_document_options(p); add_output(p); p.set_defaults(handler=cmd_inspect)

    p = sub.add_parser("align", help="Parallel bilingual, learning, CSV, Markdown, or TMX output")
    p.add_argument("--source", required=True); p.add_argument("--target", required=True); p.add_argument("--source-locale", required=True); p.add_argument("--target-locale", required=True); p.add_argument("--granularity", choices=["segment", "sentence", "paragraph", "table-row", "timecode"], default="segment"); p.add_argument("--learning", action="store_true"); p.add_argument("--glossary"); add_config(p); add_document_options(p); add_output(p); p.set_defaults(handler=cmd_align)

    p = sub.add_parser("terms", help="Term extraction, glossary validation/checking, style and pronunciation")
    p.add_argument("operation", choices=["extract", "validate", "check", "style", "pronunciation"]); p.add_argument("--input"); p.add_argument("--source"); p.add_argument("--target"); p.add_argument("--source-locale", default="auto"); p.add_argument("--target-locale", default="und"); p.add_argument("--glossary"); p.add_argument("--style"); p.add_argument("--max-terms", type=int, default=100); p.add_argument("--encoding", default="auto"); p.add_argument("--keys", action="append"); p.add_argument("--columns", action="append"); add_config(p); add_output(p); p.set_defaults(handler=cmd_terms)

    p = sub.add_parser("memory", help="Build, clean, or export translation memory")
    p.add_argument("operation", choices=["build", "clean", "tmx"]); p.add_argument("--input"); p.add_argument("--source"); p.add_argument("--target"); p.add_argument("--source-locale", required=True); p.add_argument("--target-locale", required=True); p.add_argument("--status", choices=["draft", "approved", "reviewed"], default="draft"); p.add_argument("--origin", default="aligned"); p.add_argument("--confirm-sensitive", action="store_true"); p.add_argument("--encoding", default="auto"); p.add_argument("--keys", action="append"); p.add_argument("--columns", action="append"); add_config(p); add_output(p); p.set_defaults(handler=cmd_memory)

    p = sub.add_parser("qa", help="Deterministic bilingual QA and accessibility checks")
    p.add_argument("--source", required=True); p.add_argument("--target", required=True); p.add_argument("--source-locale", required=True); p.add_argument("--target-locale", required=True); p.add_argument("--glossary"); p.add_argument("--translation-report"); p.add_argument("--domain", default="general"); p.add_argument("--allow-unit-conversion", action="store_true"); p.add_argument("--accessibility", action="store_true"); p.add_argument("--encoding", default="auto"); p.add_argument("--keys", action="append"); p.add_argument("--columns", action="append"); add_config(p); add_output(p); p.set_defaults(handler=cmd_qa)

    p = sub.add_parser("review", help="Apply accept/reject/escalate decisions to aligned QA output")
    p.add_argument("input", help="QA JSON report"); p.add_argument("--decisions", required=True); add_output(p); p.set_defaults(handler=cmd_review)

    p = sub.add_parser("delta", help="Source change detection, translation reuse, and regression analysis")
    p.add_argument("--previous", required=True); p.add_argument("--current", required=True); p.add_argument("--memory"); p.add_argument("--source-locale", required=True); p.add_argument("--target-locale", required=True); p.add_argument("--reused-output"); add_config(p); add_output(p); p.set_defaults(handler=cmd_delta)

    p = sub.add_parser("convert", help="Translation-safe exchange extraction/application and XLIFF")
    p.add_argument("operation", choices=["extract", "xliff", "apply"]); p.add_argument("input"); p.add_argument("output"); p.add_argument("--exchange"); p.add_argument("--source-locale", required=True); p.add_argument("--target-locale", required=True); p.add_argument("--approve-overwrite", action="store_true"); p.add_argument("--encoding", default="auto"); p.add_argument("--keys", action="append"); p.add_argument("--columns", action="append"); add_config(p); p.set_defaults(handler=cmd_convert)

    p = sub.add_parser("subtitles", help="Subtitle timing/QA and voice-over packaging")
    p.add_argument("operation", choices=["adjust", "qa", "package"]); p.add_argument("input"); p.add_argument("--target", help="Translated subtitle file"); p.add_argument("--format", choices=["srt", "vtt", "ass", "ssa"]); p.add_argument("--encoding", default="auto"); p.add_argument("--offset-ms", type=int, default=0); p.add_argument("--shot-changes"); p.add_argument("--snap-threshold-ms", type=int, default=120); p.add_argument("--min-gap-ms", type=int, default=80); p.add_argument("--approve-overwrite", action="store_true"); p.add_argument("--target-locale", default="und"); p.add_argument("--title", default="Localized production"); p.add_argument("--characters"); p.add_argument("--glossary"); add_config(p); add_output(p); p.set_defaults(handler=cmd_subtitles)

    p = sub.add_parser("audio", help="PCM/WAV assessment, cleanup, and chunk preparation")
    p.add_argument("operation", choices=["assess", "prepare"]); p.add_argument("input"); p.add_argument("--output-dir"); p.add_argument("--chunk-seconds", type=float, default=30); p.add_argument("--normalize", action="store_true"); p.add_argument("--trim-silence", action="store_true"); p.add_argument("--silence-db", type=float, default=-45); add_config(p); add_output(p); p.set_defaults(handler=cmd_audio)

    p = sub.add_parser("transcribe", help="Configurable speech transcription/diarization and speech translation")
    p.add_argument("input", help="Audio file or manually corrected transcript JSON"); p.add_argument("output"); p.add_argument("--format", choices=["json", "srt", "vtt", "md"], default="json"); p.add_argument("--source", default="auto"); p.add_argument("--target"); p.add_argument("--diarize", action="store_true"); p.add_argument("--verbatim", choices=["clean", "verbatim", "detailed"], default="clean"); p.add_argument("--provider", choices=["glossary", "http"], default="http"); p.add_argument("--glossary"); p.add_argument("--allow-network", action="store_true"); p.add_argument("--endpoint-env"); p.add_argument("--model-env"); p.add_argument("--resume", action="store_true"); p.add_argument("--title", default="Transcript"); p.add_argument("--domain", default="general"); p.add_argument("--set", action="append"); add_config(p); p.set_defaults(handler=cmd_transcribe)

    p = sub.add_parser("ocr", help="Configurable vision OCR with boxes and optional visual translation")
    p.add_argument("input"); p.add_argument("--target"); p.add_argument("--allow-network", action="store_true"); p.add_argument("--endpoint-env"); p.add_argument("--model-env"); add_config(p); add_output(p); p.set_defaults(handler=cmd_ocr)

    p = sub.add_parser("visual", help="OCR sidecar translation and layer/layout reconstruction plan")
    p.add_argument("input"); p.add_argument("--source", default="auto"); p.add_argument("--target", default="und"); p.add_argument("--translate-blocks", action="store_true"); p.add_argument("--provider", choices=["glossary", "http", "pseudo"]); p.add_argument("--glossary"); p.add_argument("--allow-network", action="store_true"); p.add_argument("--max-expansion-ratio", type=float, default=1.5); p.add_argument("--domain", default="visual"); p.add_argument("--resume", action="store_true"); p.add_argument("--set", action="append"); add_config(p); add_output(p); p.set_defaults(handler=cmd_visual)

    p = sub.add_parser("font", help="Glyph coverage, shaping tables, vertical metrics, and license flags")
    p.add_argument("input"); p.add_argument("--text"); p.add_argument("--text-file", dest="input_text"); p.add_argument("--locales"); add_config(p); add_output(p); p.set_defaults(handler=cmd_font)

    p = sub.add_parser("locale", help="Locale coverage, plural/gender test planning, and ICU checks")
    p.add_argument("operation", choices=["plan", "plural", "message"]); p.add_argument("--locales"); p.add_argument("--locale", default="en"); p.add_argument("--values"); p.add_argument("--values-json", help="JSON arguments for bounded ICU message rendering"); p.add_argument("--input"); p.add_argument("--text"); add_output(p); p.set_defaults(handler=cmd_locale)

    p = sub.add_parser("plan", help="Localization scope, routing, milestones, and review design")
    p.add_argument("--input"); p.add_argument("--locales", required=True); p.add_argument("--capability"); p.add_argument("--deadline"); p.add_argument("--domain", default="general"); p.add_argument("--content-type", default="text"); p.add_argument("--vendors"); p.add_argument("--words", type=int); p.add_argument("--segments", type=int); p.add_argument("--subtitle-minutes", type=float, default=0); p.add_argument("--audio-hours", type=float, default=0); p.add_argument("--screenshots", type=int, default=0); add_config(p); add_output(p); p.set_defaults(handler=cmd_plan)

    p = sub.add_parser("project", help="Stateful tracking, governance, handoff, metrics, sampling, and release")
    p.add_argument("operation", choices=["init", "update", "status", "metrics", "feedback", "sample", "handoff", "release"]); p.add_argument("--project", required=True); p.add_argument("--name"); p.add_argument("--locales"); p.add_argument("--owner", default="UNASSIGNED"); p.add_argument("--event"); p.add_argument("--expected-revision", type=int); p.add_argument("--confirm-sensitive", action="store_true"); p.add_argument("--input"); p.add_argument("--sample-rate", type=float); p.add_argument("--files", nargs="*"); p.add_argument("--glossary"); p.add_argument("--style"); p.add_argument("--instructions", default="Preserve stable IDs and return editable localized assets with QA, review notes, decisions, and exceptions."); p.add_argument("--acceptance", default="No blocking defects,All requested locales present,Accountable approvals recorded"); p.add_argument("--archive", action="store_true"); p.add_argument("--manifest"); add_config(p); add_output(p); p.set_defaults(handler=cmd_project)

    p = sub.add_parser("repository", help="Static resource, hard-coded text, missing-key, and secret scan")
    p.add_argument("input"); p.add_argument("--include", action="append"); add_config(p); add_output(p); p.set_defaults(handler=cmd_repository)

    p = sub.add_parser("secure", help="Secret scanning, redaction, and explicitly approved cleanup")
    p.add_argument("operation", choices=["scan", "redact", "cleanup"]); p.add_argument("--input"); p.add_argument("--paths", nargs="*", default=[]); p.add_argument("--encoding", default="auto"); p.add_argument("--exclude-email", action="store_true"); p.add_argument("--include-email", action="store_true"); p.add_argument("--approve-overwrite", action="store_true"); p.add_argument("--approve-delete", action="store_true"); add_config(p); add_output(p); p.set_defaults(handler=cmd_secure)

    p = sub.add_parser("web", help="Static-site locale folders, route map, hreflang and fallback handoff")
    p.add_argument("input"); p.add_argument("--localized", action="append", required=True, help="locale=directory"); p.add_argument("--output-dir", required=True); p.add_argument("--default-locale", required=True); add_output(p); p.set_defaults(handler=cmd_web)

    p = sub.add_parser("narrative", help="Branching narrative graph and optional provider continuity review")
    p.add_argument("input"); p.add_argument("--provider-review", action="store_true"); p.add_argument("--target-locale", default="und"); p.add_argument("--allow-network", action="store_true"); add_config(p); add_output(p); p.set_defaults(handler=cmd_narrative)

    p = sub.add_parser("adapt", help="Provider-backed transcreation, review, glossing, learning, dubbing, and summaries")
    p.add_argument("operation", choices=sorted(ADAPT_REQUIRED)); p.add_argument("input"); p.add_argument("--original"); p.add_argument("--source-locale", default="auto"); p.add_argument("--target-locale", required=True); p.add_argument("--audience"); p.add_argument("--domain", default="general"); p.add_argument("--variants", type=int, default=3); p.add_argument("--style"); p.add_argument("--channel"); p.add_argument("--max-length", type=int); p.add_argument("--allow-network", action="store_true", required=False); p.add_argument("--encoding", default="auto"); add_config(p); add_output(p); p.set_defaults(handler=cmd_adapt)

    p = sub.add_parser("validate", help="Configuration, schema, exchange, handoff, and CI gates")
    p.add_argument("operation", choices=["config", "schema", "exchange", "handoff", "ci"]); p.add_argument("--input"); p.add_argument("--schema"); p.add_argument("--fail-on-review", action="store_true"); p.add_argument("--require-release-ready", action="store_true"); add_config(p); add_output(p); p.set_defaults(handler=cmd_validate)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = argparse.Namespace(debug=False)
    try:
        args = parser.parse_args(argv)
        result = args.handler(args)
        if args.command == "batch" and isinstance(result, dict) and not result.get("ok", True): return 2
        return 0
    except ToolError as exc:
        sys.stderr.write(core.json_output(core.error_result(exc)))
        return 3 if exc.code in {"approval_required", "network_approval_required"} else 5 if exc.code == "ci_gate_failed" else 2
    except Exception as exc:
        sys.stderr.write(core.json_output(core.error_result(exc)))
        if getattr(args, "debug", False): raise
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
