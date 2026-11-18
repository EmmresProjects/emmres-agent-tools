#!/usr/bin/env python3
"""tools-offline-game-development.py -- the entry point of the offline-game-development Skill Tool.

What this Skill Tool is
    One unified tool over the whole catalog in
    offline-game-development/asset-catalog.json: 34 categories and 1,419 capabilities covering
    engine environment, gameplay, content creation, testing, optimization, packaging, stores,
    rights, and lifecycle operations for Godot, Unity, Unreal Engine, and Roblox Studio. The
    category is the umbrella; every capability the source document lists is reachable through
    the commands below, so nothing is split into a separate tool per category.

What it does
    Reads the catalog, writes the artifacts a team files (capability specs, sprint plans,
    checklists, evidence skeletons and verdicts, prompt packs, exports), and enforces the gates
    those artifacts name (rights R1-R10, performance budgets, evidence, offline posture, catalog
    coverage). It never runs an engine, compiler, device, or store command, never touches the
    network, and never executes code it is given.

Run it
    python3 offline-game-development/tools-offline-game-development.py <command> [args]
    python3 offline-game-development/tools-offline-game-development.py help
    python3 offline-game-development/tools-offline-game-development.py capabilities

Exit codes
    0 ok · 2 usage · 3 input or environment · 4 not found or ambiguous · 5 gate failed ·
    6 approval required · 7 cancelled · 8 internal · 9 limit or timeout · 10 integration unavailable
"""

from __future__ import annotations

import argparse
import io
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
PACKAGE = os.path.basename(HERE)
sys.path.insert(0, HERE)


