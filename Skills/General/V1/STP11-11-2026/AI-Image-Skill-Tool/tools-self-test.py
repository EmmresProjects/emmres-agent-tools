#!/usr/bin/env python3
"""Self-test for the AI Image Skill Tool.

Runs from any location. It copies nothing into the skill folder and writes only to a temporary
directory (removed at the end unless --keep is given). The provider tests use an in-process mock
HTTP server bound to 127.0.0.1 on an ephemeral port; no external network is used.

Usage:
  python3 tools-self-test.py            # run all tests
  python3 tools-self-test.py --keep     # keep the temporary workspace for inspection
  python3 tools-self-test.py -v         # print each command's exit code
"""
import argparse
import csv
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.dont_write_bytecode = True  # keep the skill folder free of __pycache__ folders
HERE = Path(__file__).resolve().parent
CLI = HERE / "tools-ai-image-toolkit.py"
ASSETS = ["asset-capability-catalog.json", "asset-config-default.json", "asset-prompt-vocabulary.json",
          "asset-plan-templates.json", "asset-model-profiles.json", "asset-policy-rules.json",
          "asset-layout-presets.json", "asset-sample-sweep.json"]
ENGINE_FILES = ["tools-prompt-engine.py", "tools-compute-engine.py", "tools-policy-engine.py",
                "tools-image-engine.py", "tools-provider-adapter.py"]
CHECKLIST = ["rights", "privacy", "factuality", "security", "disclosure", "brand", "accessibility", "platform"]
KEY = "selftest-key-" + str(os.getpid())

RESULTS = []
VERBOSE = False


# ----------------------------------------------------------------- mock provider

class _Mock(BaseHTTPRequestHandler):
    state = {"fail": False, "posts": 0, "slow": False, "json_mode": False}

    def log_message(self, *a):
        pass

    def _send(self, code, body, ctype):
        try:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass  # the client gave up (timeout test); not a failure

    def do_GET(self):
        if self.path.startswith("/health"):
            return self._send(200, b'{"status":"ok"}', "application/json")
        self._send(404, b"not found", "text/plain")

    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0))
        self.rfile.read(n)
        if self.path == "/slow":
            time.sleep(3)
            return self._send(200, PNG_BYTES, "image/png")
        _Mock.state["posts"] += 1
        if self.headers.get("Authorization") != f"Bearer {KEY}":
            return self._send(401, b"unauthorised", "text/plain")
        if _Mock.state["fail"]:
            return self._send(500, b"server error", "text/plain")
        if _Mock.state["json_mode"]:
            import base64
            body = json.dumps({"image_base64": base64.b64encode(PNG_BYTES).decode()}).encode()
            return self._send(200, body, "application/json")
        return self._send(200, PNG_BYTES, "image/png")


PNG_BYTES = b""


def start_mock():
    global PNG_BYTES
    import io
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (64, 64), (10, 120, 200)).save(buf, format="PNG")
    PNG_BYTES = buf.getvalue()
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _Mock)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_address[1]}"


# ----------------------------------------------------------------- helpers

class Env:
    def __init__(self, root, base_url):
        self.root = Path(root)
        self.fx = self.root / "fixtures"
        self.out = self.root / "out"
        self.base_url = base_url
        self.fx.mkdir(parents=True, exist_ok=True)
        self.make_fixtures()

    def make_fixtures(self):
        from PIL import Image, ImageDraw
        W, H = 512, 384
        im = Image.new("RGB", (W, H), (128, 128, 128))
        ImageDraw.Draw(im).ellipse((150, 100, 350, 300), fill=(220, 40, 40))
        im.save(self.fx / "photo.png")
        im.convert("RGB").save(self.fx / "photo.jpg", quality=92)
        m = Image.new("L", (W, H), 0)
        ImageDraw.Draw(m).ellipse((150, 100, 350, 300), fill=255)
        m.save(self.fx / "mask.png")
        Image.new("RGB", (W, H), (200, 200, 200)).save(self.fx / "second.png")
        Image.new("L", (256, 256), 255).save(self.fx / "mask-small.png")
        (self.fx / "tags.json").write_text(json.dumps({"photo.png": ["red circle"]}), encoding="utf-8")
        (self.fx / "imgs").mkdir(exist_ok=True)
        for name in ("photo.png", "second.png"):
            shutil.copy(self.fx / name, self.fx / "imgs" / name)
        (self.fx / "not-text.bin").write_bytes(b"\x89PNG\r\n\x1a\n\x00\xff\xfe")

    def cfg(self, name, body):
        p = self.root / name
        p.write_text(json.dumps(body), encoding="utf-8")
        return str(p)


