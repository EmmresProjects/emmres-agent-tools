#!/usr/bin/env python3
"""Media and file-size optimizer for the powerpoint-agent skill tool.

Capabilities
------------
* inventory every media part (format, pixels, bytes, references)
* downscale and recompress embedded raster images in place inside the package
  while preserving their format, so no relationship or content-type surgery is
  needed
* optionally drop media parts that no relationship references (zip-level
  rewrite, verified by reopening the deck)
* always reports before/after sizes and verifies the optimized file opens

Optimization is a write operation, so ``--approve`` is required and a dry run
is the default at the CLI layer.
"""

from __future__ import annotations

import io
import os
import shutil
import tempfile
import zipfile
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

CORE = None

RASTER_CONTENT_TYPES = {
    "image/jpeg",
    "image/png",
    "image/gif",
    "image/bmp",
    "image/tiff",
    "image/x-tiff",
    "image/webp",
}


def bind(core_module) -> None:
    global CORE
    CORE = core_module


def _image_size(blob: bytes) -> Tuple[int, int, str]:
    core = CORE
    if not core.PIL_AVAILABLE:
        return 0, 0, ""
    try:
        from PIL import Image  # type: ignore

        with Image.open(io.BytesIO(blob)) as image:
            return int(image.width), int(image.height), (image.format or "").upper()
    except Exception:
        return 0, 0, ""


def _part_slide_usage(prs) -> Dict[str, List[int]]:
    """Map media part name -> list of slide numbers that reference it."""

    usage: Dict[str, List[int]] = {}
    for index, slide in enumerate(prs.slides, start=1):
        try:
            rels = slide.part.rels
        except Exception:
            continue
        for rel in rels.values():
            try:
                target = rel.target_part
            except Exception:
                continue
            name = str(target.partname)
            usage.setdefault(name, [])
            if index not in usage[name]:
                usage[name].append(index)
    return usage


def referenced_parts(prs) -> set:
    """Every part name that some relationship in the package points at.

    A media part used only by the slide master, a layout, or a notes page is
    still referenced, so this must walk the whole package rather than slides.
    """

    referenced: set = set()
    try:
        parts = list(prs.part.package.iter_parts())
    except Exception:
        return referenced
    for part in parts:
        try:
            rels = part.rels
        except Exception:
            continue
        for rel in rels.values():
            try:
                target = rel.target_part
            except Exception:
                continue
            referenced.add(str(target.partname))
    return referenced


@dataclass
class MediaEntry:
    part: str
    content_type: str
    bytes: int
    width_px: int
    height_px: int
    image_format: str
    slide_refs: List[int] = field(default_factory=list)
    referenced: bool = True
    optimizable: bool = False

    def as_dict(self) -> Dict[str, Any]:
        return {
            "part": self.part,
            "content_type": self.content_type,
            "bytes": self.bytes,
            "width_px": self.width_px,
            "height_px": self.height_px,
            "format": self.image_format,
            "slide_refs": self.slide_refs,
            "referenced": self.referenced,
            "optimizable": self.optimizable,
        }


def inventory(prs, *, path: Optional[str] = None) -> Dict[str, Any]:
    """Return a media inventory for an opened presentation."""

    if not CORE.PIL_AVAILABLE:
        raise CORE.DependencyError(
            "Pillow is required for the media optimizer.",
            hint="Install dependencies with: python -m pip install -r tools-requirements.txt",
        )
    usage = _part_slide_usage(prs)
    referenced = referenced_parts(prs)
    entries: List[MediaEntry] = []
    for part in prs.part.package.iter_parts():
        name = str(part.partname)
        if not name.startswith("/ppt/media/"):
            continue
        try:
            blob = part.blob
            content_type = str(getattr(part, "content_type", ""))
        except Exception:
            continue
        width_px, height_px, fmt = _image_size(blob)
        refs = usage.get(name, [])
        entries.append(
            MediaEntry(
                part=name,
                content_type=content_type,
                bytes=len(blob),
                width_px=width_px,
                height_px=height_px,
                image_format=fmt,
                slide_refs=refs,
                referenced=name in referenced,
                optimizable=bool(width_px and content_type in RASTER_CONTENT_TYPES),
            )
        )
    entries.sort(key=lambda entry: -entry.bytes)
    total = sum(entry.bytes for entry in entries)
    unused = [entry for entry in entries if not entry.referenced]
    return {
        "file": os.path.basename(path) if path else "",
        "bytes": os.path.getsize(path) if path and os.path.isfile(path) else None,
        "media_count": len(entries),
        "media_bytes": total,
        "entries": [entry.as_dict() for entry in entries],
        "unused_parts": [entry.part for entry in unused],
        "unused_bytes": sum(entry.bytes for entry in unused),
    }


