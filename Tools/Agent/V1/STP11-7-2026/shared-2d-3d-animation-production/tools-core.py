"""Core library for the shared-2d-3d-animation-production Skill Tool.

Implements the offline, deterministic manifest/script capabilities from the
Shared 2D and 3D Animation Production Skill Catalog (November 2026 edition):
project scaffolding, configuration authoring/validation, naming compliance,
metadata sidecars, shot lists, version publication, changelogs, 3-2-1 backup
plans, checksum verification, delivery packaging, rights manifests, and the
final sign-off gate.

Standard library only. No network access is required or attempted.
"""

from __future__ import annotations

import csv
import hashlib
import json
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path

TOOL_VERSION = "1.0.0"

STANDARD_FOLDERS = [
    "development",
    "boards",
    "editorial",
    "assets",
    "shots",
    "scenes",
    "audio",
    "caches",
    "renders",
    "review",
    "delivery",
    "licenses",
    "archives",
]

CONFIG_FILENAME = "project-config.json"
VERSIONS_DIRNAME = "versions"
CHANGELOG_FILENAME = "CHANGELOG.json"
RIGHTS_FILENAME = "rights-manifest.json"
DELIVERY_MANIFEST = "delivery-manifest.json"

FRAME_RATES = {"23.976", "24", "25", "29.97", "30", "48", "50", "59.94", "60"}


class SkillError(Exception):
    """Structured, user-facing error."""

    def __init__(self, code: str, message: str, details: dict | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _read_json(path: Path) -> dict:
    if not path.is_file():
        raise SkillError("missing-file", f"Required file not found: {path}")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise SkillError("invalid-json", f"Invalid JSON in {path}: {exc}")


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=False) + "\n", encoding="utf-8")


def load_project_config(root: Path) -> dict:
    cfg = _read_json(root / CONFIG_FILENAME)
    validate_project_config(cfg, source=str(root / CONFIG_FILENAME))
    return cfg


def validate_project_config(cfg: dict, source: str = "config") -> list[str]:
    """Validate a project configuration; returns list of problems (empty = valid)."""
    problems: list[str] = []
    for key in ("project_name", "frame_rate", "resolution", "aspect_ratio", "color_space"):
        if key not in cfg:
            problems.append(f"{source}: missing required key '{key}'")
    name = cfg.get("project_name")
    if name is not None and not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", str(name)):
        problems.append(f"{source}: project_name must be lowercase hyphenated, got {name!r}")
    fr = str(cfg.get("frame_rate", ""))
    if fr and fr not in FRAME_RATES:
        problems.append(f"{source}: frame_rate {fr!r} not in supported set {sorted(FRAME_RATES)}")
    res = cfg.get("resolution")
    if res is not None:
        if not (isinstance(res, dict) and re.fullmatch(r"\d+", str(res.get("width", "")))
                and re.fullmatch(r"\d+", str(res.get("height", "")))):
            problems.append(f"{source}: resolution must have integer width/height")
    ar = cfg.get("aspect_ratio")
    if ar is not None and not re.fullmatch(r"\d+:\d+", str(ar)):
        problems.append(f"{source}: aspect_ratio must look like '16:9', got {ar!r}")
    return problems


def default_project_config(project_name: str) -> dict:
    return {
        "project_name": project_name,
        "frame_rate": "24",
        "resolution": {"width": 1920, "height": 1080},
        "aspect_ratio": "16:9",
        "color_space": "Rec.709",
        "timecode_start": "01:00:00:00",
        "created": utc_now(),
        "tool_version": TOOL_VERSION,
    }


# --- Naming compliance -------------------------------------------------------

ASSET_RE = re.compile(
    r"^[a-z0-9]+(?:-[a-z0-9]+)*_(?:[a-z0-9]+(?:-[a-z0-9]+)*)_v\d{3}\.[a-z0-9]+$"
)
SHOT_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*_s\d{3}t\d{2,}$")


def check_asset_filename(name: str) -> list[str]:
    """Validate an asset filename like 'hero-character_design_v003.png'."""
    problems = []
    if not name or name != name.strip():
        problems.append("leading/trailing whitespace")
    if " " in name or "_" in name.split(".")[0].replace("_", "_", 0) and False:
        pass
    if not ASSET_RE.match(name):
        problems.append(
            "must match '<slug>_<type>_v###.<ext>' lowercase hyphenated, e.g. hero-character_design_v003.png"
        )
    return problems


def check_shot_id(shot_id: str) -> list[str]:
    """Validate a shot id like 'seq01_s010t02'."""
    if not SHOT_RE.match(shot_id):
        return ["must match '<sequence>_s###t###' lowercase, e.g. seq01_s010t02"]
    return []