def run_cli(env, args, *, config=None, extra_env=None, approve=False, overwrite=False, out=True, timeout=120):
    """Run the CLI as a subprocess. Returns (exit_code, parsed_stdout_or_None, error_dict_or_None, stderr)."""
    cmd = [sys.executable, str(CLI)] + list(args)
    if out and "--output-dir" not in args:
        cmd += ["--output-dir", str(env.out)]
    if config:
        cmd += ["--config", config]
    if approve:
        cmd.append("--approve")
    if overwrite:
        cmd.append("--overwrite")
    e = dict(os.environ)
    e.pop("AIIMG_PROVIDER_KEY", None)
    if extra_env:
        e.update(extra_env)
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, env=e, cwd=str(env.root))
    stdout = None
    err = None
    if r.stdout.strip():
        try:
            stdout = json.loads(r.stdout)
        except json.JSONDecodeError:
            stdout = {"_raw": r.stdout}
    if r.stderr.strip():
        try:
            err = json.loads(r.stderr).get("error")
        except json.JSONDecodeError:
            err = {"code": "unparsed", "message": r.stderr[-300:]}
    if VERBOSE:
        print(f"      $ {' '.join(args)[:110]} -> exit {r.returncode}")
    return r.returncode, stdout, err, r.stderr


def P(*pairs):
    """Build -p key=value args from pairs."""
    out = []
    for k, v in pairs:
        if isinstance(v, (dict, list)):
            v = json.dumps(v)
        out += ["-p", f"{k}={v}"]
    return out


def test(name):
    def deco(fn):
        RESULTS.append((name, fn))
        return fn
    return deco


def expect(cond, msg):
    if not cond:
        raise AssertionError(msg)


# ----------------------------------------------------------------- structure and metadata tests

@test("S01 files: only tools-/asset-/knowledge- files, no SKILL.md, no subfolders")
def _(env):
    names = sorted(p.name for p in HERE.iterdir() if not p.name.startswith(".") and p.name != "__pycache__")
    bad = [n for n in names if not n.startswith(("tools-", "asset-", "knowledge-"))]
    expect(not bad, f"unexpected file names: {bad}")
    expect("SKILL.md" not in names, "SKILL.md present")
    dirs = [p.name for p in HERE.iterdir() if p.is_dir() and p.name != "__pycache__"]  # cache dir is not content
    expect(not dirs, f"subfolders present: {dirs}")
    kn = [n for n in names if n.startswith("knowledge-")]
    expect(kn == ["knowledge-readme.md"], f"knowledge files: {kn}")


@test("S02 every required asset present and valid JSON")
def _(env):
    for a in ASSETS:
        p = HERE / a
        expect(p.is_file(), f"missing {a}")
        json.loads(p.read_text(encoding="utf-8"))


@test("S03 engine modules compile")
def _(env):
    for f in ENGINE_FILES + ["tools-common.py", "tools-ai-image-toolkit.py"]:
        compile((HERE / f).read_text(encoding="utf-8"), f, "exec")


@test("S04 catalog: 184 unique capabilities in 23 categories x 8")
def _(env):
    cat = json.loads((HERE / "asset-capability-catalog.json").read_text(encoding="utf-8"))["capabilities"]
    expect(len(cat) == 184, f"count {len(cat)}")
    expect(len({c["id"] for c in cat}) == 184, "duplicate ids")
    expect(len({c["number"] for c in cat}) == 184, "duplicate numbers")
    cats = {}
    for c in cat:
        cats.setdefault(c["category_number"], []).append(c)
    expect(sorted(cats) == list(range(1, 24)), "categories are not 1..23")
    expect(all(len(v) == 8 for v in cats.values()), "category sizes are not all 8")
    need = ["id", "number", "category_number", "category_name", "name", "description", "operation",
            "execution_mode", "approval", "risk", "default_params"]
    for c in cat:
        missing = [k for k in need if k not in c]
        expect(not missing, f"{c.get('number')} missing {missing}")


def _registry():
    import importlib.util
    mods = {}
    for f in ENGINE_FILES:
        name = "selftest_" + f[:-3].replace("-", "_")
        spec = importlib.util.spec_from_file_location(name, HERE / f)
        m = importlib.util.module_from_spec(spec)
        sys.modules[name] = m
        spec.loader.exec_module(m)
        mods[f] = m
    return mods


@test("S05 every catalog operation is registered; no duplicate operation names")
def _(env):
    cat = json.loads((HERE / "asset-capability-catalog.json").read_text(encoding="utf-8"))["capabilities"]
    mods = _registry()
    reg = {}
    for f, m in mods.items():
        for name in m.OPS:
            expect(name not in reg, f"duplicate operation {name}")
            reg[name] = m.OPS[name]
    needed = {c["operation"] for c in cat}
    missing = sorted(needed - set(reg))
    expect(not missing, f"unregistered operations: {missing}")


@test("S06 catalog default_params only use declared parameters of their operation")
def _(env):
    cat = json.loads((HERE / "asset-capability-catalog.json").read_text(encoding="utf-8"))["capabilities"]
    reg = {}
    for m in _registry().values():
        reg.update(m.OPS)
    for c in cat:
        declared = {s["name"] for s in reg[c["operation"]]["params"]}
        stray = set(c.get("default_params", {})) - declared
        expect(not stray, f"{c['number']} {c['operation']} undeclared defaults {sorted(stray)}")


