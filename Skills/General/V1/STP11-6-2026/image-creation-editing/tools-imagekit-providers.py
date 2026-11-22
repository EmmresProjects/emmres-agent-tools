"""imagekit providers: optional adapters to external image models.

Nothing here is enabled by default and no credential is ever stored in the
package. Credentials are read from environment variables named in
asset-config-default.json. Network access is refused unless
providers.allow_network is true AND the single call is approved.

Two adapters ship:
  * mock-local  - offline, deterministic; exercises the whole adapter contract
                  (request build -> transport -> decode -> save -> provenance)
                  so the plumbing is testable without any external service.
  * generic-http-image - a template adapter for any JSON image API. It is
                  disabled until you set endpoint + env var names in your own
                  config file, and it has not been exercised against a live
                  endpoint in this package (see knowledge-readme.md).

Optional: for advanced multipart endpoints implement a subclass of
ProviderAdapter in a copy of this file; the CLI contract stays the same.
"""

from __future__ import annotations

import base64
import io
import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path

import imagekit_core as core


class ProviderAdapter:
    """Contract every adapter implements."""

    kind = "abstract"

    def __init__(self, name, cfg, ctx):
        self.name = name
        self.cfg = cfg or {}
        self.ctx = ctx

    def describe(self):
        return {"name": self.name, "kind": self.kind, "enabled": bool(self.cfg.get("enabled")),
                "description": self.cfg.get("description", "")}

    def check(self):
        raise NotImplementedError

    def generate(self, request):
        raise NotImplementedError


class MockProvider(ProviderAdapter):
    """Offline deterministic provider: renders a reference composition locally."""

    kind = "mock"

    def check(self):
        return {"ok": True, "detail": "offline mock adapter is always available",
                "produces": "deterministic reference composition sampled from the request prompt",
                "not_a_model": True}

    def generate(self, request):
        import imagekit_plan as plan
        import imagekit_render as render
        size = (int(request.get("width") or 1024), int(request.get("height") or 1024))
        prompt = request.get("prompt") or "abstract composition"
        cfg = self.ctx.config
        preset_key, preset = render.resolve_preset(cfg, request.get("preset"), request.get("style"), None)
        pp = plan.prompt_presets(cfg)
        palette = request.get("palette") or pp["palettes"][preset.get("palette_hint", "warm-neutral")]["colors"]
        seed = int(request.get("seed") or 1)
        img, scene = render.render_scene(size, preset, palette, seed)
        return {"image": img, "provider": self.name, "model": "imagekit-reference-compositor",
                "prompt_echo": prompt, "seed": seed, "preset": preset_key,
                "notice": "mock provider output: deterministic local composition, not a model generation"}