def check_naming(root: Path) -> dict:
    """Scan assets/ and shots/ for naming violations."""
    report = {"checked": 0, "violations": []}
    assets = root / "assets"
    if assets.is_dir():
        for p in sorted(assets.rglob("*")):
            if p.is_file() and p.suffix and not p.name.endswith(".json"):
                report["checked"] += 1
                probs = check_asset_filename(p.name)
                if probs:
                    report["violations"].append({"file": str(p.relative_to(root)), "problems": probs})
    shots = root / "shots"
    if shots.is_dir():
        for p in sorted(shots.rglob("*")):
            if p.is_dir() and p.name:
                report["checked"] += 1
                probs = check_shot_id(p.name)
                if probs:
                    report["violations"].append({"file": str(p.relative_to(root)), "problems": probs})
    report["ok"] = not report["violations"]
    return report


# --- Sidecar metadata --------------------------------------------------------

def write_sidecar(root: Path, asset_rel: str, metadata: dict) -> dict:
    asset = root / asset_rel
    if not asset.is_file():
        raise SkillError("missing-file", f"Asset not found: {asset}")
    sidecar = asset.with_suffix(asset.suffix + ".json")
    data = {
        "asset": asset.name,
        "relative_path": asset_rel,
        "sha256": sha256_file(asset),
        "metadata": metadata,
        "written": utc_now(),
    }
    _write_json(sidecar, data)
    return {"sidecar": str(sidecar.relative_to(root)), "sha256": data["sha256"]}


def verify_sidecars(root: Path) -> dict:
    report = {"checked": 0, "mismatches": []}
    assets = root / "assets"
    if assets.is_dir():
        for sc in sorted(assets.rglob("*.json")):
            if sc.name.endswith(".json") and (sc.parent / sc.name[: -len(".json")]).is_file():
                report["checked"] += 1
                data = _read_json(sc)
                target = sc.parent / sc.name[: -len(".json")]
                if data.get("sha256") != sha256_file(target):
                    report["mismatches"].append(str(target.relative_to(root)))
    report["ok"] = not report["mismatches"]
    return report


# --- Shot list ---------------------------------------------------------------

def build_shotlist(root: Path, rows: list[dict]) -> dict:
    for i, row in enumerate(rows, 1):
        shot_id = str(row.get("shot_id", ""))
        probs = check_shot_id(shot_id)
        if probs:
            raise SkillError("invalid-input", f"row {i}: bad shot_id {shot_id!r}: {'; '.join(probs)}")
        for key in ("scene", "description", "frames"):
            if key not in row:
                raise SkillError("invalid-input", f"row {i}: missing key '{key}'")
        if not re.fullmatch(r"\d+", str(row["frames"])):
            raise SkillError("invalid-input", f"row {i}: frames must be a positive integer")
    out = root / "shots" / "shot-list.json"
    payload = {"project": load_project_config(root).get("project_name"), "shots": rows,
               "total_frames": sum(int(r["frames"]) for r in rows), "written": utc_now()}
    _write_json(out, payload)
    return {"shotlist": str(out.relative_to(root)), "shots": len(rows),
            "total_frames": payload["total_frames"]}


def validate_shotlist(root: Path) -> dict:
    data = _read_json(root / "shots" / "shot-list.json")
    problems = []
    seen = set()
    for i, row in enumerate(data.get("shots", []), 1):
        sid = str(row.get("shot_id", ""))
        if check_shot_id(sid):
            problems.append(f"row {i}: invalid shot_id {sid!r}")
        if sid in seen:
            problems.append(f"row {i}: duplicate shot_id {sid!r}")
        seen.add(sid)
    return {"ok": not problems, "problems": problems, "shots": len(data.get("shots", []))}


# --- Versioning / changelog --------------------------------------------------

def publish_version(root: Path, version: str, note: str) -> dict:
    if not re.fullmatch(r"v\d{3}", version):
        raise SkillError("invalid-input", f"version must look like 'v001', got {version!r}")
    vdir = root / VERSIONS_DIRNAME / version
    vdir.mkdir(parents=True, exist_ok=True)
    manifest = {"version": version, "note": note, "published": utc_now(),
                "config": load_project_config(root)}
    _write_json(vdir / "version-manifest.json", manifest)
    log_path = root / CHANGELOG_FILENAME
    log = _read_json(log_path) if log_path.is_file() else {"entries": []}
    log["entries"].append({"version": version, "note": note, "time": utc_now()})
    _write_json(log_path, log)
    return {"version": version, "manifest": str((vdir / "version-manifest.json").relative_to(root)),
            "changelog_entries": len(log["entries"])}