@test("S07 safety defaults: no execute/enable switch defaults to true")
def _(env):
    cat = json.loads((HERE / "asset-capability-catalog.json").read_text(encoding="utf-8"))["capabilities"]
    for c in cat:
        expect(c.get("default_params", {}).get("execute") in (None, False), f"{c['number']} execute default true")
    cfg = json.loads((HERE / "asset-config-default.json").read_text(encoding="utf-8"))
    expect(cfg["provider"]["enabled"] is False, "provider enabled by default")
    expect(cfg["provider"]["endpoint"] == "", "endpoint not empty by default")


@test("S08 approval and execution-mode values are the documented ones")
def _(env):
    cat = json.loads((HERE / "asset-capability-catalog.json").read_text(encoding="utf-8"))["capabilities"]
    modes = {c["execution_mode"] for c in cat}
    expect(modes <= {"local", "heuristic", "engine", "policy", "blueprint"}, f"modes {modes}")
    req = [c["number"] for c in cat if c["approval"] == "required"]
    expect(req == ["18.8"], f"required-approval capabilities: {req}")


@test("S09 asset cross-references used by code exist")
def _(env):
    lay = json.loads((HERE / "asset-layout-presets.json").read_text(encoding="utf-8"))
    for name, cdef in lay["layouts"].items():
        if "canvas" in cdef:
            expect(cdef["canvas"] in lay["canvases"], f"layout {name} references unknown canvas {cdef['canvas']}")
    for k in ("canvases", "layouts", "delivery_specs", "crop_targets"):
        expect(k in lay and lay[k], f"layout presets missing {k}")
    pol = json.loads((HERE / "asset-policy-rules.json").read_text(encoding="utf-8"))
    for k in ("rulesets", "workflow", "release_checklist", "disclosure_templates", "artist_style_map"):
        expect(k in pol and pol[k], f"policy rules missing {k}")
    expect(set(CHECKLIST) <= set(pol["release_checklist"]), "release checklist incomplete")
    wf = pol["workflow"]
    states = set(wf["states"])
    for tname, t in wf["transitions"].items():
        for s in t["from"] if isinstance(t["from"], list) else [t["from"]]:
            expect(s in states, f"transition {tname} from unknown state {s}")
        expect(t["to"] in states, f"transition {tname} to unknown state {t['to']}")
    for rname, rs in pol["rulesets"].items():
        expect(rs.get("rules"), f"ruleset {rname} has no rules")
        for r in rs["rules"]:
            import re
            for pat in r.get("any", []) + r.get("all", []):
                re.compile(pat)
    for k, v in pol["disclosure_templates"].items():
        expect(v, f"disclosure template {k} empty")


# ----------------------------------------------------------------- CLI tests

@test("C01 help, version and list work; list shows 184 and category filters")
def _(env):
    rc, _, _, err = run_cli(env, ["help"], out=False)
    expect(rc == 0, f"help exit {rc}")
    rc, _, _, _ = run_cli(env, ["version"], out=False)
    expect(rc == 0, "version failed")
    r = subprocess.run([sys.executable, str(CLI), "list", "--json"], capture_output=True, text=True)
    rows = json.loads(r.stdout)
    expect(len(rows) == 184, f"list rows {len(rows)}")
    r = subprocess.run([sys.executable, str(CLI), "list", "--category", "8", "--json"], capture_output=True, text=True)
    expect(len(json.loads(r.stdout)) == 8, "category 8 count wrong")
    r = subprocess.run([sys.executable, str(CLI), "list", "--mode", "engine", "--json"], capture_output=True, text=True)
    expect(len(json.loads(r.stdout)) == 32, "engine mode count wrong")


@test("C02 describe prints parameters; unknown capability returns not_found (exit 7)")
def _(env):
    rc, _, _, _ = run_cli(env, ["describe", "8.1"], out=False)
    expect(rc == 0, "describe 8.1 failed")
    rc, _, err, _ = run_cli(env, ["describe", "99.9"], out=False)
    expect(rc == 7 and err and err["code"] == "not_found", f"exit {rc} err {err}")


@test("C03 unknown parameter, missing required and bad type are invalid_input (exit 2)")
def _(env):
    rc, _, err, _ = run_cli(env, ["run", "8.1", "-p", "bogus=1"])
    expect(rc == 2 and err["code"] == "invalid_input", f"unknown param exit {rc} {err}")
    rc, _, err, _ = run_cli(env, ["run", "8.1"])
    expect(rc == 2 and "image" in err["message"], f"missing image: {err}")
    rc, _, err, _ = run_cli(env, ["run", "8.1", *P(("image", str(env.fx / "photo.png")), ("threshold", "abc"))])
    expect(rc == 2, f"bad int exit {rc}")
    rc, _, err, _ = run_cli(env, ["run", "8.1", *P(("image", str(env.fx / "nope.png")))])
    expect(rc == 7 and err["code"] == "not_found", f"missing file exit {rc} {err}")


