#!/usr/bin/env python3
"""Self-test suite for the offline-3d-production Skill Tool.

Run from the Skill Tool folder:

    python tools-o3d-tests.py            # full suite
    python tools-o3d-tests.py -v         # verbose
    python tools-o3d-tests.py --only mesh

Tests drive the *real* command-line interface through subprocess, so they prove the
shipped entry point works rather than testing internal helpers in isolation. Every
test writes into a temporary directory and cleans it up; nothing outside the temp
root is touched.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ENTRY = os.path.join(HERE, "tools-o3d.py")
PY = sys.executable

EXIT_OK = 0
EXIT_INVALID = 2
EXIT_APPROVAL = 3


def run(args, workspace=None, expect=None, timeout=300):
    cmd = [PY, ENTRY] + args
    if workspace:
        cmd += ["--workspace", workspace]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, cwd=HERE)
    out = proc.stdout.strip()
    payload = None
    if out.startswith("{"):
        try:
            payload = json.loads(out)
        except json.JSONDecodeError:
            payload = None
    if expect is not None and proc.returncode != expect:
        raise AssertionError(
            "command {0} exited {1}, expected {2}\nSTDOUT:\n{3}\nSTDERR:\n{4}".format(
                " ".join(args), proc.returncode, expect, proc.stdout[-4000:], proc.stderr[-2000:])
        )
    return proc, payload


class Harness(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="o3d-tests-")
        cls.addClassCleanup(shutil.rmtree, cls.tmp, True)
        cfg = os.path.join(HERE, "asset-default-config.json")
        with open(cfg, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        data["limits"]["audit_dir"] = "audit"
        data["limits"]["output_dir"] = "outputs"
        cls.config = os.path.join(cls.tmp, "config.json")
        with open(cls.config, "w", encoding="utf-8") as fh:
            json.dump(data, fh)
        shutil.copy(os.path.join(HERE, "asset-license-policy.json"), cls.tmp)
        shutil.copy(os.path.join(HERE, "asset-consent-policy.json"), cls.tmp)
        cls.out = os.path.join(cls.tmp, "outputs")
        os.makedirs(cls.out, exist_ok=True)
        proc, _ = run(["--config", cls.config, "--workspace", cls.tmp, "fixture", "--dir", "fixtures",
                       "--force"])
        assert proc.returncode == 0, proc.stdout + proc.stderr

    def base(self, *args):
        return ["--config", self.config, "--workspace", self.tmp] + list(args)

    def out_path(self, name):
        return os.path.join(self.out, name)


class TestMeta(Harness):
    def test_capabilities(self):
        proc, payload = run(["capabilities"])
        self.assertEqual(proc.returncode, EXIT_OK)
        self.assertGreater(payload["command_count"], 50)
        self.assertEqual(payload["catalog_coverage"]["categories"], 32)
        self.assertEqual(payload["catalog_coverage"]["skills"], 446)

    def test_help(self):
        proc, payload = run(["help"])
        self.assertEqual(proc.returncode, EXIT_OK)
        self.assertIn("EXAMPLES", payload["examples"])
        self.assertEqual(payload["exit_codes"]["3"], "approval required")

    def test_doctor(self):
        proc, payload = run(["doctor"])
        self.assertEqual(proc.returncode, EXIT_OK)
        self.assertFalse(payload["network_required"])
        self.assertIn("dependencies", payload)

    def test_gate_catalogue(self):
        proc, payload = run(["gate"])
        self.assertEqual(proc.returncode, EXIT_OK)
        names = [g["gate"] for g in payload["gates"]]
        for expected in ("geometry", "animation", "package", "license", "consent", "install"):
            self.assertIn(expected, names)

    def test_text_format(self):
        proc, _ = run(["capabilities", "--format", "text"])
        self.assertEqual(proc.returncode, EXIT_OK)
        self.assertIn("offline-3d-production", proc.stdout)

    def test_no_args_is_invalid(self):
        proc = subprocess.run([PY, ENTRY], capture_output=True, text=True, cwd=HERE)
        self.assertEqual(proc.returncode, EXIT_INVALID)

    def test_unknown_command(self):
        proc = subprocess.run([PY, ENTRY, "not-a-command"], capture_output=True, text=True, cwd=HERE)
        self.assertEqual(proc.returncode, EXIT_INVALID)


class TestStandards(Harness):
    def test_naming_units_axis_config(self):
        for topic, extra, expect_key in [
            ("naming", [], "pattern"),
            ("units", ["--unit", "cm"], "scene_unit"),
            ("axis", ["--up", "Z", "--forward", "Y"], "up_axis"),
            ("config", ["--project", "hero"], "fps"),
        ]:
            out = self.out_path("std-{0}.json".format(topic))
            proc, payload = run(self.base("standards", topic, "--out", out, *extra))
            self.assertEqual(proc.returncode, EXIT_OK, proc.stdout + proc.stderr)
            self.assertIn(expect_key, payload["document"])
            self.assertTrue(os.path.exists(out))
            with open(out, "r", encoding="utf-8") as fh:
                json.load(fh)

    def test_handoff(self):
        out = self.out_path("handoff.json")
        proc, payload = run(self.base("standards", "handoff", "--project", "hero",
                                      "--department", "rigging", "--out", out))
        self.assertEqual(proc.returncode, EXIT_OK)
        self.assertEqual(payload["document"]["department"], "rigging")
        self.assertIn("approval_status", payload["document"]["fields"])

    def test_bad_axis_rejected(self):
        proc, payload = run(self.base("standards", "axis", "--up", "Z", "--forward", "Z"))
        self.assertEqual(proc.returncode, EXIT_INVALID)
        self.assertEqual(payload["error"]["code"], "invalid_input")

    def test_units_roundtrip(self):
        proc, payload = run(self.base("standards", "units", "--unit", "mm", "--for-print"))
        self.assertEqual(proc.returncode, EXIT_OK)
        self.assertIn("1 mm", payload["document"]["print_note"])


class TestNames(Harness):
    def test_valid_and_invalid_names(self):
        out = self.out_path("names.json")
        args = self.base("validate", "names", "--out", out,
                         "--name", "hero_model_body_v001",
                         "--name", "hero model body",
                         "--name", "Default",
                         "--name", "hero_model_body_v001")
        proc, payload = run(args, expect=EXIT_INVALID)
        codes = {f["code"] for f in payload["findings"]}
        self.assertIn("illegal_character", codes)
        self.assertIn("duplicate_name", codes)
        self.assertIn("reserved_default_name", codes)
        self.assertEqual(payload["gate"], "fail")

    def test_all_valid_names_pass(self):
        out = self.out_path("names-ok.json")
        proc, payload = run(self.base("validate", "names", "--out", out,
                                      "--name", "hero_model_body_v001",
                                      "--name", "hero_rig_body_v002"))
        self.assertEqual(proc.returncode, EXIT_OK)
        self.assertEqual(payload["gate"], "pass")

    def test_names_file_input(self):
        path = os.path.join(self.tmp, "names.txt")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("hero_model_body_v001\noak_tex_bark_v002\n")
        out = self.out_path("names-file.json")
        proc, payload = run(self.base("validate", "names", "--names-file", "names.txt", "--out", out))
        self.assertEqual(proc.returncode, EXIT_OK)
        self.assertEqual(payload["checked"], 2)

    def test_missing_names_is_invalid(self):
        proc, payload = run(self.base("validate", "names"))
        self.assertEqual(proc.returncode, EXIT_INVALID)


class TestMesh(Harness):
    def mesh(self, name):
        return os.path.join("fixtures", name)

    def test_inspect_cube(self):
        out = self.out_path("cube-inspect.json")
        proc, payload = run(self.base("mesh", "inspect", "--mesh", self.mesh("fixture-cube.obj"), "--out", out))
        self.assertEqual(proc.returncode, EXIT_OK, proc.stdout + proc.stderr)
        self.assertGreater(payload["triangles"], 0)
        self.assertTrue(payload["watertight"])
        self.assertEqual(payload["boundary_edges"], 0)
        self.assertAlmostEqual(payload["dimensions"][0], 1.0, places=3)

    def test_inspect_open_mesh_detects_boundary(self):
        proc, payload = run(self.base("mesh", "inspect", "--mesh", self.mesh("fixture-open.obj")))
        self.assertEqual(proc.returncode, EXIT_OK)
        self.assertGreater(payload["boundary_edges"], 0)
        self.assertFalse(payload["watertight"])

    def test_inspect_uv_on_sphere(self):
        proc, payload = run(self.base("mesh", "inspect", "--mesh", self.mesh("fixture-sphere.obj"), "--uv"))
        self.assertEqual(proc.returncode, EXIT_OK)
        self.assertIn("uv", payload)

    def test_validate_gate_fails_on_open_mesh(self):
        profile = os.path.join(self.tmp, "closed-profile.json")
        with open(profile, "w", encoding="utf-8") as fh:
            json.dump({"max_triangles": 100000, "require_closed": True,
                       "expected_height_m": 1.0, "check_uv": False, "fail_on": "error"}, fh)
        out = self.out_path("open-gate.json")
        proc, payload = run(self.base("mesh", "validate", "--mesh", self.mesh("fixture-open.obj"),
                                      "--profile", "closed-profile.json", "--out", out),
                            expect=EXIT_INVALID)
        self.assertEqual(payload["gate"], "fail")
        self.assertIn("open_boundary", payload["blocking_codes"])

    def test_validate_gate_passes_on_cube(self):
        profile = os.path.join(self.tmp, "cube-profile.json")
        with open(profile, "w", encoding="utf-8") as fh:
            json.dump({"max_triangles": 100000, "require_closed": True, "expected_height_m": 1.0,
                       "check_uv": False, "fail_on": "error"}, fh)
        out = self.out_path("cube-gate.json")
        proc, payload = run(self.base("mesh", "validate", "--mesh", self.mesh("fixture-cube.obj"),
                                      "--profile", "cube-profile.json", "--out", out))
        self.assertEqual(proc.returncode, EXIT_OK, proc.stdout + proc.stderr)
        self.assertEqual(payload["gate"], "pass")

    def test_scale_defect_detected(self):
        profile = os.path.join(self.tmp, "human-profile.json")
        with open(profile, "w", encoding="utf-8") as fh:
            json.dump({"max_triangles": 100000, "expected_height_m": 3.0, "check_uv": False,
                       "fail_on": "error"}, fh)
        proc, payload = run(self.base("mesh", "validate", "--mesh", self.mesh("fixture-cube.obj"),
                                      "--profile", "human-profile.json"), expect=EXIT_INVALID)
        self.assertIn("scale_defect", payload["blocking_codes"])

    def test_repair_fills_holes(self):
        out = self.out_path("repaired.obj")
        proc, payload = run(self.base("mesh", "repair", "--mesh", self.mesh("fixture-open.obj"),
                                      "--fill", "--out", out))
        self.assertEqual(proc.returncode, EXIT_OK, proc.stdout + proc.stderr)
        self.assertGreater(payload["operations"]["holes"]["filled_loops"], 0)
        self.assertTrue(os.path.exists(out))
        proc2, after = run(self.base("mesh", "inspect", "--mesh", out))
        self.assertEqual(after["boundary_edges"], 0)

    def test_convert_obj_to_glb_and_back(self):
        out = self.out_path("converted.glb")
        proc, payload = run(self.base("mesh", "convert", "--mesh", self.mesh("fixture-cube.obj"),
                                      "--to", "glb", "--out", out))
        self.assertEqual(proc.returncode, EXIT_OK, proc.stdout + proc.stderr)
        self.assertTrue(os.path.getsize(out) > 100)
        with open(out, "rb") as fh:
            self.assertEqual(fh.read(4), b"glTF")
        proc2, back = run(self.base("mesh", "inspect", "--mesh", out))
        self.assertEqual(proc2.returncode, EXIT_OK, proc2.stdout + proc2.stderr)
        self.assertEqual(back["triangles"], payload["triangles"])

    def test_convert_units_and_axis(self):
        out = self.out_path("cube-cm.glb")
        proc, payload = run(self.base("mesh", "convert", "--mesh", self.mesh("fixture-cube.obj"),
                                      "--to", "glb", "--from-unit", "m", "--to-unit", "cm",
                                      "--up-from", "Z", "--up-to", "Y", "--out", out))
        self.assertEqual(proc.returncode, EXIT_OK, proc.stdout + proc.stderr)
        self.assertEqual(len(payload["transforms"]), 2)
        proc2, info = run(self.base("mesh", "inspect", "--mesh", out))
        self.assertAlmostEqual(info["dimensions"][0], 100.0, places=2)

    def test_convert_stl(self):
        out = self.out_path("cube.stl")
        proc, payload = run(self.base("mesh", "convert", "--mesh", self.mesh("fixture-sphere.obj"),
                                      "--to", "stl", "--out", out))
        self.assertEqual(proc.returncode, EXIT_OK)
        self.assertGreater(os.path.getsize(out), 84)

    def test_decimate_reduces(self):
        out = self.out_path("decimated.obj")
        proc, payload = run(self.base("mesh", "decimate", "--mesh", self.mesh("fixture-sphere.obj"),
                                      "--target-tris", "300", "--out", out))
        self.assertEqual(proc.returncode, EXIT_OK, proc.stdout + proc.stderr)
        self.assertLessEqual(payload["report"]["end_triangles"], 300)
        self.assertGreater(payload["report"]["end_triangles"], 8)
        proc2, info = run(self.base("mesh", "inspect", "--mesh", out))
        self.assertLess(info["triangles"], 1000)

    def test_decimate_rejects_upward_target(self):
        proc, payload = run(self.base("mesh", "decimate", "--mesh", self.mesh("fixture-cube.obj"),
                                      "--target-tris", "999999"))
        self.assertEqual(proc.returncode, EXIT_INVALID)

    def test_subdivide(self):
        out = self.out_path("subd.obj")
        proc, payload = run(self.base("mesh", "subdivide", "--mesh", self.mesh("fixture-cube.obj"),
                                      "--levels", "1", "--out", out))
        self.assertEqual(proc.returncode, EXIT_OK, proc.stdout + proc.stderr)
        self.assertGreater(payload["after_triangles"], payload["before_triangles"])

    def test_remesh(self):
        out = self.out_path("remeshed.obj")
        proc, payload = run(self.base("mesh", "remesh", "--mesh", self.mesh("fixture-sphere.obj"),
                                      "--voxel-size", "0.08", "--out", out))
        self.assertEqual(proc.returncode, EXIT_OK, proc.stdout + proc.stderr)
        self.assertGreater(payload["report"]["end_triangles"], 100)

    def test_symmetry_report(self):
        proc, payload = run(self.base("mesh", "symmetry", "--mesh", self.mesh("fixture-cube.obj"),
                                      "--axis", "X"))
        self.assertEqual(proc.returncode, EXIT_OK)
        self.assertGreater(payload["matched_ratio"], 0.9)

    def test_symmetry_repair_requires_approval(self):
        proc, payload = run(self.base("mesh", "symmetry", "--mesh", self.mesh("fixture-cube.obj"),
                                      "--repair"), expect=EXIT_APPROVAL)
        self.assertEqual(payload["error"]["code"], "approval_required")

    def test_symmetry_repair_with_approval(self):
        out = self.out_path("symmetric.obj")
        proc, payload = run(self.base("mesh", "symmetry", "--mesh", self.mesh("fixture-open.obj"),
                                      "--repair", "--approve", "--out", out))
        self.assertEqual(proc.returncode, EXIT_OK, proc.stdout + proc.stderr)
        self.assertGreater(payload["after"]["matched_ratio"], 0.95)

    def test_sample_deterministic(self):
        out1 = self.out_path("samples-a.json")
        out2 = self.out_path("samples-b.json")
        run(self.base("mesh", "sample", "--mesh", self.mesh("fixture-sphere.obj"), "--count", "50",
                      "--seed", "5", "--out", out1))
        run(self.base("mesh", "sample", "--mesh", self.mesh("fixture-sphere.obj"), "--count", "50",
                      "--seed", "5", "--out", out2))
        with open(out1, encoding="utf-8") as fh:
            a = json.load(fh)
        with open(out2, encoding="utf-8") as fh:
            b = json.load(fh)
        self.assertEqual(a["points"], b["points"])

    def test_sample_different_seed_changes_result(self):
        out1 = self.out_path("samples-c.json")
        out2 = self.out_path("samples-d.json")
        run(self.base("mesh", "sample", "--mesh", self.mesh("fixture-sphere.obj"), "--count", "10",
                      "--seed", "1", "--out", out1))
        run(self.base("mesh", "sample", "--mesh", self.mesh("fixture-sphere.obj"), "--count", "10",
                      "--seed", "2", "--out", out2))
        with open(out1, encoding="utf-8") as fh:
            a = json.load(fh)
        with open(out2, encoding="utf-8") as fh:
            b = json.load(fh)
        self.assertNotEqual(a["points"], b["points"])

    def test_uv_analysis_on_sphere(self):
        proc, payload = run(self.base("mesh", "uv-analysis", "--mesh", self.mesh("fixture-sphere.obj")))
        self.assertEqual(proc.returncode, EXIT_OK, proc.stdout + proc.stderr)
        self.assertGreater(payload["uv_triangles"], 0)
        self.assertEqual(payload["flipped_uv_triangles"], 0)
        self.assertIn(payload["dominant_uv_orientation"], ("positive", "negative"))
        self.assertGreater(payload["texel_density_px_per_m"], 0)

    def test_uv_analysis_reports_missing_uv(self):
        # strip UVs by round-tripping through the JSON mesh format
        proc, payload = run(self.base("mesh", "convert", "--mesh", "fixtures/fixture-cube.obj",
                                      "--to", "json", "--out", self.out_path("nouvs.json")))
        self.assertEqual(proc.returncode, EXIT_OK)
        with open(self.out_path("nouvs.json"), "r", encoding="utf-8") as fh:
            data = json.load(fh)
        data["uvs"] = []
        data["face_uvs"] = []
        with open(self.out_path("nouvs.json"), "w", encoding="utf-8") as fh:
            json.dump(data, fh)
        proc2, payload2 = run(self.base("mesh", "uv-analysis", "--mesh", "outputs/nouvs.json"))
        self.assertEqual(proc2.returncode, EXIT_INVALID)
        self.assertEqual(payload2["error"]["code"], "invalid_input")

    def test_missing_mesh_is_invalid(self):
        proc, payload = run(self.base("mesh", "inspect", "--mesh", "fixtures/nope.obj"))
        self.assertEqual(proc.returncode, EXIT_INVALID)
        self.assertIn("does not exist", payload["error"]["message"])

    def test_corrupt_mesh_is_invalid(self):
        bad = os.path.join(self.tmp, "bad.obj")
        with open(bad, "w", encoding="utf-8") as fh:
            fh.write("# empty\n")
        proc, payload = run(self.base("mesh", "inspect", "--mesh", "bad.obj"))
        self.assertEqual(proc.returncode, EXIT_INVALID)

    def test_path_escape_blocked(self):
        proc, payload = run(self.base("mesh", "inspect", "--mesh", "../outside.obj"))
        self.assertEqual(proc.returncode, EXIT_INVALID)
        self.assertIn("..", payload["error"]["message"])

    def test_absolute_path_inside_workspace_allowed(self):
        absolute = os.path.join(self.tmp, "fixtures", "fixture-cube.obj")
        proc, payload = run(self.base("mesh", "inspect", "--mesh", absolute))
        self.assertEqual(proc.returncode, EXIT_OK, proc.stdout + proc.stderr)

    def test_absolute_path_outside_workspace_blocked(self):
        for absolute in ("/etc/hostname", "C:\\Windows\\win.ini"):
            proc, payload = run(self.base("mesh", "inspect", "--mesh", absolute))
            self.assertEqual(proc.returncode, EXIT_INVALID, absolute)
            self.assertIn("not allowed", payload["error"]["message"])

    def test_allow_outside_flag_permits_escape(self):
        outside = tempfile.mkdtemp(prefix="o3d-outside-")
        self.addCleanup(shutil.rmtree, outside, True)
        target = os.path.join(outside, "copy.obj")
        shutil.copy(os.path.join(self.tmp, "fixtures", "fixture-cube.obj"), target)
        proc, payload = run(self.base("mesh", "inspect", "--mesh", target, "--allow-outside"))
        self.assertEqual(proc.returncode, EXIT_OK, proc.stdout + proc.stderr)
        self.assertEqual(payload["triangles"], 192)

    def test_render_png_and_exr(self):
        png = self.out_path("hero.png")
        proc, payload = run(self.base("mesh", "render", "--mesh", self.mesh("fixture-sphere.obj"),
                                      "--width", "160", "--height", "120", "--samples", "1",
                                      "--pose", "hero", "--out", png))
        self.assertEqual(proc.returncode, EXIT_OK, proc.stdout + proc.stderr)
        self.assertTrue(os.path.exists(png))
        with open(png, "rb") as fh:
            self.assertEqual(fh.read(8), b"\x89PNG\r\n\x1a\n")
        self.assertGreater(payload["render"]["triangles_drawn"], 0)
        exr = self.out_path("hero.exr")
        proc2, payload2 = run(self.base("mesh", "render", "--mesh", self.mesh("fixture-sphere.obj"),
                                        "--width", "64", "--height", "48", "--samples", "1",
                                        "--still-format", "exr", "--out", exr))
        self.assertEqual(proc2.returncode, EXIT_OK, proc2.stdout + proc2.stderr)
        with open(exr, "rb") as fh:
            head = fh.read(8)
        self.assertEqual(head[4:6], b"\x02\x00")

    def test_render_deterministic(self):
        a = self.out_path("det-a.png")
        b = self.out_path("det-b.png")
        for target in (a, b):
            run(self.base("mesh", "render", "--mesh", self.mesh("fixture-cube.obj"),
                          "--width", "80", "--height", "60", "--samples", "1", "--out", target))
        with open(a, "rb") as fh:
            da = fh.read()
        with open(b, "rb") as fh:
            db = fh.read()
        self.assertEqual(da, db)

    def test_render_settings_change_output(self):
        a = self.out_path("pose-hero.png")
        b = self.out_path("pose-wire.png")
        run(self.base("mesh", "render", "--mesh", self.mesh("fixture-cube.obj"),
                      "--width", "80", "--height", "60", "--samples", "1", "--pose", "hero", "--out", a))
        run(self.base("mesh", "render", "--mesh", self.mesh("fixture-cube.obj"),
                      "--width", "80", "--height", "60", "--samples", "1", "--pose", "hero",
                      "--wireframe", "--out", b))
        with open(a, "rb") as fh:
            da = fh.read()
        with open(b, "rb") as fh:
            db = fh.read()
        self.assertNotEqual(da, db)

    def test_tiny_render_rejected(self):
        proc, payload = run(self.base("mesh", "render", "--mesh", self.mesh("fixture-cube.obj"),
                                      "--width", "4", "--height", "4"))
        self.assertEqual(proc.returncode, EXIT_INVALID)


class TestTexture(Harness):
    def test_generate_kinds(self):
        for kind in ("checker", "uv-layout", "height", "normal", "ao", "roughness", "mask"):
            out = self.out_path("tex-{0}.png".format(kind))
            proc, payload = run(self.base("texture", "generate", "--kind", kind,
                                          "--width", "64", "--height", "64", "--out", out))
            self.assertEqual(proc.returncode, EXIT_OK, proc.stdout + proc.stderr)
            self.assertTrue(os.path.exists(out))
            with open(out, "rb") as fh:
                self.assertEqual(fh.read(8), b"\x89PNG\r\n\x1a\n")

    def test_generate_rejects_bad_size(self):
        proc, payload = run(self.base("texture", "generate", "--kind", "checker",
                                      "--width", "4", "--height", "4"))
        self.assertEqual(proc.returncode, EXIT_INVALID)

    def test_pbr_set_and_audit(self):
        proc, payload = run(self.base("texture", "pbr-set", "--family", "metal",
                                      "--width", "64", "--height", "64", "--out", self.out_path("metal-maps")))
        self.assertEqual(proc.returncode, EXIT_OK, proc.stdout + proc.stderr)
        self.assertEqual(len(payload["maps"]), 6)
        base = self.out_path("metal-maps").replace("metal-maps", "metal-maps") + "/pbr-metal-basecolor.png"
        self.assertTrue(os.path.exists(base), base)
        proc2, audit = run(self.base("texture", "audit", "--image", "outputs/metal-maps/pbr-metal-basecolor.png",
                                     "--role", "basecolor", "--out", self.out_path("audit.json")))
        self.assertEqual(proc2.returncode, EXIT_OK, proc2.stdout + proc2.stderr)
        self.assertEqual(audit["width"], 64)
        self.assertIn("findings", audit)

    def test_normal_from_height(self):
        h = self.out_path("height.png")
        run(self.base("texture", "generate", "--kind", "height", "--width", "64", "--height", "64", "--out", h))
        n = self.out_path("normal.png")
        proc, payload = run(self.base("texture", "normal", "--height-map", "outputs/height.png",
                                      "--strength", "3", "--out", n))
        self.assertEqual(proc.returncode, EXIT_OK, proc.stdout + proc.stderr)
        proc2, audit = run(self.base("texture", "audit", "--image", "outputs/normal.png", "--role", "normal"))
        self.assertEqual(proc2.returncode, EXIT_OK)
        self.assertEqual(audit["channels"], 3)

    def test_pack_channels(self):
        a = self.out_path("ao.png")
        r = self.out_path("rough.png")
        m = self.out_path("metalmap.png")
        for kind, path in (("ao", a), ("roughness", r), ("metallic", m)):
            run(self.base("texture", "generate", "--kind", kind, "--width", "32", "--height", "32",
                          "--channels", "1", "--out", path))
        out = self.out_path("orm.png")
        proc, payload = run(self.base("texture", "pack",
                                      "--source", "ao=outputs/ao.png",
                                      "--source", "rough=outputs/rough.png",
                                      "--source", "metal=outputs/metalmap.png",
                                      "--layout", "R=ao:r,G=rough:r,B=metal:r",
                                      "--out", out))
        self.assertEqual(proc.returncode, EXIT_OK, proc.stdout + proc.stderr)
        self.assertEqual(payload["channels"], 3)
        proc2, audit = run(self.base("texture", "audit", "--image", "outputs/orm.png"))
        self.assertEqual(audit["width"], 32)

    def test_pack_rejects_mismatched_sizes(self):
        a = self.out_path("a32.png")
        b = self.out_path("b64.png")
        run(self.base("texture", "generate", "--kind", "ao", "--width", "32", "--height", "32",
                      "--channels", "1", "--out", a))
        run(self.base("texture", "generate", "--kind", "ao", "--width", "64", "--height", "64",
                      "--channels", "1", "--out", b))
        proc, payload = run(self.base("texture", "pack", "--source", "a=outputs/a32.png",
                                      "--source", "b=outputs/b64.png", "--layout", "R=a,G=b"))
        self.assertEqual(proc.returncode, EXIT_INVALID)

    def test_variant_modes_change_pixels(self):
        src = self.out_path("src.png")
        run(self.base("texture", "generate", "--kind", "basecolor", "--width", "48", "--height", "48",
                      "--out", src))
        outputs = {}
        for mode in ("worn", "wet", "dirty", "colorway", "damaged", "clean"):
            out = self.out_path("variant-{0}.png".format(mode))
            proc, payload = run(self.base("texture", "variant", "--image", "outputs/src.png",
                                          "--mode", mode, "--amount", "0.6", "--out", out))
            self.assertEqual(proc.returncode, EXIT_OK, proc.stdout + proc.stderr)
            with open(out, "rb") as fh:
                outputs[mode] = fh.read()
        self.assertEqual(len(set(outputs.values())), len(outputs))

    def test_mips(self):
        src = self.out_path("mip-src.png")
        run(self.base("texture", "generate", "--kind", "checker", "--width", "64", "--height", "64",
                      "--out", src))
        proc, payload = run(self.base("texture", "mips", "--image", "outputs/mip-src.png",
                                      "--levels", "3", "--out", self.out_path("mips")))
        self.assertEqual(proc.returncode, EXIT_OK, proc.stdout + proc.stderr)
        self.assertEqual(len(payload["written"]), 3)

    def test_audit_flags_non_power_of_two(self):
        src = self.out_path("odd.png")
        run(self.base("texture", "generate", "--kind", "checker", "--width", "96", "--height", "48",
                      "--out", src))
        proc, payload = run(self.base("texture", "audit", "--image", "outputs/odd.png"))
        codes = {f["code"] for f in payload["findings"]}
        self.assertIn("non_power_of_two", codes)

    def test_audit_missing_file(self):
        proc, payload = run(self.base("texture", "audit", "--image", "outputs/nope.png"))
        self.assertEqual(proc.returncode, EXIT_INVALID)


class TestAnim(Harness):
    def curves_file(self, name="curves.json"):
        path = os.path.join(self.tmp, name)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump({"root_z": [[1, 0.0], [12, 0.8], [24, 0.0]],
                       "hip_rot": [[1, 0.0], [24, 45.0]]}, fh)
        return path

    def test_sample_and_stats(self):
        self.curves_file()
        out = self.out_path("sampled.json")
        proc, payload = run(self.base("anim", "sample", "--keys", "curves.json", "--curve", "root_z",
                                      "--start", "1", "--end", "24", "--step", "6", "--out", out))
        self.assertEqual(proc.returncode, EXIT_OK, proc.stdout + proc.stderr)
        self.assertEqual(len(payload["keys"]), 4)
        self.assertAlmostEqual(payload["keys"][0][1], 0.0, places=4)
        proc2, stats = run(self.base("anim", "stats", "--keys", "curves.json", "--curve", "root_z"))
        self.assertEqual(stats["keys"], 3)
        self.assertAlmostEqual(stats["max"], 0.8, places=4)

    def test_easing_changes_result(self):
        self.curves_file()
        a = self.out_path("lin.json")
        b = self.out_path("ease.json")
        run(self.base("anim", "sample", "--keys", "curves.json", "--curve", "root_z",
                      "--easing", "linear", "--step", "6", "--out", a))
        run(self.base("anim", "sample", "--keys", "curves.json", "--curve", "root_z",
                      "--easing", "ease-in", "--step", "6", "--out", b))
        with open(a, encoding="utf-8") as fh:
            da = json.load(fh)
        with open(b, encoding="utf-8") as fh:
            db = json.load(fh)
        self.assertNotEqual(da["keys"], db["keys"])

    def test_reduce(self):
        self.curves_file()
        out = self.out_path("reduced.json")
        proc, payload = run(self.base("anim", "reduce", "--keys", "curves.json", "--curve", "root_z",
                                      "--tolerance", "0.001", "--out", out))
        self.assertEqual(proc.returncode, EXIT_OK)
        self.assertGreaterEqual(payload["report"]["keys_after"], 2)
        self.assertLessEqual(payload["report"]["keys_after"], payload["report"]["keys_before"])

    def test_loop(self):
        self.curves_file()
        out = self.out_path("loop.json")
        proc, payload = run(self.base("anim", "loop", "--keys", "curves.json", "--curve", "root_z",
                                      "--loop-frames", "24", "--loops", "2", "--out", out))
        self.assertEqual(proc.returncode, EXIT_OK)
        self.assertAlmostEqual(payload["report"]["seam_drift"], 0.0, places=6)

    def test_retime(self):
        self.curves_file()
        out = self.out_path("retimed.json")
        proc, payload = run(self.base("anim", "retime", "--keys", "curves.json", "--curve", "root_z",
                                      "--factor", "2", "--out", out))
        self.assertEqual(proc.returncode, EXIT_OK)
        self.assertAlmostEqual(payload["keys"][-1][0], 12.5, places=3)

    def test_gate_passes_clean_curves(self):
        path = os.path.join(self.tmp, "clean-curves.json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump({"root_z": [[1, 0.0], [120, 1.0]]}, fh)
        out = self.out_path("anim-gate.json")
        proc, payload = run(self.base("anim", "gate", "--curves", "clean-curves.json", "--out", out))
        self.assertEqual(proc.returncode, EXIT_OK, proc.stdout + proc.stderr)
        self.assertEqual(payload["gate"], "pass")

    def test_gate_detects_pop(self):
        path = os.path.join(self.tmp, "pop-curves.json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump({"root_z": [[1, 0.0], [2, 9.0], [3, 0.0], [120, 0.0]]}, fh)
        out = self.out_path("pop-gate.json")
        proc, payload = run(self.base("anim", "gate", "--curves", "pop-curves.json", "--out", out),
                            expect=EXIT_INVALID)
        self.assertIn("jitter_or_pop", payload["blocking_codes"])

    def test_gate_missing_file(self):
        proc, payload = run(self.base("anim", "gate", "--curves", "nope.json"))
        self.assertEqual(proc.returncode, EXIT_INVALID)

    def test_malformed_curve_input(self):
        path = os.path.join(self.tmp, "bad-curves.json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump({"root_z": [[1, 0.0], [2]]}, fh)
        proc, payload = run(self.base("anim", "stats", "--keys", "bad-curves.json"))
        self.assertEqual(proc.returncode, EXIT_INVALID)


class TestMocap(Harness):
    def test_inspect(self):
        out = self.out_path("bvh.json")
        proc, payload = run(self.base("mocap", "inspect", "--bvh", "fixtures/fixture-capture.bvh", "--out", out))
        self.assertEqual(proc.returncode, EXIT_OK, proc.stdout + proc.stderr)
        self.assertEqual(payload["frames"], 48)
        self.assertEqual(payload["fps"], 24)
        self.assertIn("Hips", payload["joint_names"])

    def test_contacts(self):
        proc, payload = run(self.base("mocap", "contacts", "--bvh", "fixtures/fixture-capture.bvh"))
        self.assertEqual(proc.returncode, EXIT_OK, proc.stdout + proc.stderr)
        self.assertIn("LeftFoot", payload["feet"])
        self.assertGreater(payload["feet"]["LeftFoot"]["plant_frames"], 0)

    def test_rootmotion(self):
        proc, payload = run(self.base("mocap", "rootmotion", "--bvh", "fixtures/fixture-capture.bvh"))
        self.assertEqual(proc.returncode, EXIT_OK, proc.stdout + proc.stderr)
        self.assertGreater(payload["planar_distance_m"], 0.1)

    def test_retarget(self):
        out = self.out_path("retarget.json")
        proc, payload = run(self.base("mocap", "retarget", "--source", "Hips:95,Spine:12",
                                      "--target", "Hips:104,Spine:14", "--out", out))
        self.assertEqual(proc.returncode, EXIT_OK, proc.stdout + proc.stderr)
        self.assertAlmostEqual(payload["scale"], 118.0 / 107.0, places=4)

    def test_retarget_length_mismatch(self):
        proc, payload = run(self.base("mocap", "retarget", "--source", "Hips:95", "--target", "Hips:1,Spine:2"))
        self.assertEqual(proc.returncode, EXIT_INVALID)

    def test_synth_then_parse(self):
        out = self.out_path("synth.bvh")
        proc, payload = run(self.base("mocap", "synth", "--frames", "24", "--fps", "30", "--out", out))
        self.assertEqual(proc.returncode, EXIT_OK, proc.stdout + proc.stderr)
        proc2, summary = run(self.base("mocap", "inspect", "--bvh", "outputs/synth.bvh"))
        self.assertEqual(proc2.returncode, EXIT_OK, proc2.stdout + proc2.stderr)
        self.assertEqual(summary["frames"], 24)
        self.assertEqual(summary["fps"], 30)

    def test_corrupt_bvh(self):
        bad = os.path.join(self.tmp, "bad.bvh")
        with open(bad, "w", encoding="utf-8") as fh:
            fh.write("HIERARCHY\nROOT Hips\n{\n}\n")
        proc, payload = run(self.base("mocap", "inspect", "--bvh", "bad.bvh"))
        self.assertEqual(proc.returncode, EXIT_INVALID)


class TestLipsync(Harness):
    def test_text_vtt(self):
        out = self.out_path("sync.vtt")
        proc, payload = run(self.base("lipsync", "text", "--text", "offline pipeline ready",
                                      "--captions", "vtt", "--out", out))
        self.assertEqual(proc.returncode, EXIT_OK, proc.stdout + proc.stderr)
        self.assertGreater(payload["meta"]["cue_count"], 5)
        with open(out, encoding="utf-8") as fh:
            text = fh.read()
        self.assertTrue(text.startswith("WEBVTT"))
        self.assertIn("-->", text)

    def test_text_both_captions(self):
        proc, payload = run(self.base("lipsync", "text", "--text", "hello world",
                                      "--captions", "both", "--out", self.out_path("both.json")))
        self.assertEqual(proc.returncode, EXIT_OK)
        self.assertIn("vtt", payload["written"])
        self.assertIn("srt", payload["written"])
        with open(self.out_path("both.srt"), encoding="utf-8") as fh:
            self.assertIn("-->", fh.read())

    def test_empty_text_rejected(self):
        proc, payload = run(self.base("lipsync", "text", "--text", "   "))
        self.assertEqual(proc.returncode, EXIT_INVALID)

    def test_audio_from_generated_wav(self):
        wav = os.path.join(self.tmp, "tone.wav")
        import math
        import struct
        import wave

        rate = 16000
        with wave.open(wav, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(rate)
            frames = bytearray()
            for i in range(int(rate * 1.2)):
                amp = 0.6 * math.sin(2 * math.pi * 220 * i / rate)
                env = 0.5 + 0.5 * math.sin(2 * math.pi * 3 * i / rate)
                frames.extend(struct.pack("<h", int(amp * env * 32000)))
            wf.writeframes(bytes(frames))
        out = self.out_path("audio-sync.json")
        proc, payload = run(self.base("lipsync", "audio", "--wav", "tone.wav", "--captions", "vtt",
                                      "--out", out))
        self.assertEqual(proc.returncode, EXIT_OK, proc.stdout + proc.stderr)
        self.assertGreater(payload["meta"]["cue_count"], 0)
        self.assertIn("vtt", payload["written"])


class TestPlan(Harness):
    def test_vram(self):
        out = self.out_path("vram.json")
        proc, payload = run(self.base("plan", "vram", "--triangles", "500000",
                                      "--texture", "4096x4096x3=albedo", "--samples", "128",
                                      "--device-gb", "8", "--out", out))
        self.assertEqual(proc.returncode, EXIT_OK, proc.stdout + proc.stderr)
        self.assertGreater(payload["peak_gib"], 0)
        self.assertIn("recommendation", payload)

    def test_vram_changes_with_input(self):
        a = self.out_path("vram-a.json")
        b = self.out_path("vram-b.json")
        run(self.base("plan", "vram", "--triangles", "1000", "--device-gb", "8", "--out", a))
        run(self.base("plan", "vram", "--triangles", "2000000", "--device-gb", "8", "--out", b))
        with open(a, encoding="utf-8") as fh:
            da = json.load(fh)
        with open(b, encoding="utf-8") as fh:
            db = json.load(fh)
        self.assertGreater(db["peak_gib"], da["peak_gib"])

    def test_ram_and_disk_and_render(self):
        for op, extra in [("ram", ["--triangles", "100000", "--sculpt-millions", "12"]),
                          ("disk", ["--frames", "240", "--available-gb", "120"]),
                          ("render", ["--frames", "240", "--seconds-per-frame", "12", "--machines", "4"])]:
            proc, payload = run(self.base("plan", op, "--out", self.out_path(op + ".json"), *extra))
            self.assertEqual(proc.returncode, EXIT_OK, proc.stdout + proc.stderr)

    def test_hardware_audit_fail(self):
        req = os.path.join(self.tmp, "req.json")
        rep = os.path.join(self.tmp, "rep.json")
        with open(req, "w", encoding="utf-8") as fh:
            json.dump({"vram_gb": 16, "ram_gb": 64}, fh)
        with open(rep, "w", encoding="utf-8") as fh:
            json.dump({"vram_gb": 8, "ram_gb": 32}, fh)
        proc, payload = run(self.base("plan", "hardware", "--requirements", "req.json",
                                      "--reported", "rep.json"), expect=EXIT_INVALID)
        self.assertEqual(payload["gate"], "fail")

    def test_hardware_audit_pass(self):
        req = os.path.join(self.tmp, "req2.json")
        rep = os.path.join(self.tmp, "rep2.json")
        with open(req, "w", encoding="utf-8") as fh:
            json.dump({"vram_gb": 8}, fh)
        with open(rep, "w", encoding="utf-8") as fh:
            json.dump({"vram_gb": 12}, fh)
        proc, payload = run(self.base("plan", "hardware", "--requirements", "req2.json",
                                      "--reported", "rep2.json"))
        self.assertEqual(proc.returncode, EXIT_OK)
        self.assertEqual(payload["gate"], "pass")


class TestLicenseAndIntegrity(Harness):
    def assets(self, extra=None):
        path = os.path.join(self.tmp, "assets.json")
        assets = {
            "assets": [
                {"id": "a1", "license": "cc0-1.0", "source": "local", "redistribute": True,
                 "model_training": True, "attribution": False},
                {"id": "a2", "license": "cc-by-4.0", "source": "local", "redistribute": True,
                 "model_training": True, "attribution": True},
                {"id": "a3", "license": "cc-by-nc-4.0", "source": "local", "redistribute": False,
                 "model_training": False, "attribution": True},
            ]
        }
        if extra:
            assets["assets"].append(extra)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(assets, fh)
        return path

    def test_license_audit_blocks_nc(self):
        self.assets()
        out = self.out_path("lic.json")
        proc, payload = run(self.base("license", "audit", "--assets", "assets.json", "--out", out),
                            expect=EXIT_INVALID)
        self.assertIn("no_commercial_use", payload["blocking_codes"])

    def test_license_audit_unknown_license_critical(self):
        self.assets({"id": "a4", "license": "mystery-license", "source": "web"})
        proc, payload = run(self.base("license", "audit", "--assets", "assets.json"), expect=EXIT_INVALID)
        self.assertIn("unknown_license", payload["blocking_codes"])

    def test_license_audit_clean(self):
        path = os.path.join(self.tmp, "clean-assets.json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump({"assets": [
                {"id": "c1", "license": "cc0-1.0", "source": "local", "redistribute": True,
                 "model_training": True, "attribution": False},
                {"id": "c2", "license": "mit", "source": "local", "redistribute": True,
                 "model_training": True, "attribution": True},
            ]}, fh)
        proc, payload = run(self.base("license", "audit", "--assets", "clean-assets.json"))
        self.assertEqual(proc.returncode, EXIT_OK, proc.stdout + proc.stderr)
        self.assertEqual(payload["gate"], "pass")

    def test_consent_audit(self):
        path = os.path.join(self.tmp, "consent.json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump({"records": [
                {"performer_id": "P1", "consent_date": "2026-01-01", "usage_scope": "body-mocap",
                 "territory": "worldwide", "term_start": "2026-01-01", "term_end": "2027-01-01",
                 "retention_policy": "destroy raw capture 2028-01-01", "model_training": True,
                 "training_permitted": False},
            ]}, fh)
        proc, payload = run(self.base("license", "consent", "--records", "consent.json"), expect=EXIT_INVALID)
        self.assertIn("training_not_permitted", payload["blocking_codes"])

    def test_provenance_record(self):
        target = os.path.join(self.out_path("hero.png"))
        run(self.base("mesh", "render", "--mesh", "fixtures/fixture-cube.obj", "--width", "32",
                      "--height", "32", "--samples", "1", "--out", target))
        proc, payload = run(self.base("license", "provenance", "--file", "outputs/hero.png",
                                      "--model", "local-texture-model", "--model-version", "2.1",
                                      "--seed", "42", "--license-id", "cc-by-4.0",
                                      "--out", self.out_path("prov.json")))
        self.assertEqual(proc.returncode, EXIT_OK, proc.stdout + proc.stderr)
        self.assertTrue(payload["completeness"]["complete"])
        self.assertEqual(payload["seed"], 42)
        self.assertEqual(len(payload["output_sha256"]), 64)

    def test_provenance_reports_missing_fields(self):
        target = os.path.join(self.out_path("hero.png"))
        if not os.path.exists(target):
            run(self.base("mesh", "render", "--mesh", "fixtures/fixture-cube.obj", "--width", "32",
                          "--height", "32", "--samples", "1", "--out", target))
        proc, payload = run(self.base("license", "provenance", "--file", "outputs/hero.png",
                                      "--out", self.out_path("prov2.json")))
        self.assertEqual(proc.returncode, EXIT_OK)
        self.assertFalse(payload["completeness"]["complete"])

    def test_install_manifest_detects_missing(self):
        entries = os.path.join(self.tmp, "entries.json")
        with open(entries, "w", encoding="utf-8") as fh:
            json.dump([{"file": "absent.tar.xz", "version": "1.0", "license": "mit"}], fh)
        os.makedirs(os.path.join(self.tmp, "bundle"), exist_ok=True)
        proc, payload = run(self.base("install", "manifest", "--entries", "entries.json",
                                      "--bundle-dir", "bundle"), expect=EXIT_INVALID)
        self.assertIn("installer_missing", payload["blocking_codes"])

    def test_hash_and_verify(self):
        out = self.out_path("checksums.json")
        proc, payload = run(self.base("hash", "--file", "fixtures/fixture-cube.obj", "--out", out))
        self.assertEqual(proc.returncode, EXIT_OK, proc.stdout + proc.stderr)
        self.assertEqual(len(payload["files"][0]["sha256"]), 64)
        manifest = os.path.join(self.tmp, "manifest.json")
        with open(out, encoding="utf-8") as fh:
            data = json.load(fh)
        with open(manifest, "w", encoding="utf-8") as fh:
            json.dump({"files": [{"path": f["path"], "sha256": f["sha256"]} for f in data["files"]]}, fh)
        proc2, verified = run(self.base("install", "verify", "--manifest", "manifest.json",
                                        "--base-dir", "."))
        self.assertEqual(proc2.returncode, EXIT_OK, proc2.stdout + proc2.stderr)
        self.assertEqual(verified["gate"], "pass")
        # tamper -> mismatch
        with open(manifest, "w", encoding="utf-8") as fh:
            json.dump({"files": [{"path": data["files"][0]["path"], "sha256": "0" * 64}]}, fh)
        proc3, tampered = run(self.base("install", "verify", "--manifest", "manifest.json",
                                        "--base-dir", "."), expect=EXIT_INVALID)
        self.assertIn("checksum_mismatch", tampered["blocking_codes"])


class TestProjectAndReports(Harness):
    def test_scaffold(self):
        proc, payload = run(self.base("scaffold", "--project", "hero-film", "--dir", "projects/hero",
                                      "--fps", "25"))
        self.assertEqual(proc.returncode, EXIT_OK, proc.stdout + proc.stderr)
        self.assertIn("00-admin", payload["folders"])
        self.assertTrue(os.path.isdir(os.path.join(self.tmp, "projects", "hero", "13-renders")))
        cfg_path = os.path.join(self.tmp, "projects", "hero", "00-admin", "project-config.json")
        self.assertTrue(os.path.exists(cfg_path))
        with open(cfg_path, encoding="utf-8") as fh:
            cfg = json.load(fh)
        self.assertEqual(cfg["fps"], 25)

    def test_package_gate_pass_and_fail(self):
        good = os.path.join(self.tmp, "delivery-good")
        os.makedirs(good, exist_ok=True)
        with open(os.path.join(good, "README.md"), "w", encoding="utf-8") as fh:
            fh.write("# delivery\n")
        with open(os.path.join(good, "mesh.obj"), "w", encoding="utf-8") as fh:
            fh.write("v 0 0 0\nv 1 0 0\nv 0 1 0\nf 1 2 3\n")
        proc, payload = run(self.base("validate", "package", "--dir", "delivery-good"))
        self.assertEqual(proc.returncode, EXIT_OK, proc.stdout + proc.stderr)
        self.assertEqual(payload["gate"], "pass")
        bad = os.path.join(self.tmp, "delivery-bad")
        os.makedirs(bad, exist_ok=True)
        open(os.path.join(bad, "empty.obj"), "w").close()
        proc2, payload2 = run(self.base("validate", "package", "--dir", "delivery-bad"), expect=EXIT_INVALID)
        self.assertIn("empty_file", payload2["blocking_codes"])
        self.assertIn("missing_readme", payload2["blocking_codes"])

    def test_report_markdown_and_html(self):
        gate = self.out_path("cube-gate.json")
        if not os.path.exists(gate):
            profile = os.path.join(self.tmp, "p.json")
            with open(profile, "w", encoding="utf-8") as fh:
                json.dump({"max_triangles": 100000, "check_uv": False}, fh)
            run(self.base("mesh", "validate", "--mesh", "fixtures/fixture-cube.obj",
                          "--profile", "p.json", "--out", gate))
        proc, payload = run(self.base("report", "--input", "outputs/cube-gate.json",
                                      "--title", "Geometry Gate", "--out", self.out_path("rep.md")))
        self.assertEqual(proc.returncode, EXIT_OK, proc.stdout + proc.stderr)
        with open(self.out_path("rep.md"), encoding="utf-8") as fh:
            text = fh.read()
        self.assertIn("# Geometry Gate", text)
        self.assertIn("|", text)
        proc2, payload2 = run(self.base("report", "--input", "outputs/cube-gate.json", "--html",
                                        "--out", self.out_path("rep.html")))
        self.assertEqual(proc2.returncode, EXIT_OK)
        with open(self.out_path("rep.html"), encoding="utf-8") as fh:
            html_text = fh.read()
        self.assertIn("<!DOCTYPE html>", html_text)
        self.assertIn("</html>", html_text)

    def test_contact_sheet(self):
        a = self.out_path("sheet-a.png")
        b = self.out_path("sheet-b.png")
        for pose, target in (("hero", a), ("top", b)):
            run(self.base("mesh", "render", "--mesh", "fixtures/fixture-cube.obj", "--width", "96",
                          "--height", "96", "--samples", "1", "--pose", pose, "--out", target))
        out = self.out_path("sheet.png")
        proc, payload = run(self.base("sheet", "--image", "hero=outputs/sheet-a.png",
                                      "--image", "top=outputs/sheet-b.png", "--columns", "2",
                                      "--cell", "96", "--out", out))
        self.assertEqual(proc.returncode, EXIT_OK, proc.stdout + proc.stderr)
        self.assertEqual(payload["items"], 2)
        with open(out, "rb") as fh:
            self.assertEqual(fh.read(8), b"\x89PNG\r\n\x1a\n")

    def test_turntable_sequence(self):
        out = self.out_path("turntable")
        proc, payload = run(self.base("review", "turntable", "--mesh", "fixtures/fixture-cube.obj",
                                      "--frames", "6", "--width", "64", "--height", "64",
                                      "--samples", "1", "--label", "review", "--out", out))
        self.assertEqual(proc.returncode, EXIT_OK, proc.stdout + proc.stderr)
        self.assertEqual(payload["frames"], 6)
        frames = sorted(f for f in os.listdir(out) if f.endswith(".png"))
        self.assertEqual(len(frames), 6)
        self.assertEqual(frames[0], "frame-0001.png")
        with open(os.path.join(out, frames[0]), "rb") as fh:
            self.assertEqual(fh.read(8), b"\x89PNG\r\n\x1a\n")

    def test_encode_degrades_gracefully_or_encodes(self):
        frames_dir = self.out_path("encode-frames")
        run(self.base("review", "turntable", "--mesh", "fixtures/fixture-cube.obj", "--frames", "4",
                      "--width", "32", "--height", "32", "--samples", "1", "--out", frames_dir))
        proc, payload = run(self.base("review", "encode", "--frames-dir", "outputs/encode-frames",
                                      "--fps", "24", "--out", self.out_path("review.mp4")))
        if payload["status"] == "ok":
            self.assertGreater(payload["bytes"], 1000)
            with open(self.out_path("review.mp4"), "rb") as fh:
                self.assertEqual(fh.read(4)[:4], b"\x00\x00\x00\x18"[:4] if False else fh.read(0) or b"")
            with open(self.out_path("review.mp4"), "rb") as fh:
                self.assertEqual(fh.read(12)[4:8], b"ftyp")
            self.assertEqual(proc.returncode, EXIT_OK)
        else:
            self.assertEqual(payload["status"], "dependency_unavailable")
            self.assertIn("offline_install_hint", payload)

    def test_encode_without_frames_is_invalid(self):
        empty = os.path.join(self.tmp, "empty-frames")
        os.makedirs(empty, exist_ok=True)
        proc, payload = run(self.base("review", "encode", "--frames-dir", "empty-frames"))
        self.assertEqual(proc.returncode, EXIT_INVALID)

    def test_render_is_not_black(self):
        out = self.out_path("brightness.png")
        run(self.base("mesh", "render", "--mesh", "fixtures/fixture-cube.obj", "--width", "64",
                      "--height", "64", "--samples", "1", "--out", out))
        import zlib
        with open(out, "rb") as fh:
            data = fh.read()
        # decode IHDR + IDAT with the stdlib to measure mean brightness
        width = int.from_bytes(data[16:20], "big")
        height = int.from_bytes(data[20:24], "big")
        pos = 8
        idat = b""
        while pos < len(data):
            length = int.from_bytes(data[pos:pos + 4], "big")
            ctype = data[pos + 4:pos + 8]
            body = data[pos + 8:pos + 8 + length]
            if ctype == b"IDAT":
                idat += body
            pos += 12 + length
        raw = zlib.decompress(idat)
        stride = width * 3 + 1
        total = 0
        count = 0
        for y in range(height):
            row = raw[y * stride + 1:(y + 1) * stride]
            total += sum(row)
            count += len(row)
        mean = total / float(count)
        self.assertGreater(mean, 12.0, "render came out too dark (mean {0:.1f})".format(mean))

    def test_sheet_labels_and_sizes(self):
        out = self.out_path("sheet2.png")
        proc, payload = run(self.base("sheet", "--image", "a=fixtures/fixture-pbr-basecolor.png",
                                      "--image", "b=fixtures/fixture-pbr-normal.png",
                                      "--columns", "2", "--cell", "48", "--out", out))
        self.assertEqual(proc.returncode, EXIT_OK, proc.stdout + proc.stderr)
        self.assertEqual(payload["width"], 96)
        self.assertEqual(payload["height"], 1 * (48 + 16))  # 2 items in 2 columns = 1 row

    def test_dry_run_writes_nothing(self):
        target = self.out_path("dry.png")
        proc, payload = run(self.base("mesh", "render", "--mesh", "fixtures/fixture-cube.obj",
                                      "--width", "32", "--height", "32", "--samples", "1",
                                      "--dry-run", "--out", target))
        self.assertEqual(proc.returncode, EXIT_OK)
        self.assertTrue(payload.get("dry_run"))
        self.assertFalse(os.path.exists(target))

    def test_timeout_respected(self):
        proc, payload = run(self.base("mesh", "render", "--mesh", "fixtures/fixture-sphere.obj",
                                      "--width", "64", "--height", "64", "--samples", "1",
                                      "--timeout", "0.0001", "--out", self.out_path("timeout.png")))
        self.assertEqual(proc.returncode, 4)
        self.assertEqual(payload["error"]["code"], "limit_exceeded")

    def test_bad_overrides_json(self):
        proc, payload = run(["--config", self.config, "--workspace", self.tmp,
                             "--overrides", "{not json", "doctor"])
        self.assertEqual(proc.returncode, EXIT_INVALID)

    def test_config_missing_section_rejected(self):
        broken = os.path.join(self.tmp, "broken-config.json")
        with open(broken, "w", encoding="utf-8") as fh:
            json.dump({"project": {}}, fh)
        proc, payload = run(["--config", broken, "--workspace", self.tmp, "doctor"])
        self.assertEqual(proc.returncode, EXIT_INVALID)


def main(argv):
    verbose = "-v" in argv
    argv = [a for a in argv if a not in ("-v", "--verbose")]
    only = None
    if "--only" in argv:
        idx = argv.index("--only")
        only = argv[idx + 1] if idx + 1 < len(argv) else None
        del argv[idx : idx + 2]
    loader = unittest.TestLoader()
    suite = loader.loadTestsFromModule(sys.modules[__name__])
    if only:
        needle = only.lower()
        def flatten(s):
            for item in s:
                if isinstance(item, unittest.TestSuite):
                    for sub in flatten(item):
                        yield sub
                else:
                    yield item
        selected = [t for t in flatten(suite) if needle in t.id().lower()]
        if not selected:
            print("No tests matched --only {0}".format(only))
            return 2
        suite = unittest.TestSuite(selected)
    runner = unittest.TextTestRunner(verbosity=2 if verbose else 1, buffer=not verbose)
    result = runner.run(suite)
    print("\nRan {0} tests: {1} ok, {2} failures, {3} errors, {4} skipped".format(
        result.testsRun, result.testsRun - len(result.failures) - len(result.errors),
        len(result.failures), len(result.errors), len(result.skipped)))
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