def _load(filename: str, alias: str):
    import importlib.util

    if alias in sys.modules:
        return sys.modules[alias]
    path = os.path.join(HERE, filename)
    if not os.path.isfile(path):
        sys.stderr.write(f"E-ENVIRONMENT: missing {PACKAGE}/{filename}\n"
                         f"hint: every file of the Skill Tool folder must sit beside the entry point\n")
        raise SystemExit(3)
    spec = importlib.util.spec_from_file_location(alias, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[alias] = module
    spec.loader.exec_module(module)
    return module


kit = _load("tools-runtime-kit.py", "ogd_tools-runtime-kit")
engine = _load("tools-catalog-engine.py", "ogd_tools-catalog-engine")
generators = _load("tools-artifact-generators.py", "ogd_tools-artifact-generators")
gates = _load("tools-gates.py", "ogd_tools-gates")

TOOL_VERSION = kit.TOOL_VERSION
ENTRY_NAME = "tools-offline-game-development.py"
RUN = f"python3 {PACKAGE}/{ENTRY_NAME}"

# ---------------------------------------------------------------------------
# the capability list: every command, what it is for, and why it exists
# ---------------------------------------------------------------------------
CAPABILITIES = [
    ("info", "describe", "The Skill Tool, the catalog it is built on, its edition, fingerprint, engines, "
                          "layers, and the source document's acceptance reference."),
    ("capabilities", "describe", "This list: what the unified tool can do and which file implements it."),
    ("categories", "list", "The umbrella categories of the catalog with counts and layer, so a selection "
                           "can start from a category instead of an id."),
    ("list", "list", "Browse or filter capability records by category, layer, tag, difficulty, owner, "
                     "engine, or risk class, with paging."),
    ("show", "list", "The full record for one or more capabilities: description, tags, owner, estimate, "
                     "engines in scope, risk, and the catalog reference."),
    ("search", "list", "Ranked search over names, descriptions, and tags, with the terms that matched."),
    ("spec", "produce", "A capability spec: the archetype's section contract rendered as tables with the "
                        "team-owned rows left marked 'fill', plus the gates that block completion."),
    ("plan", "produce", "An ordered sprint plan for a selection: phases, owners, effort, dependencies, "
                        "gates, and the capabilities that need an approval token."),
    ("checklist", "produce", "Tick-box work list per capability, from the same sections and gates, optionally "
                             "ticked from an evidence file."),
    ("prompts", "produce", "Prompt packs for a local model only, with the context files to attach and the "
                           "answers that are forbidden; refused if network use is configured."),
    ("evidence", "verify", "new writes an evidence skeleton with one check per blocking section and gate; "
                           "check validates records against the schema and the gates and reports "
                           "contradictions."),
    ("rights", "gate", "The R1-R10 rights gate over an asset manifest: license known, provenance fields, "
                       "commercial use, redistribution, attribution, generated content, engines and SDKs, "
                       "record file, review age, approvals."),
    ("budget", "gate", "check compares measured values with the device profile ceilings; presets lists the "
                       "profiles and metrics available."),
    ("offline-audit", "gate", "Audits this Skill Tool's own folder and any extra files: flat layout and "
                              "naming, no network or exec tokens, no credential-shaped strings."),
    ("coverage", "gate", "Coverage of the dataset: per-category counts against the source's declared counts, "
                         "archetype, layer, owner, difficulty, engine, and risk distributions."),
    ("validate", "gate", "Structural validation of a dataset, and schema validation of generated artifacts "
                         "or any records file against a named record definition."),
    ("export", "export", "The selected or all records as CSV or JSON, in the documented column order."),
    ("import", "ingest", "Rebuild a dataset from plain text, Markdown, HTML, CSV, TSV, or JSON: tags, CSS, "
                         "and numbering ignored; real category names, skill names, and descriptions kept."),
    ("config", "configure", "show prints the merged configuration and where each layer came from; init "
                            "writes an editable copy of the packaged defaults."),
    ("doctor", "configure", "Twelve read-only checks that the install, the assets, the config, and the "
                            "documented commands are consistent."),
    ("cleanup", "maintain", "Find (and, with approval, delete) the .tmp-* leftovers of interrupted writes, "
                            "plus the .bak backups this tool made."),
    ("selftest", "maintain", "The unit suite that exercises every command, the gates, the config layering, "
                             "and the reference rules; run it after any local edit."),
    ("help", "describe", "Usage for one command: help <command>."),
]
COMMAND_NAMES = [name for name, _kind, _what in CAPABILITIES]
GROUP_LABELS = {"describe": "Tell you what is there", "list": "Find capabilities",
                "produce": "Write the artifacts a team files", "verify": "Record and check proof",
                "gate": "Enforce the gates the catalog names", "export": "Move data out",
                "ingest": "Bring data in", "configure": "Configure and diagnose",
                "maintain": "Maintain the tool"}


# ---------------------------------------------------------------------------
# context
# ---------------------------------------------------------------------------
class Ctx:
    """Everything a command handler needs, resolved once, with the limits already armed."""

    def __init__(self, args):
        self.args = args
        self.command = args.command
        self.package_dir = HERE
        self.skill_root = os.path.dirname(HERE)
        self.quiet = bool(args.quiet)
        self.loaded = {"tools-runtime-kit.py": kit, "tools-catalog-engine.py": engine,
                       "tools-artifact-generators.py": generators, "tools-gates.py": gates}
        self.commands = COMMAND_NAMES
        self.config_layers: list = []
        cfg, layers = self._load_config(args)
        # output.format from the config is authoritative: an explicit --format reached cfg via the
        # flag layer, and an absent one leaves the configured default standing
        self.fmt = str((cfg.get("output", {}) or {}).get("format") or "json")
        self.cfg = cfg
        self.config_layers = layers
        self.limits = cfg.get("limits", {})
        self.max_input_mb = self.limits.get("max_input_mb", kit.DEFAULT_MAX_INPUT_MB)
        self.assets_dir = self._resolve_assets_dir(args)
        self.paths = self._paths()
        self.deadline = kit.Deadline(self._limit_value(args.timeout, self.limits.get("timeout_seconds", 60),
                                                       "limits.timeout_seconds"))
        self.cancel = self.deadline.cancel
        if args.max_targets is not None:
            self.limits["max_targets"] = self._limit_value(args.max_targets, 400, "max_targets")
        if args.limit is not None:
            self.limits["max_list_rows"] = self._limit_value(args.limit, 0, "max_list_rows")
        output_cfg = cfg.get("output", {}) or {}
        self.stamp = bool(args.stamp) or bool(output_cfg.get("timestamp")) or \
            output_cfg.get("deterministic", True) is False
        self.redact = bool(cfg.get("output", {}).get("redact_secrets", True))
        self.engine = self._resolve_engine(args.engine or "")
        self.target = dict(cfg.get("target", {}))
        if args.platform:
            self.target["platform"] = args.platform
        if args.device_profile:
            self.target["device_profile"] = args.device_profile
        if args.region:
            self.target["region"] = args.region
        configured_dir = (cfg.get("output", {}) or {}).get("dir") or ""
        if args.out_dir_flag:
            configured_dir = args.out_dir_flag
        self.out_dir = os.path.abspath(os.path.expanduser(configured_dir)) if configured_dir else os.getcwd()
        self._assets_cache: dict = {}
        self.result = kit.Result(self.command, self.fmt)
        self._written: list = []

    # -- config and paths -------------------------------------------------
    def _load_config(self, args):
        layers = ["builtin"]
        config_path = args.config or ""
        default_asset = os.path.join(self.package_dir, "asset-config-default.json")
        cfg = json.loads(json.dumps(kit.BUILTIN_CONFIG))
        if os.path.isfile(default_asset):
            embedded = kit.load_json(default_asset, 8)
            if isinstance(embedded, dict):
                cfg = kit.deep_merge(cfg, {key: value for key, value in embedded.items()
                                           if not key.startswith("_")})
                layers.append(f"{PACKAGE}/asset-config-default.json")
        if config_path:
            candidate = config_path if os.path.isabs(config_path) else os.path.join(os.getcwd(), config_path)
            if not os.path.isfile(candidate):
                raise kit.InputError(f"config file not found: {config_path}",
                                     hint="pass a readable path, or omit --config to use "
                                          f"{PACKAGE}/asset-config-default.json", where=config_path)
            cfg = kit.deep_merge(cfg, kit.load_json(candidate, 8))
            layers.append(config_path)
        env_blob = (os.environ.get("OGD_CONFIG") or "").strip()
        if env_blob:
            if os.path.isfile(env_blob):
                cfg = kit.deep_merge(cfg, kit.load_json(env_blob, 8))
                layers.append("OGD_CONFIG file")
            else:
                try:
                    parsed = json.loads(env_blob)
                except ValueError as exc:
                    raise kit.InputError(f"OGD_CONFIG is not JSON or a readable path: {exc}",
                                         hint='use OGD_CONFIG=\'{"limits": {"timeout_seconds": 120}}\' or '
                                              "OGD_CONFIG=/path/config.json", where="OGD_CONFIG") from exc
                if isinstance(parsed, dict):
                    cfg = kit.deep_merge(cfg, parsed)
                    layers.append("OGD_CONFIG value")
        cfg = kit.deep_merge(cfg, self._flag_overlay(args))
        layers.append("flags")
        self._validate_config(cfg)
        return cfg, layers

    def _flag_overlay(self, args) -> dict:
        overlay: dict = {}

        def put(path, value):
            if value is None:
                return
            node = overlay
            parts = path.split(".")
            for part in parts[:-1]:
                node = node.setdefault(part, {})
            node[parts[-1]] = value

        put("target.platform", args.platform)
        put("target.device_profile", args.device_profile)
        put("target.region", args.region)
        put("engines.default", args.engine)
        put("output.format", None if args.format == "json" and not getattr(args, "format_explicit", False)
            else args.format)
        put("output.dir", args.out_dir_flag)
        put("output.timestamp", True if args.stamp else None)
        put("limits.timeout_seconds", args.timeout)
        put("limits.max_targets", args.max_targets)
        put("limits.max_input_mb", args.max_input_mb)
        put("limits.retries", args.retries)
        put("limits.max_list_rows", args.limit)
        put("provider.kind", args.provider_kind)
        put("provider.model_id", args.provider_model)
        put("provider.endpoint", args.provider_endpoint)
        put("dataset", args.dataset)
        return overlay

    def _validate_config(self, cfg: dict) -> None:
        """Fail before any work on a config that would misbehave later."""
        limits = cfg.get("limits") or {}
        for key, ceiling in (("timeout_seconds", 3600), ("max_targets", 5000), ("max_input_mb", 4096),
                             ("retries", 10), ("max_list_rows", 100000), ("walk_depth", 24)):
            if key not in limits:
                continue
            value = limits[key]
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise kit.UsageError(f"limits.{key} must be a number, found {type(value).__name__}",
                                     hint="edit the config file; the key is read by this tool before work "
                                          "starts", where=f"limits.{key}")
            if value < 0:
                raise kit.UsageError(f"limits.{key} must be zero or positive, found {value}",
                                     hint="zero means unlimited for max_list_rows and max_targets; use "
                                          "--config to change it", where=f"limits.{key}")
            if value > ceiling:
                raise kit.UsageError(f"limits.{key}={value} is above the sane ceiling {ceiling}",
                                     hint="this tool refuses a limit that would run unbounded; lower it",
                                     where=f"limits.{key}")
        engines = (cfg.get("engines") or {}).get("enabled")
        if not isinstance(engines, list) or not engines:
            raise kit.UsageError("engines.enabled must be a non-empty list",
                                 hint=f"choose from {', '.join(engine.ENGINE_IDS[1:])}", where="engines.enabled")
        unknown = sorted(set(engines) - set(engine.ENGINE_IDS))
        if unknown:
            raise kit.UsageError(f"engines.enabled contains unknown engines: {', '.join(unknown)}",
                                 hint=f"valid ids: {', '.join(engine.ENGINE_IDS)}", where="engines.enabled")
        offline = cfg.get("offline") or {}
        if not isinstance(offline.get("allow_network", False), bool):
            raise kit.UsageError("offline.allow_network must be true or false", where="offline.allow_network")
        approvals = cfg.get("approvals") or {}
        if not isinstance(approvals.get("require_for"), list):
            raise kit.UsageError("approvals.require_for must be a list of action names",
                                 where="approvals.require_for")
        provider = cfg.get("provider") or {}
        for key in ("kind", "model_id", "endpoint"):
            if key in provider and not isinstance(provider[key], str):
                raise kit.UsageError(f"provider.{key} must be a string", where=f"provider.{key}")

    def _resolve_assets_dir(self, args) -> str:
        configured = args.assets_dir or self.cfg.get("assets_dir") or "auto"
        if configured in ("auto", "", None):
            return HERE
        path = os.path.abspath(os.path.expanduser(str(configured)))
        if not os.path.isdir(path):
            raise kit.InputError(f"--assets-dir is not a directory: {path}",
                                 hint="point it at a folder holding the asset-*.json files, or use 'auto'",
                                 where="--assets-dir")
        return path

    def _paths(self) -> dict:
        paths = {}
        for key in ("dataset", "archetypes", "license_policy", "budget_presets", "output_schema",
                    "spec_template", "evidence_fixture"):
            raw = str(self.cfg.get(key) or "")
            if not raw:
                paths[key] = ""
                continue
            if os.path.isabs(raw) or raw.startswith((".", "/")):
                candidate = os.path.abspath(os.path.expanduser(raw))
                if not os.path.exists(candidate):
                    raise kit.NotFoundError(f"config {key} points at a missing file: {raw}",
                                            hint=f"the file should be in {PACKAGE}/ or the path corrected",
                                            where=raw)
                paths[key] = candidate
            else:
                paths[key] = gates.kit.resolve_asset(self.assets_dir, raw)
        return paths

    def _resolve_engine(self, requested: str) -> str:
        enabled = list(self.cfg.get("engines", {}).get("enabled") or [])
        allowed = ["engine-neutral"] + enabled
        if not requested:
            default = str(self.cfg.get("engines", {}).get("default") or "engine-neutral")
            if default not in allowed:
                raise kit.UsageError(f"engines.default {default!r} is disabled by engines.enabled",
                                     hint=f"this project accepts {', '.join(allowed)}; set engines.default "
                                          f"to one of those or enable it", where="engines.default")
            return default
        wanted = requested.strip().lower()
        if wanted not in engine.ENGINE_IDS:
            raise kit.UsageError(f"unknown engine {requested!r}",
                                 hint=f"valid ids: {', '.join(engine.ENGINE_IDS)}", where="--engine")
        if wanted not in allowed:
            raise kit.UsageError(f"engine {wanted!r} is disabled by config engines.enabled",
                                 hint=f"this project accepts {', '.join(allowed)}; change --engine or add the "
                                      f"engine to engines.enabled in your --config file", where="--engine")
        return wanted

    def _limit_value(self, flag_value, fallback, key: str):
        """0 means 'no cap' for the list limits, and is rejected for the timeout."""
        value = flag_value if flag_value is not None else fallback
        if value is None:
            return 0
        if key == "limits.timeout_seconds" and value <= 0:
            raise kit.UsageError("a timeout of zero would never fire",
                                 hint="omit --timeout for the config value, or pass a positive number of "
                                      "seconds", where="--timeout")
        return value

    # -- lazily loaded data ----------------------------------------------
    def archetypes_raw(self) -> dict:
        if "archetypes" not in self._assets_cache:
            self._assets_cache["archetypes"] = kit.load_json(self.paths["archetypes"], self.max_input_mb,
                                                             self.deadline)
        return self._assets_cache["archetypes"]

    def catalog(self) -> engine.Catalog:
        if "catalog" not in self._assets_cache:
            arch = self.archetypes_raw()
            dataset, findings = engine.load_catalog(self.paths["dataset"], arch, self.max_input_mb,
                                                    self.deadline)
            self._assets_cache["findings"] = findings
            self._assets_cache["catalog"] = engine.Catalog(dataset, arch)
        return self._assets_cache["catalog"]

    def policy(self) -> dict:
        if "policy" not in self._assets_cache:
            self._assets_cache["policy"] = kit.load_json(self.paths["license_policy"], self.max_input_mb,
                                                         self.deadline)
        return self._assets_cache["policy"]

    def budgets(self) -> dict:
        if "budgets" not in self._assets_cache:
            self._assets_cache["budgets"] = kit.load_json(self.paths["budget_presets"], self.max_input_mb,
                                                          self.deadline)
        return self._assets_cache["budgets"]

    def schema(self) -> dict:
        if "schema" not in self._assets_cache:
            self._assets_cache["schema"] = gates.load_schema(self.paths["output_schema"], 8, self.deadline)
        return self._assets_cache["schema"]

    def render_context(self) -> generators.RenderContext:
        if "render_ctx" not in self._assets_cache:
            template = ""
            if self.paths.get("spec_template") and os.path.isfile(self.paths["spec_template"]):
                template = kit.read_text(self.paths["spec_template"], self.max_input_mb, self.deadline)
            catalog = self.catalog()
            ctx = generators.RenderContext(catalog, self.archetypes_raw(), self.policy(), self.budgets(),
                                           self.cfg, self.engine, template)
            ctx.platform = self.target.get("platform", ctx.platform)
            ctx.device_profile = self.target.get("device_profile", ctx.device_profile)
            ctx.region = self.target.get("region", ctx.region)
            self._assets_cache["render_ctx"] = ctx
        return self._assets_cache["render_ctx"]

    # -- selection --------------------------------------------------------
    def records(self, tokens, allow_all: bool = False, require: bool = True) -> list:
        catalog = self.catalog()
        tokens = [token for token in (tokens or []) if str(token).strip()]
        if getattr(self.args, "all", False) or allow_all and not tokens:
            if getattr(self.args, "category", None):
                rows, _ = catalog.select([], require=False)
                rows = catalog.all_for(category_id=self.args.category, layer=getattr(self.args, "layer", ""),
                                        tag=getattr(self.args, "tag", ""), deadline=self.deadline)
                tokens = [record["id"] for record in rows]
            else:
                tokens = [record["id"] for record in catalog.records]
        elif not tokens and (getattr(self.args, "category", None) or getattr(self.args, "layer", None)
                             or getattr(self.args, "tag", None) or getattr(self.args, "risk", None)
                             or getattr(self.args, "difficulty", None) or getattr(self.args, "role", None)
                             or getattr(self.args, "engine", None)):
            tokens = [record["id"] for record in catalog.all_for(
                category_id=getattr(self.args, "category", ""), layer=getattr(self.args, "layer", ""),
                tag=getattr(self.args, "tag", ""), difficulty=getattr(self.args, "difficulty", ""),
                role=getattr(self.args, "role", ""), risk=getattr(self.args, "risk", ""),
                deadline=self.deadline)]
        if not tokens:
            raise kit.UsageError(f"'{self.command}' needs something to act on",
                                 hint=f"give capability references, or --category/--tag/--role filters, or "
                                      f"--all; `{RUN} help {self.command}` lists the accepted shapes",
                                 where="capability references")
        wanted = int(getattr(self.args, "limit", 0) or 0)
        if wanted:
            tokens = tokens[:wanted]
        ceiling = int(self.limits.get("max_targets") or 0)
        selected, skipped = catalog.select(tokens, require=require, deadline=self.deadline)
        if ceiling and len(selected) > ceiling:
            raise kit.LimitExceeded(f"{len(selected)} capabilities selected, above limits.max_targets="
                                    f"{ceiling}",
                                    hint="narrow it with --category/--tag/--limit, or raise --max-targets "
                                         "(the config key is limits.max_targets)", where="selection",
                                    selected=len(selected), ceiling=ceiling)
        if skipped:
            self.result.warn(f"{len(skipped)} of the given references matched nothing", code="W-SKIPPED",
                             skipped=[item["token"] for item in skipped])
        return selected

    # -- output -----------------------------------------------------------
    def write(self, path: str, text: str, kind: str = "artifact", approval: str = "", sensitive: dict | None = None):
        """Approval-gated atomic write; the run's temp files are already cleaned up on failure."""
        target = path if os.path.isabs(path) else os.path.join(self.out_dir, path)
        written = kit.atomic_write(target, text, allow_overwrite=bool(self.args.allow_overwrite),
                                   approval_token=self.args.approve, backup=bool(self.args.backup),
                                   deadline=self.deadline, retries=int(self.limits.get("retries") or 1),
                                   sensitive_names=sensitive or {})
        written["kind"] = kind
        if approval:
            written["approval"] = approval
        self._written.append(written)
        return written

    def artifact(self, pattern: str, body: str, data=None) -> str:
        """What --out receives: JSON for a .json target or --format json, otherwise the rendered body."""
        suffix = os.path.splitext(pattern or "")[1].lower()
        wants_json = suffix == ".json" or (not suffix and self.fmt == "json")
        if wants_json:
            return kit.dump_json(data if isinstance(data, (dict, list)) else {"kind": "artifact"}, indent=1)
        return body

    def packaged_dataset(self) -> str:
        return self.paths.get("dataset") or ""

    def emit(self, body: str = "", data=None, exit_code: int | None = None, gate_error=None) -> int:
        """The single place output leaves this tool, so the contract is identical everywhere.

        --format json prints the envelope. md/text/csv print the rendered body. When --out was used
        the artifact is already on disk and the envelope (with data.written) goes to stdout. A gate
        failure after a human body went to stdout puts the error envelope on stderr.
        """
        res = self.result
        if data is not None:
            res.set_data(data)
        if self._written:
            payload = res.data if isinstance(res.data, dict) else {}
            payload["written"] = self._written[0] if len(self._written) == 1 else self._written
            res.data = payload
        code = res.exit_code if exit_code is None else exit_code
        if gate_error is not None:
            res.error(gate_error)
            code = gate_error.exit_code
        if self.redact and body:
            body = kit.redact_str(body)
        if self.quiet:
            if code not in (0,):
                sys.stderr.write(kit.dump_json({"ok": False, "errors": [item for item in res.errors]},
                                              indent=1))
            return code
        if self.fmt == "json" or self._written:
            sys.stdout.write(res.to_json(indent=1, include_timestamp=self.stamp))
            if code not in (0,) and self.fmt == "json" and not self._written:
                pass  # the envelope on stdout already carries the errors
        else:
            sys.stdout.write(body if body.endswith("\n") or not body else body + "\n")
            if code not in (0,) and res.errors:
                sys.stderr.write(kit.dump_json({"ok": False, "errors": res.errors}, indent=1))
        return code


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def _ref(args) -> list:
    tokens = list(getattr(args, "refs", []) or [])
    if getattr(args, "input_refs", None):
        path = args.input_refs
        text = kit.read_text(path, args.max_input_mb or 64)
        tokens += [line for line in (part.strip() for part in re_split(text)) if line]
    if getattr(args, "from_file", None):
        text = kit.read_text(args.from_file, 8)
        tokens += [line for line in (part.strip() for part in re_split(text)) if line]
    return tokens


def re_split(text: str) -> list:
    """Capability references may arrive one per line, comma separated, or as JSON arrays."""
    stripped = (text or "").strip()
    if stripped.startswith("["):
        try:
            payload = json.loads(stripped)
            if isinstance(payload, list):
                out = []
                for item in payload:
                    if isinstance(item, str):
                        out.append(item)
                    elif isinstance(item, dict):
                        out.append(str(item.get("id") or item.get("capability_id") or ""))
                return out
        except ValueError:
            pass
    return [item for chunk in stripped.splitlines() for item in chunk.split(",")]


def _payload_for_records(ctx: Ctx, records, payload: dict) -> dict:
    payload["selection"] = {
        "count": len(records),
        "umbrella": ctx.catalog().umbrella_name(records),
        "categories": sorted({record["category"] for record in records}),
        "archetypes": {name: sum(1 for record in records if record["archetype"] == name)
                       for name in engine.ARCHETYPE_NAMES},
        "references": [record["id"] for record in records],
    }
    return payload


def _gate_error(report: dict, command: str, key: str = "findings") -> kit.GateFailure | None:
    blockers = [finding for finding in report.get(key, []) if finding.get("severity") == "block"]
    if not blockers:
        return None
    summary = report.get("summary", {})
    error = kit.GateFailure(
        f"{command} gate failed: {summary.get('blockers', len(blockers))} blocking finding(s); verdict "
        f"'{report.get('verdict')}'",
        hint=f"fix the records named in errors[0].details['{key}'], then re-run; --report-only gives the "
             f"same report with exit 0", where=PACKAGE + "/" + ENTRY_NAME)
    error.details[key] = blockers[:40]
    error.details["verdict"] = report.get("verdict")
    error.details["summary"] = summary
    return error


def _out_path(ctx: Ctx, pattern: str, record=None, index: int = 0) -> str:
    path = pattern
    if "{id}" in path and record is not None:
        path = path.replace("{id}", record["id"])
    elif "{id}" in path:
        path = path.replace("{id}", f"part{index + 1:02d}")
    if "{engine}" in path:
        path = path.replace("{engine}", ctx.engine)
    if "{sprint}" in path:
        path = path.replace("{sprint}", str(ctx.args.sprint or "sprint"))
    if "{slug}" in path and record is not None:
        path = path.replace("{slug}", record["slug"])
    return path


def _multi_write(ctx: Ctx, pattern: str, items: list, render) -> list:
    """One artifact per capability when the --out pattern contains {id}."""
    outputs = []
    for index, (record, payload) in enumerate(items):
        body = render(record, payload)
        outputs.append(ctx.write(_out_path(ctx, pattern, record, index), body,
                                kind=os.path.splitext(pattern)[1].lstrip(".") or "artifact"))
    return outputs


# ---------------------------------------------------------------------------
# commands: describe and list
# ---------------------------------------------------------------------------
def cmd_info(ctx: Ctx) -> int:
    catalog = ctx.catalog()
    source = (catalog.dataset.get("source_document") or {})
    data = {
        "skill_tool": {"name": kit.SKILL_TOOL_NAME, "version": TOOL_VERSION, "entry_point": ENTRY_NAME,
                       "run": RUN, "folder": PACKAGE + "/", "python": ">=3.9",
                       "modules": sorted(ctx.loaded), "commands": len(COMMAND_NAMES),
                       "assets": sorted(name for name in os.listdir(ctx.package_dir)
                                        if name.startswith("asset-"))},
        "unified_from": f"{source.get('title')}",
        "catalog": {"edition": source.get("edition"), "tagline": source.get("tagline"),
                    "fingerprint": catalog.meta.get("fingerprint"),
                    "categories": catalog.dataset.get("category_count"),
                    "capabilities": catalog.dataset.get("entry_count"),
                    "dataset": f"{PACKAGE}/{os.path.basename(ctx.paths['dataset'])}",
                    "imported": bool(source.get("imported")),
                    "layers": source.get("architecture_layers", []),
                    "engines": source.get("engines", []),
                    "core_acceptance_reference": source.get("core_acceptance_reference", []),
                    "catalog_design_notes": source.get("catalog_design_notes", [])},
        "scope": {"engines_enabled": ctx.cfg["engines"]["enabled"], "default_engine": ctx.engine,
                  "target": ctx.target, "offline": ctx.cfg.get("offline", {}),
                  "provider": ctx.cfg.get("provider", {})},
        "limits": ctx.limits,
        "paths": {"skill_root": os.path.basename(ctx.skill_root) + "/", "assets_dir": ctx.assets_dir,
                  "out_dir": ctx.out_dir, "config_layers": ctx.config_layers},
        "not_provided": ["no engine, compiler, device, or store command is executed",
                         "no network access of any kind", "no PDF reader: import takes text, markdown, "
                                                          "html, csv, tsv, or json",
                         "no proprietary SDK, engine binary, model weight, or credential is bundled"],
    }
    body = "\n".join([
        f"# {kit.SKILL_TOOL_NAME} - unified Skill Tool", "",
        f"Built from **{source.get('title')}** ({source.get('edition')}). {source.get('tagline')}.", "",
        f"- catalog: {catalog.dataset.get('category_count')} categories, "
        f"{catalog.dataset.get('entry_count')} capabilities, fingerprint "
        f"`{catalog.meta.get('fingerprint')}`",
        f"- run: `{RUN} <command>` (help: `{RUN} help`)",
        f"- entry point: `{PACKAGE}/{ENTRY_NAME}`, python >= 3.9, standard library only",
        f"- engines accepted here: {', '.join(ctx.cfg['engines']['enabled'])} (plus "
        f"`engine-neutral`); target `{ctx.target.get('platform')}` / "
        f"`{ctx.target.get('device_profile')}`",
        f"- limits: timeout {ctx.limits.get('timeout_seconds')}s, "
        f"{ctx.limits.get('max_targets')} capabilities per run, "
        f"{ctx.limits.get('max_input_mb')} MB input, retries {ctx.limits.get('retries')}",
        f"- config layers merged: {', '.join(ctx.config_layers)}", "",
        "## Layers of the catalog", "",
    ] + [f"- **{layer['layer']}** - {layer['role']}" for layer in source.get("architecture_layers", [])]
        + ["", "## What the source document requires of a finished capability", ""]
        + [f"{position}. {bullet}" for position, bullet in
           enumerate(source.get("core_acceptance_reference", []), start=1)]
        + ["", "## Design notes carried from the source", ""]
        + [f"- {note}" for note in source.get("catalog_design_notes", [])]
        + ["", "## Deliberately not provided", ""]
        + [f"- {item}" for item in data["not_provided"]])
    return ctx.emit(body + "\n", data)


def cmd_capabilities(ctx: Ctx) -> int:
    catalog = ctx.catalog()
    groups: dict = {}
    for name, kind, what in CAPABILITIES:
        groups.setdefault(kind, []).append({"command": name, "what": what,
                                            "implemented_in": f"{PACKAGE}/{_module_for(name)}"})
    data = {"tool": kit.SKILL_TOOL_NAME, "version": TOOL_VERSION, "command_count": len(COMMAND_NAMES),
            "groups": groups, "catalog_reach": {"categories": catalog.dataset.get("category_count"),
                                                "capabilities": catalog.dataset.get("entry_count")},
            "additional_modules": [
                {"file": "tools-runtime-kit.py", "why": "errors with exit codes, the output envelope, "
                                                        "cancellation, deadlines, config layering, "
                                                        "size-limited reads, approval-gated atomic writes, "
                                                        "redaction, and the deterministic renderers every "
                                                        "layer shares"},
                {"file": "tools-catalog-engine.py", "why": "dataset loading, validation, the six description "
                                                           "archetypes, derived record fields, token "
                                                           "selection, search, and the text/Markdown/HTML/"
                                                           "CSV/JSON ingest"},
                {"file": "tools-artifact-generators.py", "why": "spec, plan, checklist, and prompt-pack "
                                                                 "construction plus their renderers - the "
                                                                 "artifacts are built from the archetype "
                                                                 "section contracts, not from prose"},
                {"file": "tools-gates.py", "why": "the schema subset validator, rights R1-R10, the budget "
                                                   "gate, the evidence gate, the offline audit, coverage, "
                                                   "and doctor - the checks that decide whether work is done"},
                {"file": "tools-selftest.py", "why": "the unit suite that runs every command end to end, so "
                                                     "an edit to any layer is verified rather than assumed"}],
            "limitations": [
                "no engine, compiler, device, or store command is executed; commands are written into "
                "reports for a human or CI runner",
                "no network use: a provider endpoint is only accepted when it is a loopback address and "
                "network flags are off",
                "no PDF reader shipped: 'import' takes plain text, Markdown, HTML, CSV, TSV, or JSON",
                "rights and budget checks judge the records you supply; they cannot verify a license or a "
                "measurement that was never recorded"]}
    lines = [f"# Capabilities of the `{kit.SKILL_TOOL_NAME}` Skill Tool", "",
             f"One unified tool over {data['catalog_reach']['capabilities']} capabilities in "
             f"{data['catalog_reach']['categories']} categories, reachable through "
             f"{len(COMMAND_NAMES)} commands.", ""]
    order = ["describe", "list", "produce", "verify", "gate", "export", "ingest", "configure", "maintain"]
    for kind in order:
        if kind not in groups:
            continue
        lines += [f"## {GROUP_LABELS.get(kind, kind)}", ""]
        lines += [f"- `{item['command']}` - {item['what']}" for item in groups[kind]]
        lines.append("")
    lines += ["## Why there is more than one tools-* file", ""]
    lines += [f"- `{item['file']}` - {item['why']}" for item in data["additional_modules"]]
    lines += ["", "## Limits of this tool", ""]
    lines += [f"- {item}" for item in data["limitations"]]
    return ctx.emit("\n".join(lines) + "\n", data)


_MODULE_FOR = {"info": "tools-catalog-engine.py", "capabilities": "tools-offline-game-development.py",
               "categories": "tools-catalog-engine.py", "list": "tools-catalog-engine.py",
               "show": "tools-catalog-engine.py", "search": "tools-catalog-engine.py",
               "spec": "tools-artifact-generators.py", "plan": "tools-artifact-generators.py",
               "checklist": "tools-artifact-generators.py", "prompts": "tools-artifact-generators.py",
               "evidence": "tools-gates.py", "rights": "tools-gates.py", "budget": "tools-gates.py",
               "offline-audit": "tools-gates.py", "coverage": "tools-gates.py", "validate": "tools-gates.py",
               "export": "tools-artifact-generators.py", "import": "tools-catalog-engine.py",
               "config": "tools-runtime-kit.py", "doctor": "tools-gates.py", "cleanup": "tools-runtime-kit.py",
               "selftest": "tools-selftest.py", "help": "tools-offline-game-development.py"}


def _module_for(name: str) -> str:
    return _MODULE_FOR.get(name, "tools-offline-game-development.py")


def cmd_categories(ctx: Ctx) -> int:
    catalog = ctx.catalog()
    rows = []
    for number in sorted(catalog.categories):
        category = catalog.categories[number]
        rows.append({"id": f"c{number:02d}", "number": number, "title": category["title"],
                     "slug": category["slug"], "declared_count": category["declared_count"],
                     "count": category["count"], "layer": category["layer"],
                     "id_range": f"c{number:02d}e01-c{number:02d}e{category['count']:02d}"})
    data = {"count": len(rows), "categories": rows,
            "dataset": f"{PACKAGE}/{os.path.basename(ctx.paths['dataset'])}",
            "fingerprint": catalog.meta.get("fingerprint")}
    lines = ["# Categories (the umbrella names of the unified Skill Tool)", "",
             f"dataset `{os.path.basename(ctx.paths['dataset'])}` · fingerprint "
             f"`{catalog.meta.get('fingerprint')}` · {len(rows)} categories · "
             f"{len(catalog)} capabilities", ""]
    for row in rows:
        flag = "" if row["count"] == row["declared_count"] else f" (declared {row['declared_count']})"
        lines.append(f"- `{row['id']}` **{row['title']}** - {row['count']} capabilities{flag}; "
                     f"layer `{row['layer']}`; ids `{row['id_range']}`")
    body = "\n".join(lines) + "\n"
    if ctx.fmt == "csv":
        body = kit.csv_render(["number", "id", "title", "count", "declared_count", "layer", "slug"], rows)
    return ctx.emit(body, data)


LIST_COLUMNS = ["id", "ref", "name", "category", "archetype", "difficulty", "est_days", "role", "risk_class"]


def cmd_list(ctx: Ctx) -> int:
    catalog = ctx.catalog()
    rows = catalog.all_for(category_id=ctx.args.category or "", layer=ctx.args.layer or "",
                           tag=ctx.args.tag or "", difficulty=ctx.args.difficulty or "",
                           role=ctx.args.role or "", risk=ctx.args.risk or "",
                           engine=ctx.engine if ctx.args.engine_scoped else "", deadline=ctx.deadline)
    if ctx.args.search:
        scoped = catalog.search(ctx.args.search, limit=0, deadline=ctx.deadline)
        wanted = {hit["record"]["id"] for hit in scoped["hits"]}
        rows = [row for row in rows if row["id"] in wanted]
    total = len(rows)
    offset = int(ctx.args.offset or 0)
    cap = int(ctx.limits.get("max_list_rows") or 0)
    explicit_zero = ctx.args.limit == 0      # --limit 0 means 'I asked for everything'
    requested = int(ctx.args.limit or 0)
    shown = rows[offset:]
    note_truncated = False
    if requested:
        shown = shown[:requested]
        note_truncated = offset + requested < total
    capped = False
    if not requested and not explicit_zero and cap and len(shown) > cap:
        shown = shown[:cap]
        capped = True
    columns = ctx.args.fields or LIST_COLUMNS
    data = {"total": total, "shown": len(shown), "offset": offset, "limit": requested or None,
            "columns": columns, "filters": {"category": ctx.args.category, "layer": ctx.args.layer,
                                            "tag": ctx.args.tag, "difficulty": ctx.args.difficulty,
                                            "role": ctx.args.role, "risk": ctx.args.risk,
                                            "search": ctx.args.search, "engine": ctx.engine if
                                            ctx.args.engine_scoped else None},
            "records": [{key: record.get(key) for key in columns} for record in shown]}
    if note_truncated:
        ctx.result.warn(f"{total - offset - requested} further match(es); use --offset {offset + requested} "
                        f"or --limit 0 for all", code="W-TRUNCATED", total=total)
    if capped:
        ctx.result.warn(f"browse output capped at limits.max_list_rows={cap}; pass --limit for an explicit "
                        f"page or --out to export every row", code="W-CAPPED", total=total, cap=cap)
    if ctx.fmt == "csv":
        body = kit.csv_render(columns, data["records"])
    elif ctx.fmt == "text":
        body = kit.ascii_table(columns, data["records"], indent="") + "\n"
    else:
        lines = [f"# Capabilities ({total} matching)", ""]
        for record in shown:
            lines.append(f"- `{record['id']}` ({record['ref']}) **{record['name']}** - {record['archetype']}, "
                         f"{record['difficulty']}, {record['est_days']}d, owner `{record['role']}`")
        if len(shown) < total:
            lines += ["", f"_{len(shown)} of {total} shown; raise --limit or use --out for the full set._"]
        body = "\n".join(lines) + "\n"
    return ctx.emit(body, data)


def cmd_show(ctx: Ctx) -> int:
    records = ctx.records(_ref(ctx.args))
    catalog = ctx.catalog()
    payload = []
    for record in records:
        entry = dict(record)
        entry.pop("search_text", None)
        payload.append({"record": {key: entry.get(key) for key in engine.ENTRY_KEYS},
                        "category": {"number": record["category"], "title": record["category_title"],
                                     "slug": catalog.category(record)["slug"],
                                     "declared_count": catalog.category(record)["declared_count"]},
                        "archetype": {"name": record["archetype"],
                                      "intent": ctx.render_context().archetype(record["archetype"]).get("intent"),
                                      "plan_phase": ctx.render_context().archetype(record["archetype"]).get(
                                          "plan_phase"),
                                      "description_matches_template": record.get("template_ok", True),
                                      "regenerated_description": engine.describe(record["archetype"],
                                                                                 record["subject"])},
                        "evidence_kinds": record.get("evidence_kinds") or ctx.render_context().archetype(
                            record["archetype"]).get("evidence_kinds", []),
                        "gates": record.gates,
                        "source_ref": f"{PACKAGE}/asset-catalog.json#{record['id']}"})
    data = _payload_for_records(ctx, records, {"items": payload})
    body = generators.render_show_md(records, ctx.render_context())
    if ctx.fmt == "csv":
        body = generators.render_records_csv(records)
    elif ctx.fmt == "text":
        body = "\n".join(generators.record_lines(records, ctx.render_context()).splitlines()) + "\n"
    return ctx.emit(body, data)


def cmd_search(ctx: Ctx) -> int:
    catalog = ctx.catalog()
    result = catalog.search(ctx.args.query, category_id=ctx.args.category or "",
                            limit=int(ctx.args.limit or 0), offset=int(ctx.args.offset or 0),
                            deadline=ctx.deadline)
    data = {"query": result["query"], "terms": result["terms"], "total": result["total"],
            "offset": result["offset"], "limit": result["limit"], "truncated": result["truncated"],
            "hits": [{"id": hit["record"]["id"], "ref": hit["record"]["ref"],
                      "name": hit["record"]["name"], "category": hit["record"]["category"],
                      "category_title": hit["record"]["category_title"], "archetype": hit["record"]["archetype"],
                      "score": hit["score"], "matched_terms": hit["matched_terms"],
                      "description": hit["record"]["description"]} for hit in result["hits"]]}
    if ctx.fmt == "csv":
        body = kit.csv_render(["id", "name", "category", "score", "matched_terms", "description"],
                             [{key: hit[key] for key in ("id", "name", "category", "score", "matched_terms",
                                                         "description")} for hit in data["hits"]])
    else:
        body = generators.render_search_md(result, ctx.render_context())
    return ctx.emit(body, data)


# ---------------------------------------------------------------------------
# commands: produce
# ---------------------------------------------------------------------------
def _spec_payload(ctx: Ctx, records) -> list:
    render_ctx = ctx.render_context()
    return [(record, generators.build_spec(record, render_ctx)) for record in records]


def cmd_spec(ctx: Ctx) -> int:
    records = ctx.records(_ref(ctx.args))
    items = _spec_payload(ctx, records)
    schemas = ctx.schema()
    definition = gates.record_schema(schemas, "spec_record")
    issues = []
    for _record, spec in items:
        for issue in gates.validate_against_schema(spec, definition, schemas):
            issues.append({"capability_id": spec["capability_id"], **issue})
    if issues:
        raise kit.InternalError(f"{len(issues)} generated spec(s) do not satisfy the shipped output schema",
                                hint="this is a defect in the tool, not in your input; re-run after "
                                     "restoring the packaged files", where=PACKAGE + "/asset-output-schema.json",
                                issues=issues[:10])
    if len(items) == 1:
        body = generators.render_spec(items[0][1], ctx.render_context())
    else:
        chunks = ["# " + ctx.catalog().umbrella_name(records), "",
                  f"{len(items)} capability specs, engine `{ctx.engine}`, platform "
                  f"`{ctx.target.get('platform')}`, profile `{ctx.target.get('device_profile')}`", ""]
        for record, spec in items:
            chunks.append(generators.render_spec(spec, ctx.render_context()).rstrip())
            chunks.append("\n---\n")
        body = "\n".join(chunks)
    data = _payload_for_records(ctx, records, {"specs": [spec for _record, spec in items],
                                                "generated_for": items[0][1]["generated_for"]})
    pattern = ctx.args.out or ""
    if pattern and "{id}" in pattern and len(items) > 1:
        ctx._written = []
        _multi_write(ctx, pattern, items, lambda record, spec: generators.render_spec(spec, ctx.render_context()))
        data = _payload_for_records(ctx, records, {"specs_written": len(items),
                                                   "generated_for": items[0][1]["generated_for"]})
        return ctx.emit(f"wrote {len(items)} spec files from `{pattern}`\n", data)
    if ctx.fmt == "text":
        body = "\n".join(generators.spec_text(spec, ctx.render_context()) for _record, spec in items)
    if pattern:
        ctx.write(pattern, ctx.artifact(pattern, body, data), kind="spec")
    return ctx.emit(body, data)


def cmd_plan(ctx: Ctx) -> int:
    records = ctx.records(_ref(ctx.args))
    plan = generators.build_plan(records, ctx.render_context(), sprint=ctx.args.sprint or "",
                                owner_override=ctx.args.owner or "")
    schemas = ctx.schema()
    definition = gates.record_schema(schemas, "plan_item")
    issues = [issue for item in plan["items"] for issue in gates.validate_against_schema(item, definition, schemas)]
    if issues:
        raise kit.InternalError(f"{len(issues)} plan item(s) do not satisfy the shipped output schema",
                                hint="a defect in the tool; restore the packaged files and re-run",
                                where=PACKAGE + "/asset-output-schema.json", issues=issues[:10])
    body = generators.render_plan_md(plan)
    if ctx.fmt == "text":
        body = "\n".join(f"{item['seq']:>3}. [{item['phase']}] {item['capability_id']} {item['capability_name']}"
                         f" - {item['owner']} {item['effort']} {item['est_days']}d"
                         for item in plan["items"]) + "\n"
    elif ctx.fmt == "csv":
        columns = ["seq", "phase", "capability_id", "capability_name", "owner", "effort", "est_days",
                   "risk_class", "depends_on", "gates"]
        body = kit.csv_render(columns, [{key: item.get(key) for key in columns} for item in plan["items"]])
    data = _payload_for_records(ctx, records, plan)
    if ctx.args.out:
        ctx.write(ctx.args.out, ctx.artifact(ctx.args.out, body, data), kind="plan")
    return ctx.emit(body, data)


def cmd_checklist(ctx: Ctx) -> int:
    records = ctx.records(_ref(ctx.args))
    done: list = []
    passed: list = []
    if ctx.args.evidence:
        text = kit.read_text(ctx.args.evidence, ctx.max_input_mb, ctx.deadline)
        try:
            payload = json.loads(text)
        except ValueError as exc:
            raise kit.InputError(f"the evidence file for --evidence is not JSON: {exc}",
                                 hint="pass the file you would give to 'evidence check'",
                                 where=ctx.args.evidence) from exc
        if isinstance(payload, dict) and isinstance(payload.get("records"), list):
            items = payload["records"]
        elif isinstance(payload, dict):
            items = [payload]
        else:
            items = payload
        for record in items or []:
            if not isinstance(record, dict):
                continue
            if str(record.get("verdict", "")).lower() == "pass" and record.get("capability_id"):
                passed.append(str(record["capability_id"]))
            for check, state in ((check, None) for check in record.get("checks") or []):
                if isinstance(check, dict) and str(check.get("result", "")).lower() == "pass":
                    done.append(str(check.get("id")))
    checklist = generators.build_checklist(records, ctx.render_context(), done_ids=done,
                                           done_capabilities=passed)
    definition = gates.record_schema(ctx.schema(), "checklist_item")
    issues = [issue for item in checklist["items"]
              for issue in gates.validate_against_schema(item, definition, ctx.schema())]
    if issues:
        raise kit.InternalError(f"{len(issues)} checklist item(s) do not satisfy the shipped output schema",
                                hint="a defect in the tool; restore the packaged files and re-run",
                                where=PACKAGE + "/asset-output-schema.json", issues=issues[:10])
    body = generators.render_checklist_md(checklist)
    if ctx.fmt == "csv":
        body = generators.render_checklist_csv(checklist)
    elif ctx.fmt == "text":
        body = "\n".join(f"[{'x' if item['done'] else ' '}] {item['id']} {item['text']}"
                         for item in checklist["items"]) + "\n"
    data = _payload_for_records(ctx, records, checklist)
    if ctx.args.out:
        ctx.write(ctx.args.out, ctx.artifact(ctx.args.out, body, data), kind="checklist")
    return ctx.emit(body, data)


def cmd_prompts(ctx: Ctx) -> int:
    records = ctx.records(_ref(ctx.args))
    pack = generators.build_prompt_pack(records, ctx.render_context(), style=ctx.args.style)
    body = generators.render_prompt_pack_md(pack)
    data = _payload_for_records(ctx, records, pack)
    if ctx.args.out:
        ctx.write(ctx.args.out, ctx.artifact(ctx.args.out, body, data), kind="prompts")
    elif ctx.fmt == "json":
        data["prompt_chars"] = sum(len(item["prompt"]) for item in pack["packs"])
    return ctx.emit(body, data)


def cmd_evidence(ctx: Ctx) -> int:
    if ctx.args.action == "new":
        return _evidence_new(ctx)
    return _evidence_check(ctx)


def _evidence_new(ctx: Ctx) -> int:
    records = ctx.records(_ref(ctx.args))
    skeleton = generators.build_evidence_template(records, ctx.render_context())
    body = kit.dump_json({"kind": "evidence-set", "capability_count": len(skeleton),
                          "instructions": [
                              "replace every 'fill' with what you actually measured or observed",
                              "add a check whose gate field names each gate of the capability",
                              "a fail or blocked verdict needs failure_evidence, remediation, and "
                              "regression_retest",
                              f"then run: {RUN} evidence check --input <this file>"],
                          "records": skeleton}, indent=1)
    data = _payload_for_records(ctx, records, {"records": len(skeleton), "template": skeleton,
                                               "verdict_options": ["pass", "fail", "blocked", "not-run"]})
    pattern = ctx.args.out or ""
    if pattern and "{id}" in pattern and len(skeleton) > 1:
        ctx._written = []
        for index, record in enumerate(skeleton):
            match = next(item for item in records if item["id"] == record["capability_id"])
            ctx.write(_out_path(ctx, pattern, match, index), kit.dump_json(record), kind="evidence")
        data = _payload_for_records(ctx, records, {"records_written": len(skeleton)})
        return ctx.emit(f"wrote {len(skeleton)} evidence skeletons\n", data)
    if pattern:
        ctx.write(pattern, body, kind="evidence")
    return ctx.emit(body if ctx.fmt in ("md", "text") else "", data)


def _evidence_check(ctx: Ctx) -> int:
    paths = list(ctx.args.input or [])
    if not paths and ctx.args.use_fixture:
        paths = [ctx.paths["evidence_fixture"]]
    if not paths:
        raise kit.UsageError("'evidence check' needs an evidence file",
                             hint=f"write one with `{RUN} evidence new c34e23 --out evidence/c34e23.json`, "
                                  f"or pass --input, or use --use-asset-fixture for the packaged example",
                             where="--input")
    records: list = []
    for path in paths:
        found = gates.load_evidence(path, ctx.max_input_mb, ctx.deadline)
        for record in found:
            record["_source"] = os.path.basename(path)
        records += found
    report = gates.evidence_check(records, ctx.catalog(), ctx.schema(), ctx.render_context(),
                                 archetypes=ctx.archetypes_raw(), require_gates=ctx.args.require_gates)
    for record in report["verdicts"]:
        record.pop("_source", None)
    data = {"inputs": [os.path.relpath(path, os.getcwd()) for path in paths], "report": report}
    body = gates.render_evidence_md(report)
    error = None
    if report["verdict"] == "blocked" and not ctx.args.report_only:
        error = _gate_error(report, "evidence")
    if ctx.args.out:
        ctx.write(ctx.args.out, kit.dump_json({"kind": "evidence-verdicts", "findings": report["findings"],
                                              "verdicts": report["verdicts"]}), kind="evidence-verdicts")
    if ctx.fmt == "json":
        data["summary"] = report["summary"]
    return ctx.emit(body, data, gate_error=error)


# ---------------------------------------------------------------------------
# commands: gate
# ---------------------------------------------------------------------------
def cmd_rights(ctx: Ctx) -> int:
    path = ctx.args.input
    if not path:
        raise kit.UsageError("'rights' needs a manifest to check",
                             hint=f"pass --input rights/manifest.json; the required columns are listed in "
                                  f"{PACKAGE}/asset-license-policy.json, and "
                                  f"`{RUN} evidence new --help` shows the shapes accepted",
                             where="--input")
    records, findings = gates.load_manifest(path, ctx.max_input_mb, ctx.deadline)
    if not records:
        raise kit.InputError(f"the rights manifest at {path} holds no records",
                             hint="an array of records, an object with an assets array, or a CSV with the "
                                  "required columns", where=path)
    report = gates.rights_check(records, ctx.policy(), ctx.render_context(),
                               commercial=not ctx.args.non_commercial,
                               bundle=None if ctx.args.bundle is None else bool(ctx.args.bundle),
                               root=os.path.dirname(os.path.abspath(path)) or ".",
                               enforce_record_files=bool(ctx.args.check_record_files))
    report["manifest"] = os.path.relpath(path, os.getcwd())
    report["record_count"] = len(records)
    report["ingest_findings"] = findings
    waived = False
    if ctx.args.waive:
        policy_waiver = ctx.policy().get("waiver") or {}
        kit.confirm("waive a rights finding", ctx.args.approve, "WAIVE-RIGHTS", assume_yes=False)
        report["waiver"] = {"requested": True, "approved_by": None,
                           "policy_note": policy_waiver.get("note", "a human owner decides"),
                           "effect": "findings are still reported; the waiver is recorded here so the "
                                     "decision is visible"}
        waived = True
        ctx.result.warn("a rights waiver was approved for this run only: findings are reported unchanged "
                        "and must still be fixed or recorded in each asset's own file",
                        code="W-RIGHTS-WAIVED", records=len(records))
    error = None
    if report["verdict"] == "blocked" and not (ctx.args.report_only or waived):
        error = _gate_error(report, "rights")
    body = gates.render_rights_md(report)
    if ctx.fmt == "csv":
        body = gates.render_rights_csv(report)
    data = report
    if ctx.args.out:
        ctx.write(ctx.args.out, kit.dump_json({"kind": "rights-report", "findings": report["findings"],
                                              "verdicts": report["verdicts"], "summary": report["summary"]}),
                 kind="rights")
    return ctx.emit(body, data, gate_error=error)


def cmd_budget(ctx: Ctx) -> int:
    if ctx.args.action == "presets":
        return _budget_presets(ctx)
    path = ctx.args.input
    if not path:
        raise kit.UsageError("'budget check' needs measurements",
                             hint=f"pass --input measurements.json with {{"
                                  f"\"capability_id\": \"c26e05\", \"profile\": \"mobile-mid\", \"values\": "
                                  f"{{...}}}} records; see {PACKAGE}/asset-budget-presets.json", where="--input")
    measurements = gates.load_measurements(path, ctx.max_input_mb, ctx.deadline)
    report = gates.budget_check(measurements, ctx.budgets(), ctx.render_context(),
                               profile_override=ctx.args.profile or "", schema=ctx.schema())
    body = gates.render_budget_md(report)
    if ctx.fmt == "csv":
        body = gates.render_budget_csv(report)
    error = None
    if report["verdict"] == "blocked" and not ctx.args.report_only:
        error = _gate_error(report, "budget")
    data = {"input": os.path.relpath(path, os.getcwd()), "report": report}
    if ctx.args.out:
        ctx.write(ctx.args.out, kit.dump_json(report), kind="budget")
    return ctx.emit(body, data, gate_error=error)


def _budget_presets(ctx: Ctx) -> int:
    budgets = ctx.budgets()
    profiles = budgets.get("profiles") or {}
    metrics = budgets.get("metrics") or {}
    wanted = ctx.args.profile or ""
    if wanted and wanted not in profiles:
        raise kit.NotFoundError(f"no device profile named {wanted!r}",
                                hint=f"available: {', '.join(sorted(profiles))}", where="--profile")
    shown = {wanted: profiles[wanted]} if wanted else profiles
    rows = []
    for name, profile in sorted(shown.items()):
        ceilings = profile.get("metrics") or {}
        rows.append({"profile": name, "label": profile.get("label", name), "target": profile.get("target", ""),
                     "metrics": len(ceilings), "blocking_metrics": sum(1 for value in ceilings.values()
                                                                       if value.get("severity") == "block"),
                     "note": profile.get("note", "")})
    data = {"default_profile": budgets.get("default_profile"), "selection_hint": budgets.get("selection_hint"),
            "profiles": {name: shown[name] for name in sorted(shown)}, "profile_rows": rows,
            "metric_count": len(metrics), "metrics": metrics,
            "measurement_rules": budgets.get("measurement_rules", []),
            "fallback_guidance": budgets.get("fallback_guidance", {})}
    lines = [f"# Budget presets ({len(profiles)} device profiles, {len(metrics)} metrics)", "",
             f"default `{budgets.get('default_profile')}` - {budgets.get('selection_hint')}", "",
             kit.ascii_table(["profile", "label", "metrics", "blocking", "target"], rows), "",
             "## Metrics", ""]
    lines += [f"- `{name}` - {spec.get('description', '')} ({spec.get('unit', '')}, "
              f"direction {spec.get('direction', 'max')})" for name, spec in sorted(metrics.items())]
    lines += ["", "## Measurement rules", ""] + [f"- {rule}" for rule in budgets.get("measurement_rules", [])]
    if wanted:
        ceilings = (profiles[wanted].get("metrics") or {})
        lines += ["", f"## `{wanted}` ceilings", "",
                  kit.ascii_table(["metric", "limit", "unit", "severity"],
                                  [{"metric": name, "limit": value.get("max", value.get("min")),
                                    "unit": (metrics.get(name) or {}).get("unit", ""),
                                    "severity": value.get("severity", "warn")}
                                   for name, value in ceilings.items()]), ""]
    return ctx.emit("\n".join(lines) + "\n", data)


def cmd_offline_audit(ctx: Ctx) -> int:
    report = gates.offline_audit(ctx, scan_paths=[os.path.abspath(path) for path in (ctx.args.scan or [])],
                                allow_network=bool(ctx.cfg.get("offline", {}).get("allow_network")))
    body = gates.render_audit_md(report)
    data = report
    error = None
    if report["verdict"] == "blocked" and not ctx.args.report_only:
        blockers = [f for f in report["findings"] if f["severity"] == "block"]
        error = kit.GateFailure(f"offline audit found {len(blockers)} blocking issue(s) in the package",
                                hint="fix the files named in errors[0]['findings']; the audit reads text "
                                     "only, it changes nothing", findings=blockers[:40],
                                where=PACKAGE + "/")
    if ctx.args.out:
        ctx.write(ctx.args.out, kit.dump_json(report), kind="audit")
    return ctx.emit(body, data, gate_error=error)


def cmd_coverage(ctx: Ctx) -> int:
    catalog = ctx.catalog()
    expected = {}
    if ctx.args.expect_entries:
        expected["entries"] = int(ctx.args.expect_entries)
    if ctx.args.expect_fingerprint:
        expected["fingerprint"] = ctx.args.expect_fingerprint
    report = gates.coverage_report(catalog, ctx.archetypes_raw(), expected or None)
    body = gates.render_coverage_md(report)
    error = None
    problems = [finding for finding in report["findings"] if finding["severity"] == "error"]
    if problems and not ctx.args.report_only:
        error = kit.GateFailure(f"catalog coverage failed: {problems[0]['detail']}",
                                hint=f"re-import the source text with 'import', or pass "
                                     f"--expect-entries/--expect-fingerprint to compare against your own "
                                     f"copy of the document", findings=problems[:20],
                                where=f"{PACKAGE}/asset-catalog.json")
    if ctx.fmt == "csv":
        body = kit.csv_render(["number", "title", "declared_count", "count", "layer", "template_ok"],
                              [{key: item.get(key) for key in ("number", "title", "declared_count", "count",
                                                                "layer", "template_ok")}
                               for item in report["categories"]])
    if ctx.args.out:
        ctx.write(ctx.args.out, kit.dump_json(report), kind="coverage")
    return ctx.emit(body, {"dataset": f"{PACKAGE}/{os.path.basename(ctx.paths['dataset'])}", **report},
                    gate_error=error)


def cmd_validate(ctx: Ctx) -> int:
    catalog = ctx.catalog()
    findings = engine.validate_dataset(catalog.dataset, ctx.archetypes_raw())
    findings += ctx._assets_cache.get("findings", [])
    schema_findings: list = []
    if ctx.args.records:
        definition = gates.record_schema(ctx.schema(), ctx.args.kind)
        text = kit.read_text(ctx.args.records, ctx.max_input_mb, ctx.deadline)
        if text.lstrip().startswith(("{", "[")):
            try:
                payload = json.loads(text)
            except ValueError as exc:
                raise kit.InputError(f"the records file is not valid JSON: {exc}", where=ctx.args.records) from exc
            if isinstance(payload, dict) and isinstance(payload.get("data"), dict):
                payload = payload["data"]      # accept the tool's own envelope
            if isinstance(payload, list):
                items = payload
            else:
                items = payload.get("records") or payload.get("items") or payload.get("specs") \
                    or payload.get("packs") or payload.get("rows") or []
            if isinstance(payload, dict) and not items and payload.get("kind"):
                items = [payload]
        else:
            raise kit.InputError("the records file must be JSON (a record, an array, or an envelope)",
                                 hint=f"write one with `{RUN} spec c13e05 --format json --out spec.json`",
                                 where=ctx.args.records)
        for position, item in enumerate(items, start=1):
            for issue in gates.validate_against_schema(item, definition, ctx.schema()):
                schema_findings.append({"severity": "error", "code": "E-RECORD-SCHEMA",
                                        "detail": f"{issue['at']}: {issue['message']}",
                                        "location": f"{os.path.basename(ctx.args.records)}[{position}]"})
    else:  # every capability record must satisfy the schema's capability_ref definition
        definition = gates.record_schema(ctx.schema(), "capability_ref")
        schemas = ctx.schema()
        for record in catalog.records:
            ctx.deadline.tick("validate records")
            cleaned = {key: value for key, value in record.items()
                       if key in ("id", "ref", "name", "slug", "category", "category_title", "layer",
                                  "archetype", "subject", "description", "tags", "engines")}
            for issue in gates.validate_against_schema(cleaned, definition, schemas):
                schema_findings.append({"severity": "error", "code": "E-RECORD-SCHEMA",
                                        "detail": f"{record['id']} {issue['at']}: {issue['message']}",
                                        "location": record["id"]})
                break
    all_findings, seen_findings = [], set()
    for finding in findings + schema_findings:
        key = (finding["severity"], finding["code"], finding["detail"], finding.get("location", ""))
        if key in seen_findings:
            continue
        seen_findings.add(key)
        all_findings.append(finding)
    errors = [f for f in all_findings if f["severity"] == "error"]
    warnings = [f for f in all_findings if f["severity"] != "error"]
    data = {"ok": not errors, "dataset": f"{PACKAGE}/{os.path.basename(ctx.paths['dataset'])}",
            "fingerprint": catalog.meta.get("fingerprint"), "categories": catalog.dataset.get("category_count"),
            "entries": catalog.dataset.get("entry_count"), "checked_against": ctx.args.records or
            f"{PACKAGE}/{os.path.basename(ctx.paths['output_schema'])}",
            "kind": ctx.args.kind if ctx.args.records else "capability_ref",
            "errors": [f["detail"] for f in errors][:40], "warnings": [f["detail"] for f in warnings][:40],
            "findings": all_findings[:80], "counts": {"errors": len(errors), "warnings": len(warnings)}}
    lines = [f"# Validate - {'ok' if not errors else 'failed'}", "",
             f"dataset `{os.path.basename(ctx.paths['dataset'])}` · "
             f"{catalog.dataset.get('category_count')} categories · {catalog.dataset.get('entry_count')} "
             f"capabilities · fingerprint `{catalog.meta.get('fingerprint')}`",
             f"checked against `{data['checked_against']}` as `{data['kind']}`", "",
             f"- errors: {len(errors)}", f"- warnings: {len(warnings)}"]
    for finding in (errors + warnings)[:25]:
        lines.append(f"- [{finding['severity']}] {finding['code']} ({finding.get('location')}): "
                     f"{finding['detail']}")
    body = "\n".join(lines) + "\n"
    error = None
    if errors and not ctx.args.report_only:
        error = kit.GateFailure(f"validation found {len(errors)} error(s)",
                               hint="the findings list names each location; fix the dataset or the record",
                               findings=errors[:40], where=PACKAGE + "/asset-catalog.json")
    if ctx.args.out:
        ctx.write(ctx.args.out, kit.dump_json(data), kind="validation")
    return ctx.emit(body, data, gate_error=error)


# ---------------------------------------------------------------------------
# commands: export, import, config, doctor, cleanup, selftest
# ---------------------------------------------------------------------------
def cmd_export(ctx: Ctx) -> int:
    catalog = ctx.catalog()
    if ctx.args.refs:
        records = ctx.records(_ref(ctx.args))
    else:
        records = catalog.all_for(category_id=ctx.args.category or "", layer=ctx.args.layer or "",
                                  tag=ctx.args.tag or "", difficulty=ctx.args.difficulty or "",
                                  role=ctx.args.role or "", risk=ctx.args.risk or "", deadline=ctx.deadline)
        if ctx.args.limit:
            records = records[int(ctx.args.offset or 0):int(ctx.args.offset or 0) + int(ctx.args.limit)]
    columns = ctx.args.columns or generators.EXPORT_COLUMNS
    unknown = [name for name in columns if name not in engine.ENTRY_KEYS and name not in ("template_ok",)]
    if unknown:
        raise kit.UsageError(f"unknown export column(s): {', '.join(unknown)}",
                             hint=f"available: {', '.join(engine.ENTRY_KEYS)}", where="--columns")
    rows = [{key: (" ".join(str(value) if isinstance(value, list) else value) if isinstance(value, list)
                   else value) for key, value in [(column, record.get(column)) for column in columns]}
            for record in records]
    if ctx.fmt == "json":
        data = _payload_for_records(ctx, records, {"columns": columns, "count": len(rows), "records": rows})
        body = ""
    else:
        data = _payload_for_records(ctx, records, {"columns": columns, "count": len(rows)})
        body = kit.csv_render(columns, rows)
    if ctx.args.out:
        ctx.write(ctx.args.out, ctx.artifact(ctx.args.out, body, data), kind="export")
    return ctx.emit(body or f"exported {len(rows)} rows\n", data)


def cmd_import(ctx: Ctx) -> int:
    source = ctx.args.source
    if not source:
        raise kit.UsageError("'import' needs a source file",
                             hint=f"pass --source catalog.txt (or .md/.html/.csv/.json); a .pdf path is "
                                  f"accepted only when its extracted text sits beside it - see the "
                                  f"readme section 'Reading a catalog in'", where="--source")
    path = os.path.abspath(os.path.expanduser(source))
    label = source
    if not os.path.exists(path):
        raise kit.InputError(f"input file not found: {path}",
                             hint="pass a path you can read, relative to the directory you run from",
                             where=source)
    suffix = os.path.splitext(path)[1].lower()
    if suffix == ".pdf":
        companion = os.path.splitext(path)[0] + ".txt"
        if not os.path.isfile(companion):
            raise kit.InputError(
                f"the Skill Tool does not parse PDFs, and no text companion was found for "
                f"{os.path.basename(path)}",
                hint=f"extract the text (for example `pdftotext -layout {os.path.basename(path)} "
                     f"{os.path.basename(companion)}`) and save it beside the PDF with the same name, "
                     f"then re-run with --source {label}; the tool reads .txt, .md, .html, .htm, .csv, "
                     f".tsv, .json", where=source, companion=companion)
        label = source + " (via " + os.path.basename(companion) + ")"
        path = companion
        suffix = ".txt"
    elif suffix not in (".txt", ".md", ".markdown", ".html", ".htm", ".csv", ".tsv", ".json"):
        raise kit.InputError(f"unsupported source type {suffix or '(none)'} for {os.path.basename(path)}",
                             hint="accepted: .txt .md .html .htm .csv .tsv .json (a .pdf path resolves the "
                                  "same-basename .txt beside it)", where=source)
    text = kit.read_text(path, ctx.max_input_mb, ctx.deadline)
    findings: list = []
    fmt = ctx.args.input_format
    if fmt == "auto" and suffix in (".csv",):
        fmt = "csv"
    elif fmt == "auto" and suffix == ".tsv":
        fmt = "tsv"
    elif fmt == "auto" and suffix == ".json":
        fmt = "json"
    parsed = engine.parse_source(text, ctx.archetypes_raw().get("archetypes") or {}, origin=path,
                                fmt=fmt if fmt != "auto" else "auto", findings=findings)
    dataset, normalize_findings = engine.normalize_dataset(parsed, ctx.archetypes_raw(),
                                                           source=os.path.basename(path))
    errors = [f for f in normalize_findings if f["severity"] == "error"]
    if errors:
        raise kit.InputError(f"the imported text parsed, but the dataset is not valid: {errors[0]['detail']}",
                             hint=f"check the count findings; `{RUN} import --source {source} --summary-only` "
                                  f"shows the parse statistics", where=source, findings=errors[:20])
    shipped = ctx.paths.get("dataset") or ""
    sensitive = None
    if shipped:
        sensitive = {}
        for candidate in (shipped, os.path.join(ctx.package_dir, os.path.basename(shipped)),
                          os.path.join(ctx.out_dir, os.path.basename(shipped))):
            sensitive[os.path.normpath(os.path.abspath(candidate))] = "REPLACE-DATASET"
    out = ctx.args.out
    if not out and not ctx.args.summary_only:
        out = os.path.join(ctx.out_dir, "imported-catalog.json")
    body = kit.dump_json({key: value for key, value in dataset.items() if key != "records"})
    data = {"source": label, "source_format": parsed["source_document"]["input_formats"],
            "stats": parsed["stats"],
            "categories": [{"number": category["number"], "title": category["title"],
                            "declared_count": category["declared_count"], "count": len(category["entries"])}
                           for category in dataset["categories"]],
            "counts": {"categories": dataset["category_count"], "entries": dataset["entry_count"]},
            "fingerprint": dataset.get("fingerprint"),
            "findings": findings + normalize_findings,
            "vs_shipped": {"shipped_fingerprint": ctx.catalog().meta.get("fingerprint"),
                           "same_content": dataset.get("fingerprint") == ctx.catalog().meta.get("fingerprint")}}
    warnings = [f for f in findings + normalize_findings if f["severity"] != "error"]
    for finding in warnings:
        ctx.result.warn(finding["detail"], code=finding["code"], where=finding.get("location", ""))
    if ctx.args.summary_only:
        data["categories"] = [{"number": row["number"], "title": row["title"], "count": row["count"],
                              "declared_count": row["declared_count"]} for row in data["categories"]]
        data["umbrella"] = (dataset["categories"][0]["title"] if len(dataset["categories"]) == 1
                            else f"{len(dataset['categories'])} categories")
        data["out"] = None
    else:
        ctx.write(out, body, kind="dataset", sensitive=sensitive)  # the dataset is always JSON
    lines = [f"# Import - {label}", "",
             f"parsed {dataset['entry_count']} capabilities in {dataset['category_count']} categories from "
             f"`{'` / `'.join(parsed['source_document']['input_formats'])}` input",
             f"- inferred archetypes: {parsed['stats']['inferred_archetypes']}",
             f"- unclassified lines: {parsed['stats']['unclassified_lines']}",
             f"- fingerprint: `{dataset.get('fingerprint')}`", ""]
    lines += [f"- **{category['title']}** - {len(category['entries'])} of {category['declared_count'] or len(category['entries'])}"
              for category in dataset["categories"]]
    if warnings:
        lines += ["", "## Findings", ""]
        lines += [f"- [{item['severity']}] {item['code']}: {item['detail']}" for item in warnings[:20]]
    if not ctx.args.summary_only:
        lines += ["", f"dataset written to `{data.get('written', {}).get('path', out) if isinstance(data.get('written'), dict) else out}`"
                  f" - use it with --dataset", ""]
    return ctx.emit("\n".join(lines) + "\n", data)


def cmd_config(ctx: Ctx) -> int:
    if ctx.args.action == "init":
        target = ctx.args.out or os.path.join(os.getcwd(), "ogd-config.json")
        payload = json.loads(json.dumps(kit.BUILTIN_CONFIG))
        payload["_comment"] = ("Edit this copy and pass it with --config. The tool reads it after "
                              + f"{PACKAGE}/asset-config-default.json and before OGD_CONFIG and flags. "
                              "Keys marked 'auto' are resolved at run time. Every key here is read by the "
                              "code; 'doctor' fails if a documented key stops being honoured.")
        payload["_asset_names_are_relative_to_skill_root"] = (
            f"Reference packaged files as {PACKAGE}/<filename> from the future skill root.")
        written = ctx.write(target, kit.dump_json(payload, indent=1), kind="config")
        return ctx.emit(f"wrote an editable config to {written['path']}\n",
                        {"path": written["path"], "bytes": written["bytes"],
                         "created": written["created"], "keys": len(payload) - 2})
    data = {"config": ctx.cfg, "layers": ctx.config_layers, "resolved_paths": {
        key: (os.path.relpath(value, ctx.skill_root) if value else "") for key, value in ctx.paths.items()},
        "sources": {"assets_dir": ctx.assets_dir, "out_dir": ctx.out_dir,
                    "dataset": ctx.paths["dataset"], "environment_OGD_CONFIG": os.environ.get("OGD_CONFIG", "")},
        "defaults_asset": f"{PACKAGE}/asset-config-default.json",
        "editable_keys": sorted(_flatten(ctx.cfg))}
    lines = ["# Merged configuration", "",
             f"layers, last one wins: {', '.join(ctx.config_layers)}", "",
             kit.ascii_table(["key", "value"], [{"key": key, "value": value} for key, value in
                                               sorted(_flatten(ctx.cfg).items())]), "",
             "resolved asset paths:", ""]
    lines += [f"- `{key}` -> `{PACKAGE}/{os.path.basename(value) if value else 'unset'}`"
              for key, value in ctx.paths.items()]
    body = "\n".join(lines) + "\n"
    if ctx.args.out:
        ctx.write(ctx.args.out, kit.dump_json(data), kind="config")
    return ctx.emit(body, data)


def _flatten(mapping: dict, prefix: str = "") -> dict:
    out: dict = {}
    for key, value in mapping.items():
        name = f"{prefix}{key}"
        if isinstance(value, dict):
            out.update(_flatten(value, name + "."))
        else:
            out[name] = value if not isinstance(value, list) else ", ".join(map(str, value))
    return out


def cmd_doctor(ctx: Ctx) -> int:
    checks = gates.doctor_checks(ctx)
    failed = [check for check in checks if check["status"] != "pass"]
    report = {"package": f"{PACKAGE}/", "entry_point": ENTRY_NAME, "checks": checks,
              "passed": len(checks) - len(failed), "total": len(checks), "version": TOOL_VERSION,
              "python": f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
              "config_layers": ctx.config_layers}
    body = gates.render_doctor_md(checks)
    exit_code = 0
    error = None
    if failed:
        error = kit.EnvironmentFailure(f"doctor found {len(failed)} failing check(s): "
                                       + ", ".join(check["id"] for check in failed),
                                       hint="each failing check names its remediation in the report; fix it "
                                            "and re-run 'doctor'", checks=failed, where=f"{PACKAGE}/")
        exit_code = kit.EXIT_INPUT
    if ctx.args.out:
        ctx.write(ctx.args.out, kit.dump_json(report), kind="doctor")
    return ctx.emit(body, report, exit_code=exit_code if not error else None, gate_error=error)


def cmd_cleanup(ctx: Ctx) -> int:
    directory = ctx.args.directory or os.getcwd()
    if not os.path.isdir(directory):
        raise kit.InputError(f"--directory is not a directory: {directory}",
                             hint="pass the folder you ran writes into, for example '.' or 'reports'",
                             where="--directory")
    minutes = ctx.args.older_than if ctx.args.older_than is not None else 1.0
    found = kit.stale_temp_files(directory, minutes, int(ctx.limits.get("walk_depth") or 6), ctx.deadline)
    backups = []
    if ctx.args.include_backups:
        for base, dirs, files in os.walk(os.path.abspath(directory)):
            dirs[:] = [name for name in dirs if name != "__pycache__"]
            for name in files:
                if name.endswith(".bak") or ".bak" in name:
                    path = os.path.join(base, name)
                    backups.append({"path": path, "bytes": os.path.getsize(path),
                                    "age_minutes": 0})
    candidates = found + backups
    deleted: list = []
    if ctx.args.delete:
        kit.confirm("delete " + (f"{len(candidates)} file(s)" if candidates else "0 files"),
                    ctx.args.approve, "DELETE-FILES", assume_yes=ctx.args.yes)
        for item in candidates:
            try:
                os.unlink(item["path"])
                deleted.append(item["path"])
            except OSError as exc:
                ctx.result.warn(f"could not delete {item['path']}: {exc.strerror or exc}", code="W-CLEANUP")
    data = {"directory": os.path.abspath(directory), "older_than_minutes": minutes,
            "temp_files": found, "backups": backups, "count": len(candidates),
            "deleted": deleted, "dry_run": not ctx.args.delete,
            "note": "only .tmp-* files this tool's interrupted writes could have left, plus .bak backups "
                    "when --include-backups is given"}
    lines = [f"# Cleanup - {'deleted' if deleted else 'no deletions'}", "",
             f"directory `{os.path.abspath(directory)}` · older than {minutes:g} min · "
             f"{len(found)} temp file(s) · {len(backups)} backup(s)", ""]
    if candidates:
        lines.append(kit.ascii_table(["path", "bytes", "age_minutes"], candidates))
        if not ctx.args.delete:
            lines += ["", f"nothing was deleted. to delete these, re-run: `{RUN} cleanup --directory "
                          f"{os.path.abspath(directory)} --delete --approve DELETE-FILES`"]
    else:
        lines.append("No stale temp files or backups: every write completed and renamed.")
    return ctx.emit("\n".join(lines) + "\n", data)


def cmd_selftest(ctx: Ctx) -> int:
    path = os.path.join(ctx.package_dir, "tools-selftest.py")
    if not os.path.isfile(path):
        raise kit.NotFoundError(f"missing {PACKAGE}/tools-selftest.py",
                                hint="the suite ships with the Skill Tool; re-extract the package", where=path)
    import unittest

    if os.environ.get("OGD_SELFTEST_RUN"):
        raise kit.UsageError("a selftest run is already in progress; the suite must not nest inside itself",
                             hint="run it directly for tracebacks: python3 "
                                  f"{PACKAGE}/tools-selftest.py -v",
                             where=PACKAGE + "/tools-selftest.py")
    module = _load("tools-selftest.py", "ogd_tools-selftest")
    patterns = [str(item) for item in (getattr(ctx.args, "only", None) or []) if str(item).strip()]
    for pattern in patterns:
        root = pattern.split(".")[0]
        if not hasattr(module, root):
            raise kit.UsageError(f"no test case named {root}",
                                 hint="names look like TestSelection or TestSelection.test_browse_paging",
                                 where="--only")
    verbosity = 2 if ctx.args.verbose else 1
    buffer = io.StringIO()
    if patterns:
        suite = unittest.defaultTestLoader.loadTestsFromNames(patterns, module)
    else:
        suite = unittest.defaultTestLoader.loadTestsFromModule(module)
    runner = unittest.TextTestRunner(stream=buffer, verbosity=verbosity, buffer=False)
    ctx.deadline.tick("before the suite")
    saved_full = os.environ.get("OGD_SELFTEST_FULL")
    os.environ["OGD_SELFTEST_RUN"] = "1"
    if patterns:
        os.environ["OGD_SELFTEST_ONLY"] = ",".join(patterns)
        os.environ.pop("OGD_SELFTEST_FULL", None)  # a filtered run cannot claim full coverage
    else:
        os.environ["OGD_SELFTEST_FULL"] = "1"
    started = time.monotonic()
    try:
        outcome = runner.run(suite)
        elapsed = time.monotonic() - started
    finally:
        for guard in ("OGD_SELFTEST_RUN", "OGD_SELFTEST_ONLY", "OGD_SELFTEST_FULL"):
            os.environ.pop(guard, None)
        if saved_full:
            os.environ["OGD_SELFTEST_FULL"] = saved_full
    summary = {"testsRun": outcome.testsRun, "failures": len(outcome.failures),
               "errors": len(outcome.errors), "skipped": len(outcome.skipped),
               "ok": outcome.wasSuccessful(), "seconds": round(max(0.0, elapsed), 2),
               "filtered": bool(patterns), "output": buffer.getvalue()}
    body = buffer.getvalue()
    code = 0 if outcome.wasSuccessful() else kit.EXIT_GATE
    if not outcome.wasSuccessful():
        ctx.result.error(kit.GateFailure(f"selftest failed: {summary['failures']} failure(s), "
                                         f"{summary['errors']} error(s) of {summary['testsRun']} tests",
                                         hint="run the suite directly for the tracebacks: python3 "
                                              f"{PACKAGE}/tools-selftest.py -v",
                                         where=PACKAGE + "/tools-selftest.py"))
    if ctx.args.out:
        ctx.write(ctx.args.out, body, kind="selftest")
    if ctx.fmt == "json":
        return ctx.emit("", {"selftest": summary}, exit_code=code)
    return ctx.emit(body, {"selftest": summary}, exit_code=code)


def cmd_help(ctx: Ctx) -> int:
    topic = ctx.args.topic
    parser = ctx.args.parser
    if not topic:
        lines = [f"{kit.SKILL_TOOL_NAME} {TOOL_VERSION} - unified offline game development Skill Tool", "",
                 f"usage: {RUN} <command> [args]", "", "commands:"]
        for name, kind, what in CAPABILITIES:
            lines.append(f"  {name:<14} {GROUP_LABELS.get(kind, kind):<30} {what.split(';')[0]}")
        lines += ["", f"run '{RUN} help <command>' for one command's options,",
                  f"     '{RUN} capabilities' for what this tool can do and why each file exists,",
                  f"     '{RUN} info' for the catalog it is built on."]
        return ctx.emit("\n".join(lines) + "\n", {"usage": lines[:3], "commands": COMMAND_NAMES})
    if topic not in set(COMMAND_NAMES):
        raise kit.NotFoundError(f"no command named {topic!r}",
                                hint=f"commands: {', '.join(COMMAND_NAMES)}", where="help <command>")
    subparser = ctx.args.parser._subparsers._group_actions[0].choices[topic]
    text = subparser.format_help()
    return ctx.emit(text, {"command": topic, "help": text})


# ---------------------------------------------------------------------------
# parser
# ---------------------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    global_parser = argparse.ArgumentParser(add_help=False, prog=f"{RUN}")
    reference = global_parser.add_argument_group("capability selection")
    reference.add_argument("--all", action="store_true", help="take every capability in the dataset")
    reference.add_argument("--category", help="limit to one category: number, 'c13', or part of its title")
    reference.add_argument("--layer", help="limit to an architecture layer")
    reference.add_argument("--tag", help="limit to a derived topic tag")
    reference.add_argument("--difficulty", help="Foundational, Intermediate, Advanced, or Expert")
    reference.add_argument("--role", help="limit to one derived owner")
    reference.add_argument("--risk", help="routine, resource-heavy, or approval-required")
    reference.add_argument("--engine-scoped", dest="engine_scoped", action="store_true",
                           help="restrict a browse to the capabilities whose engine scope includes --engine")
    reference.add_argument("--from-file", dest="from_file", metavar="PATH",
                           help="read additional references from a text or JSON file, one per line")

    io_group = global_parser.add_argument_group("input, output, and limits")
    io_group.add_argument("--dataset", help="use another catalog dataset (path or asset name)")
    io_group.add_argument("--assets-dir", dest="assets_dir",
                          help="folder holding the asset-* files; 'auto' means beside this script")
    io_group.add_argument("--config", help="JSON config file merged over the packaged defaults")
    io_group.add_argument("--format", choices=("json", "md", "csv", "text"), default=None,
                          help="output shape (default json)")
    io_group.add_argument("--out", help="write the artifact to this path ('{id}', '{slug}', '{engine}', "
                                       "'{sprint}' allowed)")
    io_group.add_argument("--out-dir", dest="out_dir_flag", help="default directory for --out paths")
    io_group.add_argument("--limit", type=int, help="row or capability cap for this command")
    io_group.add_argument("--offset", type=int, default=0, help="skip this many rows")
    io_group.add_argument("--max-targets", dest="max_targets", type=int,
                          help="cap on capabilities selected per run (limits.max_targets)")
    io_group.add_argument("--max-input-mb", dest="max_input_mb", type=float,
                          help="largest input file the tool will read")
    io_group.add_argument("--timeout", type=float, help="wall-clock seconds before the run stops")
    io_group.add_argument("--retries", type=int, help="write attempts before giving up")
    io_group.add_argument("--allow-overwrite", dest="allow_overwrite", action="store_true",
                          help="permit replacing an existing file, with --approve")
    io_group.add_argument("--approve", default="", metavar="TOKEN",
                          help="the approval token a destructive action requires")
    io_group.add_argument("--backup", action="store_true", help="keep a .bak copy before overwriting")
    io_group.add_argument("--yes", action="store_true", help="answer the interactive prompt when a TTY exists")
    io_group.add_argument("--quiet", action="store_true", help="no stdout; only the exit code")
    io_group.add_argument("--stamp", action="store_true",
                          help="include generated_at and duration_ms (off keeps output byte-identical)")
    io_group.add_argument("--report-only", dest="report_only", action="store_true",
                          help="write and print the report even when a gate blocks, exiting 0")
    io_group.add_argument("--verbose", action="store_true", help="more detail: tracebacks, per-test output")
    io_group.add_argument("--fields", action="append", help="comma separated columns for list/show")
    io_group.add_argument("--engine", choices=engine.ENGINE_IDS, help="engine pack to write for")
    io_group.add_argument("--platform", help="declared platform target")
    io_group.add_argument("--device-profile", dest="device_profile", help="budget device profile")
    io_group.add_argument("--region", help="target region for store and localization rows")
    io_group.add_argument("--provider-kind", dest="provider_kind", choices=("local", "none", "remote"),
                          help="prompt-pack provider kind (remote is refused)")
    io_group.add_argument("--provider-model", dest="provider_model", help="local model id for prompt packs")
    io_group.add_argument("--provider-endpoint", dest="provider_endpoint",
                          help="loopback endpoint for a local model server")

    parser = argparse.ArgumentParser(prog=RUN, parents=[global_parser],
                                     description="Unified offline game development Skill Tool: capability "
                                                 "specs, sprint plans, checklists, evidence, rights, "
                                                 "budgets, and gates over the whole catalog.",
                                     epilog=f"start with '{RUN} capabilities', then '{RUN} help <command>'. "
                                          f"All paths in reports are relative to the skill root "
                                          f"({PACKAGE}/ is the tool, the folder above is the root).")
    sub = parser.add_subparsers(dest="command", metavar="<command>")

    def add(name: str, help_text: str) -> argparse.ArgumentParser:
        return sub.add_parser(name, parents=[global_parser], help=help_text, description=help_text)

    add("info", "describe this Skill Tool and the catalog behind it")
    add("capabilities", "list what the unified tool can do and which file implements it")
    add("categories", "list the umbrella categories with counts and layer")
    browse = add("list", "browse or filter capability records")
    browse.add_argument("--search", help="restrict to rows matching these words")
    show = add("show", "the full record for one or more capabilities")
    search = add("search", "ranked search over names, descriptions, and tags")
    search.add_argument("query", help="words to look for, quoted")
    spec = add("spec", "write the capability spec from its archetype section contract")
    plan = add("plan", "order a selection into a sprint plan")
    plan.add_argument("--sprint", help="sprint label to write into the plan")
    plan.add_argument("--owner", help="override the derived owner for every item")
    checklist = add("checklist", "tick-box work list for a selection")
    checklist.add_argument("--evidence", metavar="PATH",
                           help="an evidence file whose passing checks tick the boxes")
    prompts = add("prompts", "prompt packs for a local model, with context and prohibitions")
    prompts.add_argument("--style", choices=("engineer", "producer", "qa", "concise"), default="engineer")
    evidence = add("evidence", "new writes an evidence skeleton; check validates evidence records")
    evidence.add_argument("action", choices=("new", "check"), help="what to do")
    evidence.add_argument("--input", action="append", metavar="PATH",
                          help="evidence file to check (repeatable)")
    evidence.add_argument("--use-asset-fixture", dest="use_fixture", action="store_true",
                          help="check the packaged example instead of --input")
    evidence.add_argument("--require-gates", dest="require_gates", action="store_true",
                          help="escalate a gate with no recorded result from warning to blocker")
    evidence.add_argument("--summary-only", dest="summary_only", action="store_true", help=argparse.SUPPRESS)
    rights = add("rights", "run the R1-R10 rights gate over an asset manifest")
    rights.add_argument("--input", metavar="PATH", help="the rights manifest (JSON array, object, or CSV)")
    rights.add_argument("--non-commercial", dest="non_commercial", action="store_true",
                        help="the project is non-commercial, so R3 relaxes")
    rights.add_argument("--bundle", action="store_true", default=None, help="assume assets ship in a bundle")
    rights.add_argument("--no-bundle", dest="bundle", action="store_false",
                        help="assume assets are not redistributed")
    rights.add_argument("--check-record-files", dest="check_record_files", action="store_true",
                        help="escalate a missing record_path from warning to blocker")
    rights.add_argument("--waive", action="store_true",
                        help="record an approved human waiver (needs --approve WAIVE-RIGHTS)")
    budget = add("budget", "check measurements against the device profile, or list the presets")
    budget.add_argument("action", choices=("check", "presets"), help="what to do")
    budget.add_argument("--input", metavar="PATH", help="measurements file for 'budget check'")
    budget.add_argument("--profile", help="device profile to judge against (default from config)")
    audit = add("offline-audit", "audit this Skill Tool's own folder and any extra files")
    audit.add_argument("--scan", action="append", metavar="PATH", help="extra file to audit (repeatable)")
    coverage = add("coverage", "what the dataset holds, per category, archetype, layer, and owner")
    coverage.add_argument("--expect-entries", dest="expect_entries", type=int,
                          help="fail if the dataset holds a different number of capabilities")
    coverage.add_argument("--expect-fingerprint", dest="expect_fingerprint",
                          help="fail if the dataset fingerprint differs")
    validate = add("validate", "validate the dataset, or a records file against one record schema")
    validate.add_argument("--records", metavar="PATH", help="generated artifact to check")
    validate.add_argument("--kind", default="spec_record",
                          help="record definition to check against: "
                               "spec_record|plan_item|checklist_item|evidence_record|gate_verdict|"
                               "asset_manifest_record|measurements_record|capability_ref")
    export = add("export", "write the selection or the whole catalog as CSV or JSON")
    export.add_argument("--columns", action="append", help="comma separated column list")
    imp = add("import", "rebuild a dataset from plain text, Markdown, HTML, CSV, TSV, or JSON")
    imp.add_argument("--source", metavar="PATH", help="the catalog text to read")
    imp.add_argument("--input-format", dest="input_format",
                     choices=("auto", "markdown", "html", "text", "csv", "tsv", "json"), default="auto",
                     help="force a parser instead of detecting one")
    imp.add_argument("--summary-only", dest="summary_only", action="store_true",
                     help="report the parse without writing a dataset")
    config = add("config", "show the merged configuration, or write an editable copy")
    config.add_argument("action", choices=("show", "init"), help="what to do")
    add("doctor", "twelve read-only checks of the install and its consistency")
    cleanup = add("cleanup", "find or remove the temp files an interrupted write left behind")
    cleanup.add_argument("--directory", help="folder to scan (default: the current one)")
    cleanup.add_argument("--older-than", dest="older_than", type=float,
                         help="ignore files newer than this many minutes")
    cleanup.add_argument("--delete", action="store_true",
                         help="delete what was found (needs --approve DELETE-FILES)")
    cleanup.add_argument("--include-backups", dest="include_backups", action="store_true",
                         help="also offer the .bak files this tool wrote")
    selftest = add("selftest", "run the unit suite that exercises every command; add --verbose for "
                              "per-test output")
    selftest.add_argument("--only", action="append", metavar="NAME",
                          help="run one test case or test method, e.g. --only TestSelection")
    helpcmd = add("help", "usage for one command")
    helpcmd.add_argument("topic", nargs="?", help="command to explain")
    for name in ("show", "spec", "plan", "checklist", "prompts", "export", "evidence"):
        sub.choices[name].add_argument("refs", nargs="*", metavar="REF",
                                       help="capability ids, short ids, slugs, or exact names (repeatable)")
    return parser


def _normalize_args(parser: argparse.ArgumentParser, args) -> None:
    """Shared defaults for subparsers that do not declare them."""
    for attribute, value in (("refs", []), ("all", False), ("category", None), ("layer", None),
                             ("tag", None), ("difficulty", None), ("role", None), ("risk", None),
                             ("engine_scoped", False), ("from_file", None), ("format", None),
                             ("format_explicit", False), ("out", None), ("out_dir_flag", None),
                             ("dataset", None), ("assets_dir", None), ("config", None), ("limit", None),
                             ("offset", 0), ("max_targets", None), ("max_input_mb", None),
                             ("timeout", None), ("retries", None), ("only", []), ("allow_overwrite", False),
                             ("approve", ""), ("backup", False), ("yes", False), ("quiet", False),
                             ("stamp", False), ("report_only", False), ("verbose", False),
                             ("fields", None), ("engine", None), ("platform", None),
                             ("device_profile", None), ("region", None), ("provider_kind", None),
                             ("provider_model", None), ("provider_endpoint", None), ("sprint", None),
                             ("owner", None), ("input", None), ("summary_only", False)):
        if not hasattr(args, attribute):
            setattr(args, attribute, value)
    if hasattr(args, "format") and args.format is not None:
        args.format_explicit = True
        args.format = args.format
    else:
        args.format = "json"
    if isinstance(getattr(args, "fields", None), list):
        flattened = [item.strip() for chunk in args.fields for item in chunk.split(",") if item.strip()]
        args.fields = flattened or None
    if isinstance(getattr(args, "columns", None), list):
        flattened = [item.strip() for chunk in args.columns for item in chunk.split(",") if item.strip()]
        args.columns = flattened or None
    if getattr(args, "refs", None) and args.command == "help":
        args.topic = args.topic or args.refs[0]
        args.refs = args.refs[1:]
    if args.command in ("info", "capabilities", "categories", "doctor") and getattr(args, "refs", None):
        raise kit.UsageError(f"'{args.command}' takes no capability references",
                             hint=f"run `{RUN} help {args.command}` to see what it accepts",
                             where="positional arguments")


HANDLERS = {"info": cmd_info, "capabilities": cmd_capabilities, "categories": cmd_categories,
            "list": cmd_list, "show": cmd_show, "search": cmd_search,
            "spec": cmd_spec, "plan": cmd_plan, "checklist": cmd_checklist, "prompts": cmd_prompts,
            "evidence": cmd_evidence, "rights": cmd_rights, "budget": cmd_budget,
            "offline-audit": cmd_offline_audit, "coverage": cmd_coverage, "validate": cmd_validate,
            "export": cmd_export, "import": cmd_import, "config": cmd_config, "doctor": cmd_doctor,
            "cleanup": cmd_cleanup, "selftest": cmd_selftest, "help": cmd_help}


def main(argv=None, standalone: bool = True) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    parser = build_parser()
    if not argv:
        argv = ["info"]
    try:
        args = parser.parse_args(argv)
        _normalize_args(parser, args)
        ctx = Ctx(args)
    except SystemExit as exit_signal:  # argparse already printed the reason
        return int(exit_signal.code or 0)
    except kit.ToolError as error:
        stream = sys.stderr if _wants_human(argv) else sys.stdout
        if not _wants_quiet(argv):
            stream.write(kit.dump_json({"ok": False, "errors": [error.to_dict()]}, indent=1))
        return error.exit_code
    except Exception as exc:  # pragma: no cover - a bug must not print a bare traceback
        payload = {"ok": False, "errors": [{"code": "E-INTERNAL",
                                            "message": f"{type(exc).__name__}: {exc}",
                                            "hint": "re-run with --verbose for the traceback",
                                            "where": PACKAGE + "/" + ENTRY_NAME}]}
        if os.environ.get("OGD_DEBUG") or (argv and "--verbose" in argv):
            import traceback

            payload["errors"][0]["traceback"] = traceback.format_exc().splitlines()[-8:]
        sys.stderr.write(kit.dump_json(payload, indent=1))
        return kit.EXIT_INTERNAL
    ctx.args.parser = parser
    handler = HANDLERS[args.command]
    ctx.cancel.install(enabled=standalone)
    ctx.deadline.arm()
    try:
        return handler(ctx)
    except kit.ToolError as error:
        ctx.result.error(error)
        return _fail(ctx, error)
    except RecursionError as exc:  # pragma: no cover
        return _fail(ctx, kit.InternalError(f"recursion limit reached: {exc}",
                                            hint="narrow the selection with --limit", where=ctx.package_dir))
    except Exception as exc:  # pragma: no cover
        return _fail(ctx, _wrap_unexpected(exc, ctx))
    finally:
        ctx.deadline.close()
        ctx.cancel.restore()
        _prune_run_temps(ctx)


def _wrap_unexpected(exc: Exception, ctx: Ctx) -> kit.ToolError:
    if os.environ.get("OGD_DEBUG") or getattr(ctx.args, "verbose", False):
        import traceback

        sys.stderr.write(traceback.format_exc())
    if isinstance(exc, (json.JSONDecodeError, UnicodeDecodeError)):
        return kit.InputError(f"the input could not be decoded: {exc}",
                              hint="the file must be UTF-8, UTF-16, or latin-1 text",
                              where=ctx.package_dir)
    if isinstance(exc, (PermissionError, NotADirectoryError, IsADirectoryError, FileNotFoundError)):
        return kit.InputError(f"{type(exc).__name__}: {exc}",
                              hint="check the path and the directory permissions", where=ctx.package_dir)
    if isinstance(exc, MemoryError):
        return kit.LimitExceeded("out of memory while building the output",
                                  hint="lower --limit or --max-targets and re-run", where=ctx.package_dir)
    return kit.InternalError(f"{type(exc).__name__}: {exc}",
                             hint="this is a defect, not a bad input; re-run with --verbose and report the "
                                  "command line", where=PACKAGE + "/" + ENTRY_NAME)


def _wants_human(argv) -> bool:
    """True when --format names a human format, so an early failure knows where to speak."""
    tokens = list(argv)
    for index, token in enumerate(tokens):
        if token.startswith("--format="):
            return token.split("=", 1)[1] != "json"
        if token == "--format" and index + 1 < len(tokens):
            return tokens[index + 1] != "json"
    return False


def _wants_quiet(argv) -> bool:
    return "--quiet" in list(argv) or "-q" in list(argv)


def _fail(ctx: Ctx, error: kit.ToolError) -> int:
    """Report a failure without ever faking a partial success."""
    if error.code not in {item["code"] for item in ctx.result.errors}:
        ctx.result.error(error)
    if ctx.quiet:
        sys.stderr.write(kit.dump_json({"ok": False, "errors": ctx.result.errors}, indent=1))
    elif ctx.fmt == "json" or ctx._written:
        sys.stdout.write(ctx.result.to_json(indent=1, include_timestamp=ctx.stamp))
    else:
        sys.stderr.write(kit.dump_json({"ok": False, "errors": ctx.result.errors}, indent=1))
    return error.exit_code


def _prune_run_temps(ctx: Ctx) -> None:
    """Remove this run's own leftovers, so a failed run never litters the tool folder."""
    for directory in {os.path.dirname(item["path"]) for item in ctx._written} | {ctx.package_dir}:
        if not os.path.isdir(directory):
            continue
        for name in os.listdir(directory):
            if not name.startswith(kit.TEMP_PREFIX):
                continue
            path = os.path.join(directory, name)
            try:
                if os.path.isfile(path) and os.path.getsize(path) == 0:
                    os.unlink(path)
            except OSError:  # pragma: no cover
                pass


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