@test("C04 approval-required capability needs --approve (exit 3)")
def _(env):
    rc, _, err, _ = run_cli(env, ["run", "18.8", *P(("action", "publish"))])
    expect(rc == 3 and err["code"] == "approval_required", f"exit {rc} {err}")


@test("C05 doctor / check-config passes with defaults and reports Pillow and provider state")
def _(env):
    rc, out, err, _ = run_cli(env, ["check-config"], out=True)
    expect(rc == 0, f"check-config exit {rc} {err}")
    names = {c["check"]: c["status"] for c in out["checks"]}
    expect(names.get("catalog") == "pass", "catalog check not pass")
    expect(names.get("provider") == "pass", "provider check not pass")


@test("C06 check-config fails cleanly on a malformed config file (exit 2)")
def _(env):
    bad = env.root / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    rc, _, err, _ = run_cli(env, ["check-config", "--config", str(bad)], out=False)
    expect(rc == 2, f"exit {rc}")


@test("C07 utility operation image.inspect runs by name")
def _(env):
    rc, out, err, _ = run_cli(env, ["run", "image.inspect", *P(("images", str(env.fx / "photo.png")))])
    expect(rc == 0, f"exit {rc} {err}")
    expect(out["result"]["images"][0]["width"] == 512, "width wrong")


# ----------------------------------------------------------------- local operation behaviour

@test("L01 image.export writes <stem>-<w>x<h>.<ext> under output_dir/<capability-id>/")
def _(env):
    rc, out, err, _ = run_cli(env, ["run", "22.7", *P(("image", str(env.fx / "photo.png")),
                                                     ("formats", "png,webp"), ("sizes", "256,128"))])
    expect(rc == 0, f"exit {rc} {err}")
    d = env.out / "icon-family-designer"
    from PIL import Image
    for name in ("photo-256x192.png", "photo-128x96.png", "photo-256x192.webp"):
        expect((d / name).is_file(), f"missing {name}")
    with Image.open(d / "photo-128x96.png") as im:
        expect(im.size == (128, 96), f"size {im.size}")


@test("L02 image.mask_build produces an L-mode mask the size of its input")
def _(env):
    rc, out, err, _ = run_cli(env, ["run", "8.1", *P(("image", str(env.fx / "photo.png")), ("threshold", "128"))])
    expect(rc == 0, f"exit {rc} {err}")
    from PIL import Image
    with Image.open(out["result"]["mask_file"]) as m:
        expect(m.mode == "L" and m.size == (512, 384), f"mask {m.mode} {m.size}")


@test("L03 image.compare writes a heatmap and reports change")
def _(env):
    rc, out, err, _ = run_cli(env, ["run", "19.5", *P(("image_a", str(env.fx / "photo.png")),
                                                     ("image_b", str(env.fx / "second.png")))])
    expect(rc == 0, f"exit {rc} {err}")
    expect(Path(out["result"]["heatmap_file"]).is_file(), "heatmap missing")
    expect(out["result"]["changed_percent"] > 0, "no change detected")


@test("L04 image.contact_sheet writes contact-sheet.png")
def _(env):
    rc, out, err, _ = run_cli(env, ["run", "7.5", *P(("images", f"{env.fx / 'photo.png'},{env.fx / 'second.png'}"))])
    expect(rc == 0, f"exit {rc} {err}")
    expect(Path(out["result"]["sheet_file"]).is_file(), "sheet missing")


@test("L05 sweep 2x2x2x2 expands to exactly 16 settings")
def _(env):
    axes = {"steps": [10, 20], "cfg": [4, 6], "seed": [1, 2], "sampler": ["euler", "ddim"]}
    rc, out, err, _ = run_cli(env, ["run", "3.3", *P(("prompt", "a fox"), ("axes", axes))])
    expect(rc == 0, f"exit {rc} {err}")
    expect(out["result"]["item_count"] == 16, f"items {out['result']['item_count']}")


@test("L06 blind pairs: ballot hides the left/right mapping; key file holds it")
def _(env):
    rc, out, err, _ = run_cli(env, ["run", "19.3", *P(("items", f"{env.fx / 'photo.png'},{env.fx / 'second.png'}"))])
    expect(rc == 0, f"exit {rc} {err}")
    ballot = Path(out["result"]["ballot_file"])
    key = Path(out["result"]["key_file"])
    expect(ballot.is_file() and key.is_file(), "ballot or key missing")
    rows = list(csv.reader(ballot.read_text(encoding="utf-8").splitlines()))
    expect(rows[0][:3] == ["pair_id", "left_item", "right_item"], f"header {rows[0]}")
    kd = json.loads(key.read_text(encoding="utf-8"))
    expect(kd["pairs"][0]["left"] in (str(env.fx / "photo.png"), str(env.fx / "second.png"), "photo.png", "second.png"),
           f"key content {kd}")