class GenericHttpProvider(ProviderAdapter):
    """Template JSON-over-HTTP adapter with timeouts, retries and size limits."""

    kind = "http"

    def check(self):
        problems = []
        if not self.cfg.get("endpoint"):
            problems.append("endpoint is empty")
        if not self.cfg.get("auth_env"):
            problems.append("auth_env is empty (name of the environment variable holding the key)")
        elif not os.environ.get(self.cfg["auth_env"]):
            problems.append(f"environment variable {self.cfg['auth_env']} is not set")
        if not self.ctx.config.providers.get("allow_network"):
            problems.append("providers.allow_network is false in config")
        extra = self.cfg.get("extra_headers_env") or {}
        for header, env_name in extra.items():
            if env_name and not os.environ.get(env_name):
                problems.append(f"environment variable {env_name} for header {header} is not set")
        return {"ok": not problems, "problems": problems,
                "endpoint_configured": bool(self.cfg.get("endpoint")),
                "note": "this adapter has not been exercised against a live endpoint in this package"}

    def build_request(self, request):
        template = self.cfg.get("request_template") or {}
        payload = _interpolate(template, {
            "prompt": request.get("prompt", ""),
            "negative": request.get("negative", ""),
            "width": request.get("width", 1024),
            "height": request.get("height", 1024),
            "seed": request.get("seed", 1),
        })
        headers = {"Content-Type": "application/json"}
        key = os.environ.get(self.cfg.get("auth_env", ""), "")
        if key:
            headers["Authorization"] = f"Bearer {key}"
        for header, env_name in (self.cfg.get("extra_headers_env") or {}).items():
            value = os.environ.get(env_name, "")
            if value:
                headers[header] = value
        return payload, headers

    def generate(self, request):
        check = self.check()
        if not check["ok"]:
            raise core.provider_error("Provider is not usable yet: " + "; ".join(check["problems"]),
                                      "Configure the provider in your --config file and export the key.",
                                      {"provider": self.name, "problems": check["problems"]})
        self.ctx.require_approval("provider.generate",
                                 "Sending prompts and settings to an external service that may bill and log requests.",
                                 {"provider": self.name})
        payload, headers = self.build_request(request)
        timeout = int(self.cfg.get("timeout_s", 120))
        retries = int(self.cfg.get("retries", 2))
        max_bytes = int(self.cfg.get("max_response_bytes", 40 * 1024 * 1024))
        last_error = None
        for attempt in range(retries + 1):
            if self.ctx.cancelled:
                raise core.ToolError("cancelled", "Cancelled before the provider call", "", core.EXIT_CANCELLED)
            try:
                data = json.dumps(payload).encode("utf-8")
                req = urllib.request.Request(self.cfg["endpoint"], data=data, headers=headers,
                                             method=self.cfg.get("method", "POST"))
                with urllib.request.urlopen(req, timeout=timeout) as response:
                    body = response.read(max_bytes + 1)
                    if len(body) > max_bytes:
                        raise core.provider_error("Provider response exceeded max_response_bytes",
                                                  "Raise the limit deliberately or request a smaller image.")
                    decoded = json.loads(body.decode("utf-8"))
                return self.decode(decoded, request)
            except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError) as exc:
                last_error = exc
                if attempt < retries:
                    time.sleep(min(8, 2 ** attempt))
                    continue
        raise core.provider_error(f"Provider call failed after {retries + 1} attempts: {last_error}",
                                  "Check endpoint, credentials, quota and network access.")

    def decode(self, response, request):
        b64 = _json_pointer(response, self.cfg.get("response_image_base64_pointer") or "")
        url = _json_pointer(response, self.cfg.get("response_image_url_pointer") or "") if not b64 else None
        raw = None
        if b64:
            try:
                raw = base64.b64decode(b64)
            except Exception as exc:
                raise core.provider_error(f"Could not decode base64 image: {exc}")
        elif url:
            try:
                with urllib.request.urlopen(url, timeout=int(self.cfg.get("timeout_s", 120))) as resp:
                    raw = resp.read(int(self.cfg.get("max_response_bytes", 40 * 1024 * 1024)))
            except Exception as exc:
                raise core.provider_error(f"Could not download the returned image URL: {exc}")
        if not raw:
            raise core.provider_error(
                "Provider response did not contain an image at the configured pointers",
                "Check response_image_base64_pointer / response_image_url_pointer against the API docs.")
        from PIL import Image
        img = Image.open(io.BytesIO(raw))
        img.load()
        return {"image": img.convert("RGBA"), "provider": self.name,
                "model": self.cfg.get("model_name", "unspecified"),
                "prompt_echo": request.get("prompt", ""), "seed": request.get("seed"),
                "raw_bytes": len(raw)}


def _interpolate(template, values):
    """Replace {{name}} placeholders, recursively."""
    if isinstance(template, dict):
        return {k: _interpolate(v, values) for k, v in template.items()}
    if isinstance(template, list):
        return [_interpolate(v, values) for v in template]
    if isinstance(template, str):
        out = template
        for key, value in values.items():
            out = out.replace("{{" + key + "}}", str(value))
        return out
    return template


def _json_pointer(data, pointer):
    if not pointer:
        return None
    node = data
    for part in pointer.strip("/").split("/"):
        if part == "":
            continue
        if isinstance(node, list):
            try:
                node = node[int(part)]
            except (ValueError, IndexError):
                return None
        elif isinstance(node, dict):
            if part not in node:
                return None
            node = node[part]
        else:
            return None
    return node


def get_adapter(name, ctx):
    providers = ctx.config.providers or {}
    registry = providers.get("list") or {}
    if name not in registry:
        raise core.usage_error(
            f"Unknown provider '{name}'",
            "Configured providers: " + (", ".join(sorted(registry)) or "none") +
            ". Add one under providers.list in your config file.")
    cfg = registry[name]
    kind = cfg.get("kind")
    if kind == "mock":
        return MockProvider(name, cfg, ctx)
    if kind == "http":
        return GenericHttpProvider(name, cfg, ctx)
    raise core.usage_error(f"Provider kind '{kind}' is not implemented",
                          "Implemented kinds: mock, http.")


