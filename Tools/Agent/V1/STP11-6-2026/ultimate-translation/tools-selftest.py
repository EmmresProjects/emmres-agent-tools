#!/usr/bin/env python3
"""Repeatable offline and loopback integration tests for the production Skill Tool.

Tests use temporary directories only. No live external service, credential, shell command,
or machine-specific font path is required. Run: python3 ultimate-translation/tools-selftest.py -v
"""
from __future__ import annotations

import argparse
import copy
import csv
import io
import json
import math
import os
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import threading
import unittest
import wave
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

BASE_DIR = Path(__file__).resolve().parent
CLI = BASE_DIR / "tools-translate.py"
GLOSSARY = BASE_DIR / "asset-glossary.csv"


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def make_ooxml(path: Path, kind: str, text: str = "Save") -> None:
    if kind == "docx":
        entries = {
            "[Content_Types].xml": "<Types xmlns=\"http://schemas.openxmlformats.org/package/2006/content-types\"/>",
            "word/document.xml": f"<w:document xmlns:w=\"w\"><w:body><w:p><w:r><w:t>{text}</w:t></w:r><w:instrText>DO_NOT_TRANSLATE</w:instrText></w:p></w:body></w:document>",
            "word/header1.xml": f"<w:hdr xmlns:w=\"w\"><w:p><w:r><w:t>Cancel</w:t></w:r></w:p></w:hdr>",
            "word/media/image.png": b"PNG-NOT-AN-ACTUAL-IMAGE",
        }
    elif kind == "xlsx":
        entries = {
            "[Content_Types].xml": "<Types/>",
            "xl/workbook.xml": "<workbook/>",
            "xl/sharedStrings.xml": f"<sst><si><t>{text}</t></si><si><t>Cancel</t></si></sst>",
            "xl/worksheets/sheet1.xml": "<worksheet><sheetData><row r=\"1\"><c r=\"A1\" t=\"s\"><v>0</v></c><c r=\"B1\"><f>SUM(1,2)</f><v>3</v></c></row></sheetData></worksheet>",
            "xl/charts/chart1.xml": "<c:chart xmlns:c=\"c\"><c:strCache><c:pt><c:v>Close</c:v></c:pt></c:strCache><c:numCache><c:pt><c:v>42</c:v></c:pt></c:numCache></c:chart>",
        }
    elif kind == "pptx":
        entries = {
            "[Content_Types].xml": "<Types/>",
            "ppt/presentation.xml": "<p:presentation xmlns:p=\"p\"/>",
            "ppt/slides/slide1.xml": f"<p:sld xmlns:p=\"p\" xmlns:a=\"a\"><p:sp><a:t>{text}</a:t></p:sp></p:sld>",
            "ppt/notesSlides/notesSlide1.xml": "<p:notes xmlns:p=\"p\" xmlns:a=\"a\"><a:t>Cancel</a:t></p:notes>",
        }
    else:
        raise ValueError(kind)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, value in entries.items():
            archive.writestr(name, value.encode("utf-8") if isinstance(value, str) else value)


def make_wav(path: Path, seconds: float = 0.2) -> None:
    rate = 8000
    frames = int(rate * seconds)
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(rate)
        values = [round(5000 * math.sin(2 * math.pi * 220 * i / rate)) for i in range(frames)]
        wav.writeframes(b"".join(struct.pack("<h", value) for value in values))


class MockHandler(BaseHTTPRequestHandler):
    server_version = "UltimateTranslationMock/1.0"

    def log_message(self, format: str, *args: Any) -> None:
        return

    def send_json(self, value: Any, status: int = 200) -> None:
        data = json.dumps(value, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length)
        if self.path == "/redirect":
            self.server.requests.append(("redirect", length))
            self.send_response(302)
            self.send_header("Location", "/chat")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if self.path == "/transcribe":
            self.server.requests.append(("transcribe", len(raw)))
            self.send_json({"text": "Hello world.", "language": "en", "duration": 1.0, "segments": [{"id": "segment-1", "start": 0.0, "end": 1.0, "speaker": "Speaker A", "text": "Hello world.", "confidence": 0.91}]})
            return
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            self.send_json({"error": "invalid JSON"}, 400)
            return
        self.server.requests.append((self.path, payload))
        messages = payload.get("messages", [])
        user = messages[1].get("content") if len(messages) > 1 else ""
        if isinstance(user, list):
            # Vision OCR request.
            content = {"image": {"width": 100, "height": 50}, "blocks": [{"id": "block-1", "text": "Save", "translation": "Guardar", "confidence": 0.92, "bbox": [1, 2, 40, 20], "reading_order": 1, "kind": "label", "uncertainties": []}]}
        else:
            try:
                request = json.loads(user)
            except (json.JSONDecodeError, TypeError):
                self.send_json({"choices": [{"message": {"content": "{}"}}]})
                return
            if "segments" in request and request.get("task", "").startswith("Translate"):
                rows = []
                for segment in request["segments"]:
                    text = segment["text"]
                    if "Cancel" in text and self.server.fail_cancel:
                        self.send_json({"error": "planned recovery failure"}, 500)
                        return
                    translated = text.replace("Hello world.", "Hola mundo.").replace("Save", "Guardar").replace("Cancel", "Cancelar").replace("Close", "Cerrar")
                    rows.append({"id": segment["id"], "text": translated, "confidence": 0.95, "uncertainties": [], "alternatives": [], "back_translation": ""})
                content = {"translations": rows}
            elif request.get("operation") in {"transcreate", "campaign"}:
                content = {"variants": [{"text": "Guardar", "rationale": "Concise UI-equivalent mock variant", "literal_back_translation": "Save", "intent_preserved": True, "claim_changes": [], "cultural_risks": [], "uncertainties": [], "channel_length": 7}]}
            elif request.get("operation") == "back-translate":
                content = {"back_translation": "Save", "meaning_drift": [], "omissions": [], "additions": [], "added_claims": [], "tone_changes": [], "cultural_substitutions": [], "uncertainties": [], "review_required": True}
            elif request.get("operation") == "gloss":
                content = {"segments": [{"source": "Save", "natural_translation": "Guardar", "literal_translation": "to save", "tokens": [{"surface": "Save", "lemma": "save", "gloss": "save", "morphemes": ["save"], "grammatical_labels": ["VERB"], "uncertainty": None}]}]}
            elif request.get("operation") == "bilingual-review":
                content = {"assessments": [{"segment_id": "seg-1", "accuracy": "acceptable-in-mock", "fluency": "acceptable-in-mock", "severity": "none", "evidence": "mock", "confidence": 0.9, "review_required": True}]}
            else:
                # Generic operation-specific mock data, used only to verify the bounded interface.
                operation = request.get("operation", "unknown")
                required = self.server.adapt_fields.get(operation, "findings")
                value = [] if required.endswith("s") else "Mock output requiring human review"
                content = {required: value, "review_required": True}
        self.send_json({"choices": [{"message": {"content": json.dumps(content, ensure_ascii=False)}}]})


class SkillToolTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), MockHandler)
        cls.server.requests = []
        cls.server.fail_cancel = False
        cls.server.adapt_fields = {"cultural-review": "findings", "inclusive-review": "findings", "learning": "exercises", "sign-language": "interpreted_script", "dubbing": "lines", "lip-sync": "lines", "meeting": "action_items", "pronunciation": "entries", "narrative-review": "findings", "bilingual-review": "assessments"}
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.endpoint = f"http://127.0.0.1:{cls.server.server_port}/chat"

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=5)

    def setUp(self) -> None:
        self.temp = Path(tempfile.mkdtemp(prefix="ultimate-translation-test-"))

    def tearDown(self) -> None:
        shutil.rmtree(self.temp, ignore_errors=True)

    def run_cli(self, *args: Any, expected: int = 0, env: dict[str, str] | None = None, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
        command = [sys.executable, str(CLI), *map(str, args)]
        environment = os.environ.copy()
        if env: environment.update(env)
        result = subprocess.run(command, cwd=cwd or self.temp, env=environment, text=True, capture_output=True, timeout=45)
        if result.returncode != expected:
            self.fail(f"Expected exit {expected}, got {result.returncode}\nCOMMAND: {command}\nSTDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}")
        return result

    def write(self, name: str, content: str) -> Path:
        path = self.temp / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return path

    def config(self, name: str = "config.json", **changes: Any) -> Path:
        config = read_json(BASE_DIR / "asset-default-config.json")
        for key, value in changes.items():
            target = config
            parts = key.split("__")
            for part in parts[:-1]: target = target[part]
            target[parts[-1]] = value
        path = self.temp / name
        path.write_text(json.dumps(config), encoding="utf-8")
        return path

    def test_01_help_and_all_capabilities(self) -> None:
        self.run_cli("--help")
        result = self.run_cli("capabilities")
        value = json.loads(result.stdout)
        self.assertEqual(value["returned"], 144)
        self.assertEqual(value["categories"], 16)
        self.assertTrue(value["preflight"]["passed"])
        self.assertFalse(value["preflight"]["unreachable"])

    def test_02_offline_translation_change_and_structured_errors(self) -> None:
        source = self.write("source.txt", "Hello world.\n")
        target = self.temp / "target.txt"
        self.run_cli("translate", source, target, "--source", "en", "--target", "es", "--glossary", GLOSSARY)
        self.assertEqual(target.read_text(), "Hola mundo.\n")
        report = read_json(Path(str(target) + ".report.json"))
        self.assertEqual(report["translations"][0]["target"], "Hola mundo.")
        source.write_text("Save\n")
        changed = self.temp / "changed.txt"
        self.run_cli("translate", source, changed, "--source", "en", "--target", "es", "--glossary", GLOSSARY)
        self.assertEqual(changed.read_text(), "Guardar\n")
        self.assertNotEqual(target.read_text(), changed.read_text())
        missing = self.run_cli("inspect", self.temp / "missing.txt", expected=2)
        error = json.loads(missing.stderr)
        self.assertFalse(error["ok"])
        self.assertEqual(error["error"]["code"], "input_not_found")
        invalid = self.write("invalid.json", "{broken")
        bad = self.run_cli("translate", invalid, self.temp / "bad.json", "--target", "es", "--provider", "pseudo", expected=2)
        self.assertEqual(json.loads(bad.stderr)["error"]["code"], "invalid_json")

    def test_03_json_yaml_csv_and_pseudolocalization(self) -> None:
        source = self.write("ui.json", json.dumps({"title": "Save", "id": "Save", "nested": {"cancel": "Cancel"}, "count": 2}))
        target = self.temp / "ui-pseudo.json"
        self.run_cli("pseudolocalize", source, target, "--target", "qps-ploc", "--pseudo-expansion", "0.25", "--pseudo-bidi")
        value = read_json(target)
        self.assertEqual(value["id"], "Save")
        self.assertEqual(value["count"], 2)
        self.assertIn("Šáṽé", value["title"])
        self.assertIn("Çáñçéĺ", value["nested"]["cancel"])
        yaml_source = self.write("ui.yaml", "title: Save\nid: Save\nnested:\n  cancel: Cancel\n")
        yaml_target = self.temp / "ui-es.yaml"
        self.run_cli("translate", yaml_source, yaml_target, "--source", "en", "--target", "es", "--glossary", GLOSSARY)
        import yaml
        loaded = yaml.safe_load(yaml_target.read_text())
        self.assertEqual(loaded, {"title": "Guardar", "id": "Save", "nested": {"cancel": "Cancelar"}})
        csv_source = self.write("table.csv", "id,title,note,empty\na1,Save,Cancel,\na2,Close,Save,\n")
        csv_target = self.temp / "table-es.csv"
        self.run_cli("translate", csv_source, csv_target, "--source", "en", "--target", "es", "--glossary", GLOSSARY, "--columns", "title,note")
        with csv_target.open(newline="") as handle: rows = list(csv.DictReader(handle))
        self.assertEqual([row["id"] for row in rows], ["a1", "a2"])
        self.assertEqual(rows[0]["title"], "Guardar")
        self.assertEqual(rows[1]["note"], "Guardar")

    def test_04_markup_markdown_and_resource_protection(self) -> None:
        html_source = self.write("page.html", '<!doctype html><html><head><title>Save</title><style>Save</style><meta name="description" content="Save"></head><body data-name="Save"><h1 class="x">Save</h1><img alt="Save" src="x.png"><p>Fish &amp; Save</p><script>Save</script><input placeholder="Save" value="Save"></body></html>')
        html_target = self.temp / "page-pseudo.html"
        self.run_cli("pseudolocalize", html_source, html_target, "--target", "qps-ploc", "--pseudo-expansion", "0")
        rendered = html_target.read_text()
        self.assertIn('<style>Save</style>', rendered)
        self.assertIn('<script>Save</script>', rendered)
        self.assertIn('data-name="Save"', rendered)
        self.assertIn('alt="⟦Šáṽé⟧"', rendered)
        self.assertIn('Fish', html_source.read_text())
        self.assertIn("Ƒíšĥ &amp; Šáṽé", rendered)
        md_source = self.write("page.md", "---\ntitle: Save\n---\n# Save\n\n`Save`\n\n```\nSave\n```\n\n[Save](https://example.test/Save)\n")
        md_target = self.temp / "page-es.md"
        self.run_cli("translate", md_source, md_target, "--source", "en", "--target", "es", "--glossary", GLOSSARY)
        markdown = md_target.read_text()
        self.assertIn("title: Save", markdown)
        self.assertIn("# Guardar", markdown)
        self.assertIn("`Save`", markdown)
        self.assertIn("```\nSave\n```", markdown)
        self.assertIn("[Guardar](https://example.test/Save)", markdown)
        prop = self.write("messages.properties", "save=Save\nid=Save\n")
        prop_out = self.temp / "messages-es.properties"
        self.run_cli("translate", prop, prop_out, "--source", "en", "--target", "es", "--glossary", GLOSSARY)
        self.assertIn("save=Guardar", prop_out.read_text())

    def test_05_ooxml_docx_xlsx_pptx_preservation(self) -> None:
        for kind in ("docx", "xlsx", "pptx"):
            with self.subTest(kind=kind):
                source = self.temp / f"source.{kind}"
                make_ooxml(source, kind)
                target = self.temp / f"target.{kind}"
                self.run_cli("translate", source, target, "--source", "en", "--target", "es", "--glossary", GLOSSARY)
                with zipfile.ZipFile(source) as before, zipfile.ZipFile(target) as after:
                    self.assertEqual(before.namelist(), after.namelist())
                    self.assertIsNone(after.testzip())
                    if kind == "docx":
                        self.assertIn("Guardar", after.read("word/document.xml").decode())
                        self.assertIn("DO_NOT_TRANSLATE", after.read("word/document.xml").decode())
                        self.assertIn("Cancelar", after.read("word/header1.xml").decode())
                        self.assertEqual(before.read("word/media/image.png"), after.read("word/media/image.png"))
                    elif kind == "xlsx":
                        self.assertIn("Guardar", after.read("xl/sharedStrings.xml").decode())
                        self.assertIn("Cancelar", after.read("xl/sharedStrings.xml").decode())
                        self.assertIn("<f>SUM(1,2)</f>", after.read("xl/worksheets/sheet1.xml").decode())
                        chart = after.read("xl/charts/chart1.xml").decode()
                        self.assertIn("Cerrar", chart)
                        self.assertIn("<c:v>42</c:v>", chart)
                    else:
                        self.assertIn("Guardar", after.read("ppt/slides/slide1.xml").decode())
                        self.assertIn("Cancelar", after.read("ppt/notesSlides/notesSlide1.xml").decode())

    def test_06_pdf_extraction_and_reconstruction(self) -> None:
        from reportlab.pdfgen import canvas
        from pypdf import PdfReader
        source = self.temp / "source.pdf"
        pdf = canvas.Canvas(str(source)); pdf.drawString(72, 760, "Hello world."); pdf.showPage(); pdf.save()
        target = self.temp / "target.pdf"
        self.run_cli("translate", source, target, "--source", "en", "--target", "es", "--glossary", GLOSSARY)
        self.assertTrue(target.read_bytes().startswith(b"%PDF"))
        text = "\n".join(page.extract_text() or "" for page in PdfReader(str(target)).pages)
        self.assertIn("Hola mundo.", text)
        report = read_json(Path(str(target) + ".report.json"))
        self.assertTrue(any("text-first" in warning for warning in report["document_metadata"]["warnings"]))

    def test_07_qa_alignment_memory_and_review(self) -> None:
        source = self.write("source.txt", "Save 10 kg {name}\n")
        target = self.write("target.txt", "Guardar 5 kg {name}\n")
        glossary = self.write("terms.csv", "source,target,source_locale,target_locale,status,term_type,forbidden\nSave,Guardar,en,es,approved,command,Salvar\n")
        result = self.run_cli("qa", "--source", source, "--target", target, "--source-locale", "en", "--target-locale", "es", "--glossary", glossary)
        report = json.loads(result.stdout)
        categories = {row["category"] for row in report["defects"]}
        self.assertIn("number-unit", categories)
        self.assertGreater(report["summary"]["blocking_defects"], 0)
        review_decisions = self.write("decisions.json", json.dumps([{"segment_id": report["aligned_segments"][0]["id"], "decision": "changes-requested", "reviewer": "Test Reviewer", "comment": "Correct quantity"}]))
        self.write("qa.json", json.dumps(report))
        reviewed = self.run_cli("review", self.temp / "qa.json", "--decisions", review_decisions)
        self.assertEqual(json.loads(reviewed.stdout)["aligned_segments"][0]["review_status"], "changes-requested")
        good_source = self.write("good-source.txt", "Hello world.\nSave\n")
        good_target = self.write("good-target.txt", "Hola mundo.\nGuardar\n")
        memory_path = self.temp / "memory.json"
        self.run_cli("memory", "build", "--source", good_source, "--target", good_target, "--source-locale", "en", "--target-locale", "es", "--status", "approved", "--confirm-sensitive", "--output", memory_path)
        memory = read_json(memory_path)
        self.assertEqual(memory["summary"]["unique_pairs"], 2)
        tmx = self.temp / "memory.tmx"
        self.run_cli("memory", "tmx", "--input", memory_path, "--source-locale", "en", "--target-locale", "es", "--output", tmx)
        self.assertIn("<tmx", tmx.read_text())
        cleaned = self.run_cli("memory", "clean", "--input", memory_path, "--source-locale", "en", "--target-locale", "es")
        self.assertEqual(json.loads(cleaned.stdout)["summary"]["clean"], 2)

    def test_08_exchange_delta_and_learning_alignment(self) -> None:
        source = self.write("source.json", json.dumps({"title": "Save", "close": "Close"}))
        exchange = self.temp / "exchange.json"
        self.run_cli("convert", "extract", source, exchange, "--source-locale", "en", "--target-locale", "es")
        package = read_json(exchange)
        self.assertEqual(len(package["segments"]), 2)
        valid = self.run_cli("validate", "exchange", "--input", exchange)
        self.assertTrue(json.loads(valid.stdout)["valid"])
        for row in package["segments"]: row["target"] = {"Save": "Guardar", "Close": "Cerrar"}[row["source"]]
        exchange.write_text(json.dumps(package))
        applied = self.temp / "applied.json"
        self.run_cli("convert", "apply", source, applied, "--exchange", exchange, "--source-locale", "en", "--target-locale", "es")
        self.assertEqual(read_json(applied)["title"], "Guardar")
        previous = self.write("previous.txt", "Save\nClose\n")
        current = self.write("current.txt", "Save\nCancel\n")
        memory = self.temp / "delta-memory.json"
        memory.write_text(json.dumps({"segments": [{"id": "1", "source": "Cancel", "target": "Cancelar", "source_locale": "en", "target_locale": "es", "status": "approved"}]}))
        delta = self.run_cli("delta", "--previous", previous, "--current", current, "--memory", memory, "--source-locale", "en", "--target-locale", "es")
        changes = json.loads(delta.stdout)["changes"]
        self.assertEqual(changes[0]["impact"], "reuse")
        self.assertEqual(changes[1]["impact"], "memory-reuse")
        target = self.write("target.txt", "Guardar\nCerrar\n")
        aligned = self.run_cli("align", "--source", previous, "--target", target, "--source-locale", "en", "--target-locale", "es", "--learning", "--glossary", GLOSSARY)
        learning = json.loads(aligned.stdout)["learning"]
        self.assertTrue(learning["exercises"])
        self.assertTrue(learning["answer_key"])

    def test_09_subtitles_and_voiceover(self) -> None:
        source = self.write("source.srt", "1\n00:00:01,000 --> 00:00:02,000\nSave\n\n2\n00:00:02,100 --> 00:00:04,000\nClose\n")
        target = self.temp / "target.srt"
        self.run_cli("translate", source, target, "--source", "en", "--target", "es", "--glossary", GLOSSARY)
        rendered = target.read_text()
        self.assertIn("00:00:01,000 --> 00:00:02,000", rendered)
        self.assertIn("Guardar", rendered)
        adjusted = self.temp / "adjusted.srt"
        self.run_cli("subtitles", "adjust", source, "--offset-ms", "500", "--output", adjusted)
        self.assertIn("00:00:01,500 -->", adjusted.read_text())
        self.assertTrue(adjusted.read_text(encoding="utf-8").strip())
        qa = self.run_cli("subtitles", "qa", source, "--target", target)
        self.assertIn("summary", json.loads(qa.stdout))
        package = self.run_cli("subtitles", "package", source, "--target", target, "--target-locale", "es", "--title", "Test")
        value = json.loads(package.stdout)
        self.assertEqual(value["summary"]["lines"], 2)
        self.assertTrue(value["picture_lock_required"])

    def test_10_audio_and_manual_transcript_export(self) -> None:
        wav = self.temp / "audio.wav"; make_wav(wav)
        assessed = self.run_cli("audio", "assess", wav)
        self.assertEqual(json.loads(assessed.stdout)["sample_rate_hz"], 8000)
        chunks = self.temp / "chunks"
        self.run_cli("audio", "prepare", wav, "--output-dir", chunks, "--chunk-seconds", "0.1", "--normalize", "--trim-silence")
        manifest = read_json(chunks / "audio-chunks.json")
        self.assertEqual(len(manifest["chunks"]), 2)
        for row in manifest["chunks"]: self.assertTrue((chunks / row["path"]).is_file())
        transcript = self.write("transcript.json", json.dumps({"text": "Hello world.", "language": "en", "segments": [{"id": "s1", "start": 0, "end": 1, "speaker": "A", "text": "Hello world.", "confidence": 0.8}]}))
        srt = self.temp / "transcript.srt"
        self.run_cli("transcribe", transcript, srt, "--format", "srt")
        self.assertIn("A: Hello world.", srt.read_text())
        translated = self.temp / "translated.srt"
        self.run_cli("transcribe", transcript, translated, "--format", "srt", "--source", "en", "--target", "es", "--provider", "glossary", "--glossary", GLOSSARY)
        self.assertIn("A: Hola mundo.", translated.read_text())

    def test_11_visual_ocr_schema_and_font(self) -> None:
        ocr = self.write("ocr.json", json.dumps({"image": {"width": 100, "height": 50}, "blocks": [{"id": "b1", "text": "Save", "translation": "Guardar", "confidence": 0.9, "bbox": [0, 0, 20, 10], "reading_order": 1, "kind": "label", "uncertainties": []}]}))
        result = self.run_cli("visual", ocr, "--target", "es")
        plan = json.loads(result.stdout)
        self.assertEqual(plan["blocks"][0]["replacement_text"], "Guardar")
        self.assertTrue(plan["summary"]["review_required"])
        # Build a temporary tiny font, avoiding any machine-specific font path.
        from fontTools.fontBuilder import FontBuilder
        from fontTools.pens.ttGlyphPen import TTGlyphPen
        font_path = self.temp / "tiny.ttf"
        glyphs = {}
        for name in (".notdef", "A", "B"):
            pen = TTGlyphPen(None); pen.moveTo((0, 0)); pen.lineTo((100, 0)); pen.lineTo((100, 100)); pen.closePath(); glyphs[name] = pen.glyph()
        builder = FontBuilder(1000, isTTF=True)
        builder.setupGlyphOrder([".notdef", "A", "B"]); builder.setupCharacterMap({65: "A", 66: "B"}); builder.setupGlyf(glyphs)
        builder.setupHorizontalMetrics({name: (600, 0) for name in glyphs}); builder.setupHorizontalHeader(ascent=800, descent=-200)
        builder.setupNameTable({"familyName": "Test Tiny", "styleName": "Regular"}); builder.setupOS2(); builder.setupPost(); builder.save(font_path)
        audited = self.run_cli("font", font_path, "--text", "ABÇ", "--locales", "es")
        self.assertGreaterEqual(json.loads(audited.stdout)["missing_count"], 1)

    def test_12_locale_plan_transliteration_detection(self) -> None:
        plural = self.run_cli("locale", "plural", "--locale", "ar", "--values", "0,1,2,3,11")
        categories = {row["category"] for row in json.loads(plural.stdout)["cases"]}
        self.assertEqual(categories, {"zero", "one", "two", "few", "many"})
        message = self.run_cli("locale", "message", "--text", "{count, plural, one {# file} other {# files}}")
        self.assertTrue(json.loads(message.stdout)["valid"])
        gender = self.run_cli("locale", "message", "--locale", "en", "--text", "{gender, select, female {{count, plural, one {She has # file} other {She has # files}}} other {{count, plural, one {They have # file} other {They have # files}}}}", "--values-json", '{"gender":"female","count":2}')
        self.assertEqual(json.loads(gender.stdout)["rendered"], "She has 2 files")
        locales = self.run_cli("locale", "plan", "--locales", "ar,es-MX,zh-TW")
        self.assertEqual(len(json.loads(locales.stdout)["locales"]), 3)
        translit = self.run_cli("transliterate", "--text", "Привет", "--scheme", "cyrillic-latin-basic")
        value = json.loads(translit.stdout)
        self.assertTrue(value["round_trip_exact"])
        reverse = self.run_cli("transliterate", "--text", value["transliterated"], "--scheme", "cyrillic-latin-basic", "--reverse")
        self.assertEqual(json.loads(reverse.stdout)["transliterated"], "Привет")
        detected = self.run_cli("detect", self.write("languages.txt", "Hello world. This is English.\nHola el mundo y para usted.\n"))
        self.assertGreaterEqual(json.loads(detected.stdout)["summary"]["segments"], 2)

    def test_13_plan_project_governance_and_handoff(self) -> None:
        plan = self.run_cli("plan", "--locales", "es,ar", "--words", "1000", "--domain", "medical", "--capability", "Medical Translation Assistant")
        self.assertEqual(json.loads(plan.stdout)["routing"]["primary_route"], "subject-specialist")
        state = self.temp / "project.json"
        self.run_cli("project", "init", "--project", state, "--name", "Test Project", "--locales", "es", "--owner", "Owner")
        asset_event = self.write("asset-event.json", json.dumps({"type": "asset.upsert", "asset": {"id": "a1", "words": 100, "risk": "normal", "locale": "es"}}))
        self.run_cli("project", "update", "--project", state, "--event", asset_event, "--expected-revision", "0")
        approval = self.write("approval.json", json.dumps({"type": "approval.add", "role": "linguistic", "owner": "Reviewer", "date": "2026-11-05", "scope": "a1", "approved": True}))
        denied = self.run_cli("project", "update", "--project", state, "--event", approval, expected=3)
        self.assertEqual(json.loads(denied.stderr)["error"]["code"], "approval_required")
        self.run_cli("project", "update", "--project", state, "--event", approval, "--confirm-sensitive", "--expected-revision", "1")
        metrics = self.run_cli("project", "metrics", "--project", state)
        self.assertEqual(json.loads(metrics.stdout)["revision"], 2)
        asset = self.write("localized.txt", "Guardar\n")
        handoff = self.temp / "handoff.zip"
        result = self.run_cli("project", "handoff", "--project", state, "--output", handoff, "--files", asset, "--glossary", GLOSSARY)
        self.assertTrue(json.loads(result.stdout)["validated"])
        self.assertTrue(zipfile.is_zipfile(handoff))
        validated = self.run_cli("validate", "handoff", "--input", handoff)
        self.assertTrue(json.loads(validated.stdout)["ok"])
        sample = self.run_cli("project", "sample", "--project", state, "--sample-rate", "1")
        self.assertEqual(json.loads(sample.stdout)["sample_size"], 1)

    def test_14_repository_security_web_narrative_and_ci(self) -> None:
        repo = self.temp / "repo"; repo.mkdir()
        (repo / "app.py").write_text('button = "ignored"\nsetText("Save changes now")\napi_key = "TESTSECRETVALUE123"\n')
        (repo / "en.json").write_text('{"save":"Save"}')
        report = self.run_cli("repository", repo)
        value = json.loads(report.stdout)
        self.assertGreaterEqual(value["summary"]["hardcoded_candidates"], 1)
        self.assertGreaterEqual(value["summary"]["secret_findings"], 1)
        secret = self.write("secret.txt", 'api_key = "TESTSECRETVALUE123"\n')
        redacted = self.temp / "redacted.txt"
        self.run_cli("secure", "redact", "--input", secret, "--output", redacted)
        self.assertNotIn("TESTSECRETVALUE123", redacted.read_text())
        cleanup = self.write("delete.txt", "temporary")
        self.run_cli("secure", "cleanup", "--paths", cleanup, expected=3)
        self.assertTrue(cleanup.exists())
        self.run_cli("secure", "cleanup", "--paths", cleanup, "--approve-delete")
        self.assertFalse(cleanup.exists())
        source_site = self.temp / "site-en"; source_site.mkdir(); (source_site / "index.html").write_text("<h1>Save</h1>")
        es_site = self.temp / "site-es"; es_site.mkdir(); (es_site / "index.html").write_text("<h1>Guardar</h1>")
        packaged = self.temp / "site-out"
        web = self.run_cli("web", source_site, "--localized", f"es={es_site}", "--output-dir", packaged, "--default-locale", "en")
        self.assertTrue((packaged / "es/index.html").is_file())
        self.assertTrue((packaged / "localization-routes.json").is_file())
        narrative = self.write("narrative.json", json.dumps({"start": "n1", "variables": ["hero"], "nodes": [{"id": "n1", "text": "{{hero}}", "choices": [{"label": "Save", "next": "n2"}]}, {"id": "n2", "text": "Close"}]}))
        audited = self.run_cli("narrative", narrative)
        self.assertTrue(json.loads(audited.stdout)["summary"]["release_ready"])
        qa_report = self.write("gate.json", json.dumps({"summary": {"blocking_defects": 1, "review_findings": 0, "release_ready": False}}))
        gate = self.run_cli("validate", "ci", "--input", qa_report, expected=5)
        self.assertEqual(json.loads(gate.stderr)["error"]["code"], "ci_gate_failed")

    def test_15_http_translation_and_checkpoint_recovery(self) -> None:
        config = self.config(limits__batch_segments=1, limits__http_retries=0, limits__retry_backoff_seconds=0.01, translation__allow_network=True)
        env = {"ULTIMATE_TRANSLATION_ENDPOINT": self.endpoint, "ULTIMATE_TRANSLATION_MODEL": "mock-translator"}
        source = self.write("source.txt", "Save {name}\n")
        target = self.temp / "target.txt"
        self.run_cli("translate", source, target, "--config", config, "--source", "en", "--target", "es", "--provider", "http", "--allow-network", env=env)
        self.assertEqual(target.read_text(), "Guardar {name}\n")
        recovery_source = self.write("recovery.txt", "Hello world.\nCancel\nClose\n")
        recovery_target = self.temp / "recovery-target.txt"
        self.server.fail_cancel = True
        failed = self.run_cli("translate", recovery_source, recovery_target, "--config", config, "--source", "en", "--target", "es", "--provider", "http", "--allow-network", env=env, expected=2)
        self.assertIn("provider_http_error", failed.stderr)
        checkpoint = Path(str(recovery_target) + ".checkpoint.json")
        saved = read_json(checkpoint)
        self.assertEqual(saved["completed_batches"], 1)
        self.assertEqual(saved["status"], "failed")
        self.server.fail_cancel = False
        self.run_cli("translate", recovery_source, recovery_target, "--config", config, "--source", "en", "--target", "es", "--provider", "http", "--allow-network", "--resume", env=env)
        self.assertEqual(recovery_target.read_text(), "Hola mundo.\nCancelar\nCerrar\n")
        self.assertEqual(read_json(checkpoint)["status"], "complete")

    def test_16_http_ocr_transcription_and_adaptation(self) -> None:
        config = self.config(translation__allow_network=True)
        base_env = {"ULTIMATE_TRANSLATION_MODEL": "mock-translator", "ULTIMATE_TRANSLATION_ENDPOINT": self.endpoint}
        png = self.temp / "tiny.png"
        # A valid 1x1 PNG fixture; the mock analyzes the protocol, not actual pixels.
        import base64
        png.write_bytes(base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="))
        vision_env = {**base_env, "ULTIMATE_TRANSLATION_VISION_ENDPOINT": self.endpoint, "ULTIMATE_TRANSLATION_VISION_MODEL": "mock-vision"}
        ocr = self.run_cli("ocr", png, "--target", "es", "--config", config, "--allow-network", env=vision_env)
        self.assertEqual(json.loads(ocr.stdout)["blocks"][0]["translation"], "Guardar")
        wav = self.temp / "audio.wav"; make_wav(wav)
        transcription_env = {**base_env, "ULTIMATE_TRANSLATION_TRANSCRIPTION_ENDPOINT": f"http://127.0.0.1:{self.server.server_port}/transcribe", "ULTIMATE_TRANSLATION_TRANSCRIPTION_MODEL": "mock-speech"}
        transcript = self.temp / "speech.json"
        self.run_cli("transcribe", wav, transcript, "--config", config, "--allow-network", env=transcription_env)
        self.assertEqual(read_json(transcript)["transcript"]["text"], "Hello world.")
        creative = self.write("creative.txt", "Save")
        adapted = self.run_cli("adapt", "transcreate", creative, "--source-locale", "en", "--target-locale", "es", "--config", config, "--allow-network", env=base_env)
        self.assertEqual(json.loads(adapted.stdout)["result"]["variants"][0]["text"], "Guardar")
        back = self.run_cli("adapt", "back-translate", self.write("localized.txt", "Guardar"), "--original", creative, "--source-locale", "en", "--target-locale", "es", "--config", config, "--allow-network", env=base_env)
        self.assertEqual(json.loads(back.stdout)["result"]["back_translation"], "Save")

    def test_17_batch_and_clean_future_skill_root(self) -> None:
        inputs = self.temp / "inputs"; inputs.mkdir()
        (inputs / "one.txt").write_text("Save\n")
        (inputs / "two.json").write_text('{"title":"Close"}')
        outputs = self.temp / "outputs"
        batch = self.run_cli("batch", inputs, "--output-dir", outputs, "--source", "en", "--target", "es", "--glossary", GLOSSARY)
        self.assertEqual(json.loads(batch.stdout)["succeeded"], 2)
        self.assertEqual((outputs / "one.txt").read_text(), "Guardar\n")
        resumed = self.run_cli("batch", inputs, "--output-dir", outputs, "--source", "en", "--target", "es", "--glossary", GLOSSARY, "--resume")
        self.assertEqual(json.loads(resumed.stdout)["succeeded"], 2)
        # Clean future skill root: exactly one test SKILL.md beside the tool folder.
        skill_root = self.temp / "clean-skill"; skill_root.mkdir()
        shutil.copytree(BASE_DIR, skill_root / "ultimate-translation", ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        (skill_root / "SKILL.md").write_text("# Test parent skill\n")
        relative_source = skill_root / "input.txt"; relative_source.write_text("Hello world.\n")
        result = subprocess.run([sys.executable, "ultimate-translation/tools-translate.py", "translate", "input.txt", "output.txt", "--source", "en", "--target", "es", "--glossary", "ultimate-translation/asset-glossary.csv"], cwd=skill_root, text=True, capture_output=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((skill_root / "output.txt").read_text(), "Hola mundo.\n")
        tool_files = [path for path in (skill_root / "ultimate-translation").iterdir() if path.is_file()]
        self.assertFalse((skill_root / "ultimate-translation" / "SKILL.md").exists())
        self.assertTrue(all(path.name.startswith(("tools-", "knowledge-", "asset-")) for path in tool_files))

    def test_18_additional_formats_terms_and_xliff(self) -> None:
        po = self.write("messages.po", '#: ui.py:1\nmsgid "Save"\nmsgstr ""\n\n#: ui.py:2\nmsgid "Close"\nmsgstr ""\n')
        po_out = self.temp / "messages-es.po"
        self.run_cli("translate", po, po_out, "--source", "en", "--target", "es", "--glossary", GLOSSARY)
        self.assertIn('msgid "Save"\nmsgstr "Guardar"', po_out.read_text())
        self.assertIn('msgid "Close"\nmsgstr "Cerrar"', po_out.read_text())
        strings = self.write("Localizable.strings", '"save" = "Save";\n"close" = "Close";\n')
        strings_out = self.temp / "Localizable-es.strings"
        self.run_cli("translate", strings, strings_out, "--source", "en", "--target", "es", "--glossary", GLOSSARY)
        self.assertIn('"save" = "Guardar";', strings_out.read_text())
        vtt = self.write("source.vtt", "WEBVTT\n\n1\n00:00:01.000 --> 00:00:02.000 align:start\nSave\n")
        vtt_out = self.temp / "target.vtt"
        self.run_cli("translate", vtt, vtt_out, "--source", "en", "--target", "es", "--glossary", GLOSSARY)
        self.assertIn("00:00:01.000 --> 00:00:02.000 align:start", vtt_out.read_text())
        self.assertIn("Guardar", vtt_out.read_text())
        ass = self.write("source.ass", "[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\nDialogue: 0,0:00:01.00,0:00:02.00,Default,A,0,0,0,,Save\n")
        ass_out = self.temp / "target.ass"
        self.run_cli("translate", ass, ass_out, "--source", "en", "--target", "es", "--glossary", GLOSSARY)
        self.assertIn("0:00:01.00,0:00:02.00", ass_out.read_text())
        self.assertIn("Guardar", ass_out.read_text())
        ttml = self.write("source.ttml", '<tt xmlns="urn:ttml"><body><div><p begin="00:00:01.000" end="00:00:02.000">Save</p></div></body></tt>')
        ttml_out = self.temp / "target.ttml"
        self.run_cli("translate", ttml, ttml_out, "--source", "en", "--target", "es", "--glossary", GLOSSARY)
        self.assertIn('begin="00:00:01.000"', ttml_out.read_text())
        self.assertIn("Guardar", ttml_out.read_text())
        terms = self.run_cli("terms", "extract", "--input", self.write("terms.txt", "AcmeCloud AcmeCloud AcmeCloud API_TOKEN API_TOKEN\nAcmeCloud API_TOKEN\n"), "--max-terms", "10")
        self.assertTrue(json.loads(terms.stdout)["terms"])
        validated = self.run_cli("terms", "validate", "--input", GLOSSARY)
        self.assertTrue(json.loads(validated.stdout)["valid"])
        style = self.temp / "style.md"
        self.run_cli("terms", "style", "--target-locale", "es", "--style", BASE_DIR / "asset-style-guide.json", "--glossary", GLOSSARY, "--output", style)
        self.assertIn("Localization Style Guide", style.read_text())
        pronunciation = self.run_cli("terms", "pronunciation", "--input", GLOSSARY)
        self.assertGreater(json.loads(pronunciation.stdout)["summary"]["entries"], 0)
        source = self.write("exchange-source.txt", "Save\n")
        xliff = self.temp / "exchange.xlf"
        self.run_cli("convert", "xliff", source, xliff, "--source-locale", "en", "--target-locale", "es")
        import xml.etree.ElementTree as ET
        self.assertEqual(ET.parse(xliff).getroot().tag.endswith("xliff"), True)

    def test_19_batch_partial_recovery_release_and_feedback(self) -> None:
        inputs = self.temp / "partial-inputs"; inputs.mkdir()
        (inputs / "good.txt").write_text("Save\n")
        (inputs / "bad.json").write_text("{broken")
        outputs = self.temp / "partial-outputs"
        failed = self.run_cli("batch", inputs, "--output-dir", outputs, "--source", "en", "--target", "es", "--glossary", GLOSSARY, expected=2)
        summary = json.loads(failed.stdout)
        self.assertEqual(summary["failed"], 1)
        self.assertEqual(summary["succeeded"], 1)
        (inputs / "bad.json").write_text('{"title":"Close"}')
        recovered = self.run_cli("batch", inputs, "--output-dir", outputs, "--source", "en", "--target", "es", "--glossary", GLOSSARY, "--resume")
        self.assertEqual(json.loads(recovered.stdout)["succeeded"], 2)
        self.assertEqual(read_json(outputs / "bad.json")["title"], "Cerrar")
        localized = self.write("localized.txt", "Guardar\n")
        manifest = self.write("release.json", json.dumps({"locales": ["es"], "assets": [{"localized_path": "localized.txt", "locales": ["es"], "open_blocking_defects": 0}], "required_approvals": ["linguistic", "release"], "approvals": {"linguistic": {"approved": True, "owner": "Reviewer", "date": "2026-11-05"}, "release": {"approved": True, "owner": "Owner", "date": "2026-11-05"}}}))
        released = self.run_cli("project", "release", "--project", self.temp / "unused-project.json", "--manifest", manifest)
        self.assertTrue(json.loads(released.stdout)["summary"]["release_ready"])
        bad_manifest = read_json(manifest); bad_manifest["assets"][0]["open_blocking_defects"] = 1
        bad_release = self.write("bad-release.json", json.dumps(bad_manifest))
        report_path = self.temp / "bad-release-report.json"
        self.run_cli("project", "release", "--project", self.temp / "unused-project.json", "--manifest", bad_release, "--output", report_path)
        self.run_cli("validate", "ci", "--input", report_path, "--require-release-ready", expected=5)
        feedback = self.write("feedback.json", json.dumps([{"source": "reviewer-a", "text": "Wrong meaning in Save", "asset_id": "a1", "segment_id": "s1", "decision": "accept"}, {"source": "reviewer-b", "text": "Wrong meaning in Save", "asset_id": "a1", "segment_id": "s1", "decision": "reject"}]))
        consolidated = self.run_cli("project", "feedback", "--project", self.temp / "no-state.json", "--input", feedback)
        self.assertEqual(json.loads(consolidated.stdout)["summary"]["conflicts"], 1)

    def test_20_remaining_adaptation_routes_and_redirect_rejection(self) -> None:
        config = self.config(translation__allow_network=True, limits__http_retries=0)
        env = {"ULTIMATE_TRANSLATION_ENDPOINT": self.endpoint, "ULTIMATE_TRANSLATION_MODEL": "mock-adapter"}
        source = self.write("adapt.txt", "Save")
        for operation in ("campaign", "cultural-review", "inclusive-review", "gloss", "learning", "sign-language", "dubbing", "lip-sync", "meeting", "pronunciation", "narrative-review", "bilingual-review"):
            with self.subTest(operation=operation):
                result = self.run_cli("adapt", operation, source, "--source-locale", "en", "--target-locale", "es", "--config", config, "--allow-network", env=env)
                value = json.loads(result.stdout)
                self.assertTrue(value["review_required"])
                required = {"campaign": "variants", "cultural-review": "findings", "inclusive-review": "findings", "gloss": "segments", "learning": "exercises", "sign-language": "interpreted_script", "dubbing": "lines", "lip-sync": "lines", "meeting": "action_items", "pronunciation": "entries", "narrative-review": "findings", "bilingual-review": "assessments"}[operation]
                self.assertIn(required, value["result"])
        redirect_env = {"ULTIMATE_TRANSLATION_ENDPOINT": f"http://127.0.0.1:{self.server.server_port}/redirect", "ULTIMATE_TRANSLATION_MODEL": "mock-adapter"}
        target = self.temp / "redirect.txt"
        rejected = self.run_cli("translate", source, target, "--source", "en", "--target", "es", "--provider", "http", "--allow-network", "--config", config, env=redirect_env, expected=2)
        self.assertEqual(json.loads(rejected.stderr)["error"]["code"], "provider_http_error")
        self.assertFalse(target.exists())


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(SkillToolTests)
    result = unittest.TextTestRunner(verbosity=2 if args.verbose else 1).run(suite)
    print(json.dumps({"ok": result.wasSuccessful(), "tests_run": result.testsRun, "failures": len(result.failures), "errors": len(result.errors), "skipped": len(result.skipped), "external_live_services_used": False, "loopback_mock_used": True}, indent=2))
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