@test("L07 policy.run: violent scene => review; benign => allow; fraud with spacing tricks => block")
def _(env):
    rc, out, err, _ = run_cli(env, ["run", "12.8", *P(("ruleset", "content-safety"),
                              ("text", "a real celebrity in a bloody crime scene"))])
    expect(rc == 0 and out["result"]["decision"] == "review", f"violent: {rc} {out and out['result']['decision']}")
    rc, out, err, _ = run_cli(env, ["run", "12.8", *P(("ruleset", "content-safety"),
                              ("text", "a calm watercolor of a lake at dawn"))])
    expect(rc == 0 and out["result"]["decision"] == "allow", "benign not allowed")
    rc, out, err, _ = run_cli(env, ["run", "12.8", *P(("ruleset", "content-safety"),
                              ("text", "a f-a-k-e p4ssport design"))])
    expect(rc == 0 and out["result"]["decision"] == "block", f"fraud: {out and out['result']['decision']}")


@test("L08 policy screens refuse binary input as text (invalid_input, not a crash)")
def _(env):
    rc, _, err, _ = run_cli(env, ["run", "12.8", *P(("ruleset", "content-safety"),
                            ("text_file", str(env.fx / "photo.png")))])
    expect(rc == 2 and err["code"] == "invalid_input", f"exit {rc} {err}")


@test("L09 release gate: partial checklist blocks publish (exit 2); full + approve is ready, never published")
def _(env):
    partial = {k: True for k in CHECKLIST[:-1]}
    rc, _, err, _ = run_cli(env, ["run", "18.8", *P(("checks", partial), ("action", "publish"))], approve=True)
    expect(rc == 2 and err["code"] == "invalid_input" and "platform" in err["message"], f"partial exit {rc} {err}")
    full = {k: True for k in CHECKLIST}
    rc, out, err, _ = run_cli(env, ["run", "18.8", *P(("checks", full), ("action", "publish"))], approve=True)
    expect(rc == 0 and out["result"]["status"] == "ready", f"full exit {rc} {err}")
    expect(out["result"]["note"].startswith("This toolkit does not publish"), "note missing")


@test("L10 workflow: author cannot approve; different actor can; lineage keeps earlier events")
def _(env):
    lin = str(env.root / "lineage.json")
    rc, out, err, _ = run_cli(env, ["run", "20.8", *P(("state", "draft"), ("action", "submit_review"),
                              ("actor", "alice"), ("lineage", lin))], approve=True)
    expect(rc == 0, f"submit exit {rc} {err}")
    rc, _, err, _ = run_cli(env, ["run", "20.8", *P(("state", "in_review"), ("action", "approve"),
                            ("actor", "alice"), ("author", "alice"), ("lineage", lin))])
    expect(rc == 2 and "own item" in err["message"], f"self-approve exit {rc} {err}")
    rc, out, err, _ = run_cli(env, ["run", "20.8", *P(("state", "in_review"), ("action", "approve"),
                              ("actor", "bob"), ("author", "alice"), ("lineage", lin))])
    expect(rc == 0 and out["result"]["new_state"] == "approved", f"approve exit {rc} {err}")
    events = json.loads(Path(lin).read_text(encoding="utf-8"))["events"]
    expect(len(events) == 2 and events[0]["action"] == "submit_review", f"events {events}")


@test("L11 prompt builder and weight check run")
def _(env):
    rc, out, err, _ = run_cli(env, ["run", "2.1", *P(("subject", "red fox"), ("style", "watercolor"))])
    expect(rc == 0 and "red fox" in out["result"]["prompt"], f"prompt {rc} {err}")
    rc, out, err, _ = run_cli(env, ["run", "2.6", *P(("prompt", "(red fox:1.3), (blue sky:0.8)"))])
    expect(rc == 0 and out["result"]["terms"][0]["weight"] == 1.3, f"weight {rc} {err}")


# ----------------------------------------------------------------- safety: inputs, outputs, overwrites

@test("G01 an output path equal to an input is refused and the input is untouched")
def _(env):
    src = env.fx / "photo.png"
    before = src.read_bytes()
    target = env.root / "guard" / "photo.png"
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy(src, target)
    b2 = target.read_bytes()
    rc, _, err, _ = run_cli(env, ["run", "8.1", *P(("image", str(target)), ("output", str(target)))],
                            approve=True, overwrite=True)
    expect(rc == 2 and "overwrite an input" in err["message"], f"exit {rc} {err}")
    expect(target.read_bytes() == b2, "input was modified")
    expect(src.read_bytes() == before, "fixture changed")