# --- Backup plan / checksums --------------------------------------------------

def backup_plan(root: Path, copies: int = 3, destinations: list[str] | None = None) -> dict:
    if not (2 <= copies <= 5):
        raise SkillError("invalid-input", "copies must be between 2 and 5 (3-2-1 rule)")
    dests = destinations or ["primary-storage", "secondary-nas", "offsite-removable"][:copies]
    if len(dests) < 2:
        raise SkillError("invalid-input", "3-2-1 rule requires at least 2 distinct destinations")
    plan = {"rule": "3-2-1", "copies": copies, "media_types": min(copies, 3) and 2 or 2,
            "destinations": dests[:copies], "source": str(root), "created": utc_now()}
    out = root / "archives" / "backup-plan.json"
    _write_json(out, plan)
    return {"plan": str(out.relative_to(root)), "destinations": plan["destinations"]}


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def write_checksums(root: Path, subdir: str = "delivery") -> dict:
    base = root / subdir
    if not base.is_dir():
        raise SkillError("missing-file", f"Directory not found: {base}")
    entries = {}
    for p in sorted(base.rglob("*")):
        if p.is_file() and p.name != "checksums.sha256":
            entries[str(p.relative_to(base))] = sha256_file(p)
    out = base / "checksums.sha256"
    out.write_text("".join(f"{v}  {k}\n" for k, v in entries.items()), encoding="utf-8")
    return {"checksum_file": str(out.relative_to(root)), "files": len(entries)}


def verify_checksums(root: Path, subdir: str = "delivery") -> dict:
    base = root / subdir
    manifest = base / "checksums.sha256"
    if not manifest.is_file():
        raise SkillError("missing-file", f"Checksum file not found: {manifest}")
    report = {"verified": 0, "failed": []}
    for line in manifest.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        digest, _, rel = line.partition("  ")
        target = base / rel
        if not target.is_file() or sha256_file(target) != digest:
            report["failed"].append(rel)
        else:
            report["verified"] += 1
    report["ok"] = not report["failed"]
    return report


# --- Delivery packaging --------------------------------------------------------

def assemble_delivery(root: Path, files: list[str]) -> dict:
    cfg = load_project_config(root)
    missing = [f for f in files if not (root / f).is_file()]
    if missing:
        raise SkillError("missing-file", "delivery inputs missing", {"missing": missing})
    naming = check_naming(root)
    manifest = {
        "project": cfg["project_name"],
        "frame_rate": cfg["frame_rate"],
        "resolution": cfg["resolution"],
        "files": files,
        "naming_ok": naming["ok"],
        "assembled": utc_now(),
    }
    out = root / "delivery" / DELIVERY_MANIFEST
    _write_json(out, manifest)
    sums = write_checksums(root, "delivery")
    return {"manifest": str(out.relative_to(root)), "files": len(files),
            "naming_ok": naming["ok"], "checksums": sums["files"]}


# --- Rights / sign-off ---------------------------------------------------------

def rights_manifest(root: Path, entries: list[dict]) -> dict:
    for i, e in enumerate(entries, 1):
        if "asset" not in e or "license" not in e:
            raise SkillError("invalid-input", f"entry {i}: needs 'asset' and 'license'")
    manifest = {"entries": entries, "created": utc_now()}
    out = root / "licenses" / RIGHTS_FILENAME
    _write_json(out, manifest)
    return {"manifest": str(out.relative_to(root)), "entries": len(entries)}


def release_gate(root: Path, required_approvals: list[str] | None = None) -> dict:
    """Final sign-off gate: blocks release on missing rights or approvals."""
    required = required_approvals or ["director", "producer", "qc-lead"]
    blockers = []
    rights_path = root / "licenses" / RIGHTS_FILENAME
    if not rights_path.is_file():
        blockers.append("rights manifest missing (licenses/rights-manifest.json)")
    else:
        data = _read_json(rights_path)
        unlicensed = [e.get("asset") for e in data.get("entries", []) if str(e.get("license", "")).lower() in ("", "none", "unknown", "tbd")]
        if unlicensed:
            blockers.append(f"unlicensed assets: {', '.join(map(str, unlicensed))}")
    approvals_path = root / "review" / "approvals.json"
    approvals: dict = {}
    if approvals_path.is_file():
        approvals = _read_json(approvals_path).get("approvals", {})
    missing = [r for r in required if r not in approvals]
    if missing:
        blockers.append(f"missing approvals: {', '.join(missing)}")
    qc = check_naming(root)
    if not qc["ok"]:
        blockers.append(f"naming violations: {len(qc['violations'])}")
    return {"released": not blockers, "blockers": blockers}