def _recompress(blob: bytes, content_type: str, *, max_dimension: int, jpeg_quality: int) -> Optional[bytes]:
    """Recompressed bytes in the same format, or ``None`` when not beneficial."""

    from PIL import Image  # type: ignore

    try:
        with Image.open(io.BytesIO(blob)) as image:
            image.load()
            width, height = image.size
            scale = min(1.0, float(max_dimension) / float(max(width, height))) if max_dimension else 1.0
            working = image
            if scale < 1.0:
                new_size = (max(1, int(round(width * scale))), max(1, int(round(height * scale))))
                working = image.resize(new_size, Image.LANCZOS)
            buffer = io.BytesIO()
            if content_type in ("image/jpeg",) or working.format in ("JPEG", "MPO"):
                rgb = working.convert("RGB") if working.mode not in ("RGB", "L") else working
                rgb.save(buffer, format="JPEG", quality=int(jpeg_quality), optimize=True, progressive=True)
            elif content_type == "image/png":
                if "A" in working.getbands():
                    working.save(buffer, format="PNG", optimize=True)
                else:
                    working.convert("P", palette=Image.ADAPTIVE).save(buffer, format="PNG", optimize=True)
            elif content_type in ("image/gif",):
                working.save(buffer, format="GIF", optimize=True)
            elif content_type in ("image/bmp",):
                working.save(buffer, format="BMP")
            elif content_type in ("image/tiff", "image/x-tiff"):
                working.save(buffer, format="TIFF", compression="tiff_deflate")
            elif content_type == "image/webp":
                working.save(buffer, format="WEBP", quality=int(jpeg_quality), method=6)
            else:
                return None
            data = buffer.getvalue()
    except Exception:
        return None
    if not data or len(data) >= len(blob):
        return None
    # never publish an image that cannot be decoded again
    check_w, check_h, _fmt = _image_size(data)
    if not check_w or not check_h:
        return None
    return data


def _drop_zip_entries(src: str, dst: str, drop: Sequence[str]) -> int:
    """Rewrite a package without the given zip entries; returns entries dropped."""

    drop_set = {name.lstrip("/") for name in drop}
    dropped = 0
    with zipfile.ZipFile(src, "r") as source, zipfile.ZipFile(dst, "w", zipfile.ZIP_DEFLATED) as target:
        for item in source.infolist():
            if item.filename in drop_set:
                dropped += 1
                continue
            data = source.read(item.filename)
            target.writestr(item, data)
    return dropped