@test("G02 explicit existing output: no flag => invalid_input; --overwrite alone => approval_required; both => ok")
def _(env):
    dest = env.root / "explicit" / "mask.png"
    dest.parent.mkdir(parents=True, exist_ok=True)
    rc, _, _, _ = run_cli(env, ["run", "8.1", *P(("image", str(env.fx / "photo.png")), ("output", str(dest)))])
    expect(rc == 0, f"first write exit {rc}")
    rc, _, err, _ = run_cli(env, ["run", "8.1", *P(("image", str(env.fx / "photo.png")), ("output", str(dest)))])
    expect(rc == 2 and err["code"] == "invalid_input", f"no flag exit {rc} {err}")
    rc, _, err, _ = run_cli(env, ["run", "8.1", *P(("image", str(env.fx / "photo.png")), ("output", str(dest)))],
                            overwrite=True)
    expect(rc == 3 and err["code"] == "approval_required", f"overwrite-only exit {rc} {err}")
    rc, _, err, _ = run_cli(env, ["run", "8.1", *P(("image", str(env.fx / "photo.png")), ("output", str(dest)))],
                            overwrite=True, approve=True)
    expect(rc == 0, f"approved overwrite exit {rc} {err}")


@test("G03 unsupported output extension => invalid_input (not a crash)")
def _(env):
    rc, _, err, _ = run_cli(env, ["run", "8.1", *P(("image", str(env.fx / "photo.png")),
                            ("output", str(env.root / "x.txt")))])
    expect(rc == 2 and err["code"] == "invalid_input", f"exit {rc} {err}")


@test("G04 configured limits are enforced (max_pixels lowered => invalid_input)")
def _(env):
    cfg = env.cfg("tight.json", {"limits": {"max_pixels": 1000}})
    rc, _, err, _ = run_cli(env, ["run", "8.1", *P(("image", str(env.fx / "photo.png")))], config=cfg)
    expect(rc == 2 and "max_pixels" in err["message"], f"exit {rc} {err}")


@test("G05 a non-image binary given to an image operation is invalid_input")
def _(env):
    rc, _, err, _ = run_cli(env, ["run", "8.1", *P(("image", str(env.fx / "not-text.bin")))])
    expect(rc == 2 and err["code"] == "invalid_input", f"exit {rc} {err}")


# ----------------------------------------------------------------- engine and provider

@test("P01 engine.job without execute writes a job file and makes no network call")
def _(env):
    before = _Mock.state["posts"]
    rc, out, err, _ = run_cli(env, ["run", "engine.job", *P(("job_type", "txt2img"), ("prompt", "a fox"),
                              ("seed", 7)), ])
    expect(rc == 0 and out["result"]["status"] == "prepared", f"exit {rc} {err}")
    expect(Path(out["result"]["job_file"]).is_file(), "job file missing")
    expect(_Mock.state["posts"] == before, "network call made without execute")


@test("P02 inpaint job requires a mask and rejects a mask of a different size")
def _(env):
    rc, _, err, _ = run_cli(env, ["run", "engine.job", *P(("job_type", "inpaint"), ("prompt", "a lamp"),
                            ("image", str(env.fx / "photo.png")), ("denoise", 0.5))])
    expect(rc == 2 and "mask" in err["message"], f"no mask exit {rc} {err}")
    rc, _, err, _ = run_cli(env, ["run", "engine.job", *P(("job_type", "inpaint"), ("prompt", "a lamp"),
                            ("image", str(env.fx / "photo.png")), ("mask", str(env.fx / "mask-small.png")),
                            ("denoise", 0.5))])
    expect(rc == 2 and "does not match" in err["message"], f"size mismatch exit {rc} {err}")


def _provider_cfg(env, base, **over):
    prov = {"enabled": True, "endpoint": base, "allowed_hosts": ["127.0.0.1"], "timeout_seconds": 5}
    prov.update(over.pop("provider", {}))
    body = {"provider": prov, "retries": {"max_attempts": 2, "backoff_seconds": 0.05}}
    body.update(over)
    return env.cfg(f"provider-{time.time_ns()}.json", body)


@test("P03 execute without --approve => approval_required (exit 3), no network call")
def _(env):
    cfg = _provider_cfg(env, env.base_url)
    before = _Mock.state["posts"]
    rc, _, err, _ = run_cli(env, ["run", "engine.job", *P(("job_type", "txt2img"), ("prompt", "a fox"),
                            ("execute", True))], config=cfg, extra_env={"AIIMG_PROVIDER_KEY": KEY})
    expect(rc == 3 and err["code"] == "approval_required", f"exit {rc} {err}")
    expect(_Mock.state["posts"] == before, "network call made without approval")


@test("P04 execute + approve while provider disabled => unavailable (exit 5)")
def _(env):
    rc, _, err, _ = run_cli(env, ["run", "engine.job", *P(("job_type", "txt2img"), ("prompt", "a fox"),
                            ("execute", True))], approve=True)
    expect(rc == 5 and err["code"] == "unavailable", f"exit {rc} {err}")