def op_provider(ctx, a):
    action = (getattr(a, "action", None) or "list").lower()
    providers = ctx.config.providers or {}
    registry = providers.get("list") or {}
    if action == "list":
        rows = []
        for name, cfg in sorted(registry.items()):
            rows.append({"name": name, "kind": cfg.get("kind"),
                         "enabled": bool(cfg.get("enabled")),
                         "recommended": cfg.get("recommended"),
                         "description": cfg.get("description", "")})
        payload = {"operation": "provider", "action": "list",
                   "active": providers.get("active"),
                   "allow_network": bool(providers.get("allow_network")),
                   "providers": rows,
                   "note": "the reference compositor works offline; providers are optional"}
        return {"data": payload, "outputs": []}
    if action == "check":
        name = getattr(a, "name", None) or providers.get("active")
        if not name:
            raise core.usage_error("--action check needs --name PROVIDER (or providers.active in config)")
        adapter = get_adapter(name, ctx)
        result = adapter.check()
        return {"data": {"operation": "provider", "action": "check", "provider": name, **result}, "outputs": []}
    if action != "generate":
        raise core.usage_error("--action must be list, check or generate")
    name = getattr(a, "name", None) or providers.get("active")
    if not name:
        raise core.usage_error("--action generate needs --name PROVIDER",
                               "Configure providers.list in your config, then pass --name.")
    adapter = get_adapter(name, ctx)
    if not adapter.cfg.get("enabled", False):
        raise core.provider_error(f"Provider '{name}' is disabled in config",
                                  "Set providers.list.<name>.enabled = true in your config file.")
    request = {
        "prompt": getattr(a, "prompt_text", None) or "",
        "negative": getattr(a, "negative", None) or "",
        "width": (core.parse_size(getattr(a, "size", None) or "1024x1024"))[0],
        "height": (core.parse_size(getattr(a, "size", None) or "1024x1024"))[1],
        "seed": ctx.seed if ctx.seed is not None else int(getattr(a, "seed", 0) or 1),
        "preset": getattr(a, "preset", None),
        "style": getattr(a, "style", None),
        "palette": [c.strip() for c in str(a.palette).split(",")] if getattr(a, "palette", None) else None,
    }
    if not request["prompt"]:
        raise core.usage_error("--action generate needs --prompt TEXT",
                               "Use 'prompt' first to build one, or pass --prompt directly.")
    core.enforce_policy(ctx, request["prompt"], "provider-generate")
    result = adapter.generate(request)
    img = result.pop("image")
    size = core.check_size(ctx.config, core.parse_size(getattr(a, "size", None) or
                                                      f"{img.width}x{img.height}"))
    if img.size != size:
        img = img.resize(size, core._pillow().Resampling.LANCZOS)
    fmt = (getattr(a, "format", None) or "png").lower().lstrip(".")
    project = getattr(a, "project", None) or "provider"
    name_out = core.build_filename({"project": project, "content": getattr(a, "label", None) or name,
                                    "dimensions": size, "version": 1, "status": "draft"},
                                   fmt, ctx.config.naming)
    path = ctx.out_path(name_out, "." + fmt)
    meta = {"Software": f"imagekit {core.VERSION} via {name}",
            "Description": "Synthetic image produced by a configured external provider."}
    core.save_image(ctx, img, path, fmt, quality=94, dpi=(72, 72),
                    pnginfo=core.build_pnginfo(meta) if fmt == "png" else None,
                    exif=core.build_exif(meta) if fmt != "png" else None)
    sidecar = path.with_suffix(path.suffix + ".provenance.json")
    core.write_json(sidecar, {
        "record_version": 1, "asset": path.name, "created_at": core.now_iso(),
        "sha256": core.sha256_file(path), "provider": name, "kind": adapter.kind,
        "model": result.get("model"), "prompt": request["prompt"], "negative": request["negative"],
        "seed": request["seed"], "size": list(size),
        "disclosure": core.load_policy(ctx.config)["disclosures"]["synthesis"],
        "notice": result.get("notice") or "external provider output; review rights and terms of the provider",
    })
    ctx.audit("provider.generated", provider=name, size=size)
    payload = {"operation": "provider", "action": "generate", "provider": name,
               "kind": adapter.kind, "model": result.get("model"),
               "size": list(size), "format": fmt,
               "file": core.rel_posix(path, ctx.out_root),
               "provenance": core.rel_posix(sidecar, ctx.out_root),
               "seed": request["seed"],
               "notice": result.get("notice"),
               "checks": ["output reopened and saved by the tool",
                          "provenance sidecar written with the provider name and prompt"]}
    out_json = core.emit_json(ctx, core.slugify(f"{project}-provider-{name}-report"), payload)
    return {"data": payload, "outputs": [path, sidecar, out_json]}