def optimize_deck(
    path: str,
    out_path: str,
    *,
    config: Dict[str, Any],
    approve: bool = False,
    dry_run: bool = True,
    max_dimension: Optional[int] = None,
    jpeg_quality: Optional[int] = None,
    drop_unused: Optional[bool] = None,
    log=None,
    deadline=None,
    overwrite: bool = False,
    core=None,
) -> Dict[str, Any]:
    """Downscale/recompress media and optionally drop unreferenced media parts."""

    core = core or CORE
    core.require_pptx()
    core.require_pil()
    log = log or core.RunLog()

    if not dry_run and not approve:
        raise core.SafetyError(
            "Optimize rewrites the presentation and requires --approve.",
            hint="Run with --dry-run first, then rerun with --approve.",
            details={"command": "optimize"},
        )

    source = core.resolve_input(path)
    destination = core.resolve_output(out_path, overwrite=overwrite, exts=(".pptx",), label="output presentation")
    if os.path.abspath(source) == os.path.abspath(destination):
        raise core.SafetyError(
            "Optimize must write to a new file; choose a different --out path.",
            hint="The original file is never modified in place.",
        )

    settings = core.cfg(config, "optimize", {}) or {}
    max_dim = int(max_dimension if max_dimension is not None else settings.get("max_image_dimension_px", 1920))
    quality = int(jpeg_quality if jpeg_quality is not None else settings.get("jpeg_quality", 82))
    drop = bool(settings.get("drop_unused_media", False) if drop_unused is None else drop_unused)
    if not 30 <= quality <= 95:
        raise core.ValidationError("jpeg_quality must be between 30 and 95.")
    if not 320 <= max_dim <= 8000:
        raise core.ValidationError("max_image_dimension_px must be between 320 and 8000.")

    prs = core.open_presentation(source)
    inv = inventory(prs, path=source)
    changes: List[Dict[str, Any]] = []

    processable = [entry for entry in inv["entries"] if entry["optimizable"]]
    part_lookup = {}
    for part in prs.part.package.iter_parts():
        part_lookup[str(part.partname)] = part

    for entry in processable:
        if deadline:
            deadline.check("optimizing %s" % entry["part"])
        part = part_lookup.get(entry["part"])
        if part is None:
            continue
        blob = part.blob
        if max(entry["width_px"], entry["height_px"]) <= max_dim and not drop:
            # still attempt light recompression, but only when it is clearly useful
            pass
        new_blob = _recompress(blob, entry["content_type"], max_dimension=max_dim, jpeg_quality=quality)
        if not new_blob:
            continue
        new_w, new_h, _fmt = _image_size(new_blob)
        if not dry_run:
            part._blob = new_blob
        changes.append(
            {
                "part": entry["part"],
                "action": "recompress",
                "before_bytes": len(blob),
                "after_bytes": len(new_blob),
                "before_px": [entry["width_px"], entry["height_px"]],
                "after_px": [new_w, new_h],
                "saved_bytes": len(blob) - len(new_blob),
                "slide_refs": entry["slide_refs"],
            }
        )

    saved = sum(change["saved_bytes"] for change in changes)
    result: Dict[str, Any] = {
        "file": os.path.basename(source),
        "output": "",
        "output_path": "",
        "dry_run": bool(dry_run),
        "approved": bool(approve),
        "settings": {"max_image_dimension_px": max_dim, "jpeg_quality": quality, "drop_unused_media": drop},
        "inventory": {key: value for key, value in inv.items() if key != "entries"},
        "changes": changes,
        "change_count": len(changes),
        "saved_bytes": saved,
        "dropped_parts": [],
        "before_bytes": inv["bytes"] or os.path.getsize(source),
        "after_bytes": None,
        "verification": {},
    }

    if not changes and not (drop and inv["unused_parts"]):
        result["after_bytes"] = result["before_bytes"]
        result["verification"] = {"reopened": True, "slides": len(prs.slides), "note": "nothing to optimize"}
        return result

    directory = os.path.dirname(destination) or "."
    os.makedirs(directory, exist_ok=True)
    fd, staged = tempfile.mkstemp(prefix=".pptx-agent-opt-", suffix=".pptx", dir=directory)
    os.close(fd)
    try:
        core.save_presentation(prs, staged)
        dropped_parts: List[str] = []
        if drop and inv["unused_parts"]:
            fd2, rewritten = tempfile.mkstemp(prefix=".pptx-agent-opt2-", suffix=".pptx", dir=directory)
            os.close(fd2)
            dropped_parts = _drop_zip_entries(staged, rewritten, inv["unused_parts"])
            os.replace(rewritten, staged)
            result["dropped_parts"] = inv["unused_parts"]
        # verify the staged package before publishing
        verification = {"reopened": False, "slides": 0, "pictures_checked": 0, "errors": []}
        try:
            check = core.open_presentation(staged)
            verification["reopened"] = True
            verification["slides"] = len(check.slides)
            for slide in check.slides:
                for shape in core.iter_shapes(slide.shapes):
                    if str(getattr(shape, "shape_type", "")).startswith("PICTURE"):
                        try:
                            _w, _h, _fmt = _image_size(shape.image.blob)
                            verification["pictures_checked"] += 1
                        except Exception as exc:
                            verification["errors"].append(str(exc))
        except Exception as exc:
            verification["errors"].append("%s: %s" % (type(exc).__name__, exc))
        if verification["errors"]:
            raise core.AgentError(
                "Optimized package failed verification and was discarded.",
                hint="Retry with a larger max_dimension or without --drop-unused-media.",
                details=verification,
            )
        result["verification"] = verification
        result["projected_bytes"] = os.path.getsize(staged)
        result["after_bytes"] = None if dry_run else os.path.getsize(staged)
        if not dry_run:
            os.replace(staged, destination)
            result["output"] = os.path.basename(destination)
            result["output_path"] = destination
        else:
            os.unlink(staged)
    except Exception:
        if os.path.exists(staged):
            os.unlink(staged)
        raise

    result["image_saved_bytes"] = saved
    if dry_run:
        # A dry run cannot know the final package size, so report the projection
        # explicitly instead of implying the file was rewritten.
        result["saved_total_bytes"] = saved
        result["size_basis"] = "projected"
    else:
        result["saved_total_bytes"] = max(0, (result["before_bytes"] or 0) - (result["after_bytes"] or 0))
        result["size_basis"] = "measured"
    if result["before_bytes"]:
        result["saved_percent"] = round(100.0 * result["saved_total_bytes"] / result["before_bytes"], 1)
    return result