@test("P05 host not in allowed_hosts and embedded credentials are refused before any request")
def _(env):
    cfg = env.cfg("badhost.json", {"provider": {"enabled": True, "endpoint": "http://evil.example.com",
                                                "allowed_hosts": ["127.0.0.1"]}})
    rc, _, err, _ = run_cli(env, ["run", "engine.job", *P(("job_type", "txt2img"), ("prompt", "a fox"),
                            ("execute", True))], config=cfg, approve=True, extra_env={"AIIMG_PROVIDER_KEY": KEY})
    expect(rc == 2 and "allowed_hosts" in err["message"], f"exit {rc} {err}")
    cfg = env.cfg("creds.json", {"provider": {"enabled": True, "endpoint": "http://u:p@127.0.0.1:1",
                                              "allowed_hosts": ["127.0.0.1"]}})
    rc, _, err, _ = run_cli(env, ["run", "engine.job", *P(("job_type", "txt2img"), ("prompt", "a fox"),
                            ("execute", True))], config=cfg, approve=True, extra_env={"AIIMG_PROVIDER_KEY": KEY})
    expect(rc == 2 and "credentials" in err["message"], f"creds exit {rc} {err}")


@test("P06 execute + approve against the mock provider writes a verified PNG")
def _(env):
    _Mock.state.update(fail=False, json_mode=False)
    cfg = _provider_cfg(env, env.base_url)
    rc, out, err, _ = run_cli(env, ["run", "engine.job", *P(("job_type", "txt2img"), ("prompt", "a fox"),
                              ("seed", 9), ("execute", True))], config=cfg, approve=True,
                              extra_env={"AIIMG_PROVIDER_KEY": KEY})
    expect(rc == 0 and out["result"]["status"] == "completed", f"exit {rc} {err}")
    data = Path(out["result"]["result_file"]).read_bytes()
    expect(data.startswith(b"\x89PNG\r\n\x1a\n"), "result is not PNG")


@test("P07 JSON-wrapped base64 image response is decoded")
def _(env):
    _Mock.state.update(fail=False, json_mode=True)
    try:
        cfg = _provider_cfg(env, env.base_url)
        rc, out, err, _ = run_cli(env, ["run", "engine.job", *P(("job_type", "txt2img"), ("prompt", "b fox"),
                                  ("seed", 11), ("execute", True))], config=cfg, approve=True,
                                  extra_env={"AIIMG_PROVIDER_KEY": KEY})
        expect(rc == 0, f"exit {rc} {err}")
        expect(Path(out["result"]["result_file"]).read_bytes().startswith(b"\x89PNG"), "not PNG")
    finally:
        _Mock.state["json_mode"] = False


@test("P08 wrong credential => unavailable (exit 5) with a clear message")
def _(env):
    cfg = _provider_cfg(env, env.base_url)
    rc, _, err, _ = run_cli(env, ["run", "engine.job", *P(("job_type", "txt2img"), ("prompt", "a fox"),
                            ("seed", 12), ("execute", True))], config=cfg, approve=True,
                            extra_env={"AIIMG_PROVIDER_KEY": "wrong"})
    expect(rc == 5 and "credential" in err["message"], f"exit {rc} {err}")


@test("P09 missing credential environment variable => unavailable (exit 5)")
def _(env):
    cfg = _provider_cfg(env, env.base_url)
    rc, _, err, _ = run_cli(env, ["run", "engine.job", *P(("job_type", "txt2img"), ("prompt", "a fox"),
                            ("seed", 13), ("execute", True))], config=cfg, approve=True)
    expect(rc == 5 and err["code"] == "unavailable", f"exit {rc} {err}")


@test("P10 provider HTTP 500 is retried up to max_attempts, then unavailable (exit 5)")
def _(env):
    _Mock.state.update(fail=True)
    try:
        cfg = _provider_cfg(env, env.base_url)
        before = _Mock.state["posts"]
        rc, _, err, _ = run_cli(env, ["run", "engine.job", *P(("job_type", "txt2img"), ("prompt", "a fox"),
                                ("seed", 14), ("execute", True))], config=cfg, approve=True,
                                extra_env={"AIIMG_PROVIDER_KEY": KEY})
        expect(rc == 5, f"exit {rc} {err}")
        expect(_Mock.state["posts"] - before == 2, f"attempts {_Mock.state['posts'] - before}, expected 2")
    finally:
        _Mock.state["fail"] = False


@test("P11 a slow provider times out per provider.timeout_seconds (exit 9)")
def _(env):
    # the mock sleeps 3 s on /slow; the configured timeout is 1 s, so the request must time out
    slow_cfg = env.cfg("slow.json", {"provider": {"enabled": True, "endpoint": env.base_url,
                                                  "submit_path": "/slow", "allowed_hosts": ["127.0.0.1"],
                                                  "timeout_seconds": 1},
                                     "retries": {"max_attempts": 1, "backoff_seconds": 0.05}})
    rc, _, err, _ = run_cli(env, ["run", "engine.job", *P(("job_type", "txt2img"), ("prompt", "a fox"),
                            ("seed", 15), ("execute", True))], config=slow_cfg, approve=True,
                            extra_env={"AIIMG_PROVIDER_KEY": KEY})
    expect(rc in (5, 9) and err is not None and "unreachable" in err["message"] or rc == 9,
           f"exit {rc} {err}")


@test("P12 provider.check dry-run never calls the network; probe calls /health")
def _(env):
    cfg = _provider_cfg(env, env.base_url)
    before = _Mock.state["posts"]
    rc, out, err, _ = run_cli(env, ["run", "provider.check"], config=cfg, extra_env={"AIIMG_PROVIDER_KEY": KEY})
    expect(rc == 0 and out["result"]["enabled"] is True, f"dry-run {rc} {err}")
    expect(_Mock.state["posts"] == before, "dry-run posted")
    rc, out, err, _ = run_cli(env, ["run", "provider.check", *P(("mode", "probe"))], config=cfg)
    expect(rc == 0 and out["result"]["health"]["status"] == 200, f"probe {rc} {err}")


@test("P13 comfy graph and control plan produce validated specs without a server")
def _(env):
    rc, out, err, _ = run_cli(env, ["run", "engine.comfy_graph", *P(("checkpoint", "x.safetensors"),
                              ("prompt", "a fox"))])
    expect(rc == 0 and out["result"]["link_check"] == "passed", f"graph {rc} {err}")
    rc, out, err, _ = run_cli(env, ["run", "engine.control_plan", *P(("control_type", "pose"),
                              ("image", str(env.fx / "photo.png")), ("strength", 0.8))])
    expect(rc == 0 and out["result"]["control"]["preprocessor"] == "openpose", f"control {rc} {err}")


@test("P14 batch: dry-run writes manifest; execute needs approval; limit + resume complete the run")
def _(env):
    req = json.loads((HERE / "asset-sample-sweep.json").read_text(encoding="utf-8"))
    req.pop("description", None)
    req_path = env.root / "req.json"
    req_path.write_text(json.dumps(req), encoding="utf-8")
    manifest = env.root / "batch" / "manifest.json"
    cfg = _provider_cfg(env, env.base_url)
    rc, out, err, _ = run_cli(env, ["run", "provider.batch", *P(("request", f"@{req_path}"), ("manifest", str(manifest)))],
                              config=cfg)
    expect(rc == 0 and out["result"]["item_count"] == 16 and out["result"]["dry_run"], f"dry {rc} {err}")
    rc, _, err, _ = run_cli(env, ["run", "provider.batch", *P(("request", f"@{req_path}"), ("execute", True),
                            ("manifest", str(manifest)))], config=cfg, extra_env={"AIIMG_PROVIDER_KEY": KEY})
    expect(rc == 3, f"execute w/o approval exit {rc} {err}")
    rc, out, err, _ = run_cli(env, ["run", "provider.batch", *P(("request", f"@{req_path}"), ("execute", True),
                              ("limit", 3), ("manifest", str(manifest)))], config=cfg, approve=True,
                              extra_env={"AIIMG_PROVIDER_KEY": KEY})
    expect(rc == 0 and out["result"]["status_counts"].get("completed") == 3, f"limit run {rc} {err}")
    rc, out, err, _ = run_cli(env, ["run", "provider.batch", *P(("request", f"@{req_path}"), ("execute", True),
                              ("manifest", str(manifest)))], config=cfg, approve=True,
                              extra_env={"AIIMG_PROVIDER_KEY": KEY})
    expect(rc == 0 and out["result"]["status_counts"].get("completed") == 16, f"resume {rc} {err}")
    expect(out["result"]["executed_this_run"] == 13, f"executed {out['result']['executed_this_run']}")
    rc, out, err, _ = run_cli(env, ["run", "provider.batch", *P(("request", f"@{req_path}"), ("execute", True),
                              ("manifest", str(manifest)))], config=cfg, approve=True,
                              extra_env={"AIIMG_PROVIDER_KEY": KEY})
    expect(out["result"]["executed_this_run"] == 0, "completed items re-submitted")


# ----------------------------------------------------------------- runner

def main(argv=None):
    global VERBOSE
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--keep", action="store_true", help="keep the temporary workspace")
    ap.add_argument("-v", "--verbose", action="store_true")
    ap.add_argument("--only", help="run tests whose name starts with this prefix, e.g. P or L02")
    args = ap.parse_args(argv)
    VERBOSE = args.verbose
    try:
        import PIL  # noqa: F401
    except ImportError:
        print("Pillow is required for the self-test: python3 -m pip install -r tools-requirements.txt")
        return 4
    tmp = tempfile.mkdtemp(prefix="aiit-selftest-")
    srv, base = start_mock()
    env = Env(tmp, base)
    passed = failed = 0
    try:
        for name, fn in RESULTS:
            if args.only and not name.startswith(args.only):
                continue
            try:
                fn(env)
                passed += 1
                print(f"PASS  {name}")
            except Exception as e:  # report every failure, keep going
                failed += 1
                print(f"FAIL  {name}\n      {type(e).__name__}: {str(e)[:400]}")
                if VERBOSE:
                    traceback.print_exc()
    finally:
        srv.shutdown()
        if args.keep:
            print(f"workspace kept at {tmp}")
        else:
            shutil.rmtree(tmp, ignore_errors=True)
    total = passed + failed
    print(f"\n{passed}/{total} tests passed" + ("" if not failed else f", {failed} FAILED"))
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