def optimize_report_markdown(result: Dict[str, Any]) -> str:
    core = CORE
    lines: List[str] = []
    lines.append("# Optimization report")
    lines.append("")
    lines.append("- Input: `%s`" % result.get("file", ""))
    lines.append("- Output: %s" % ("`%s`" % result["output"] if result.get("output") else "_dry run, nothing written_"))
    lines.append("- Settings: max edge %s px, JPEG quality %s" % (
        result["settings"]["max_image_dimension_px"], result["settings"]["jpeg_quality"]))
    lines.append("")
    lines.append("| Metric | Value |")
    lines.append("| --- | --- |")
    lines.append("| Size before | %s |" % core.human_bytes(result.get("before_bytes") or 0))
    if result.get("dry_run"):
        lines.append("| Projected after | %s |" % core.human_bytes((result.get("before_bytes") or 0) - (result.get("saved_total_bytes") or 0)))
    else:
        lines.append("| Size after | %s |" % core.human_bytes(result.get("after_bytes") or 0))
    lines.append("| Saved (%s) | %s (%s%%) |" % (
        "projected" if result.get("dry_run") else "measured",
        core.human_bytes(result.get("saved_total_bytes") or 0), result.get("saved_percent", 0.0)))
    lines.append("| Images recompressed | %s |" % result.get("change_count", 0))
    lines.append("| Unused parts dropped | %s |" % len(result.get("dropped_parts") or []))
    lines.append("")
    changes = result.get("changes") or []
    if changes:
        lines.append("## Image changes")
        lines.append("")
        lines.append("| Part | Before | After | Pixels before | Pixels after | Saved |")
        lines.append("| --- | --- | --- | --- | --- | --- |")
        for change in changes:
            lines.append(
                "| %s | %s | %s | %sx%s | %sx%s | %s |"
                % (
                    os.path.basename(change["part"]),
                    core.human_bytes(change["before_bytes"]),
                    core.human_bytes(change["after_bytes"]),
                    change["before_px"][0],
                    change["before_px"][1],
                    change["after_px"][0],
                    change["after_px"][1],
                    core.human_bytes(change["saved_bytes"]),
                )
            )
        lines.append("")
    unused = (result.get("inventory") or {}).get("unused_parts") or []
    if unused:
        lines.append("## Unreferenced media")
        lines.append("")
        for part in unused:
            lines.append("- `%s`" % part)
        lines.append("")
    verification = result.get("verification") or {}
    if verification:
        lines.append("## Verification")
        lines.append("")
        lines.append("- Staged package reopened: %s" % ("yes" if verification.get("reopened") else "no"))
        lines.append("- Slides in verified package: %s" % verification.get("slides", 0))
        lines.append("- Pictures decoded: %s" % verification.get("pictures_checked", 0))
        lines.append("")
    if result.get("dry_run"):
        lines.append("## Next step")
        lines.append("")
        lines.append("Review the projections, then rerun with `--approve` and without `--dry-run`.")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"
