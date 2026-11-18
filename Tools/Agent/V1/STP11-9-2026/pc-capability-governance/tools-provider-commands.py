"""Provider command table for the ``pc-capability-governance`` Skill Tool.

Each provider entry is data, not code: the primary entry point reads this table
to know how to detect a capability, what version output to parse, which hard
host requirements apply, and what to recommend when the probe fails.

Provider fields
---------------
id : str                     stable capability identifier used everywhere
label : str                  human readable name
category : str               catalog category this capability belongs to
tools : list[str]            executables the capability normally needs
version_cmd : str            read-only command whose output contains the version
version_regex : str|None     optional explicit regex; default parser otherwise
version_min / version_max    supported version window (inclusive)
requirements : dict          os / arch / gpu / virtualization / network booleans
license_required : bool      tool must be licensed before lawful use
missing_status : str         contract status reported when the tool is absent
install_hint : str           shortest justified remediation
verify_cmd : str|None        read-only functional probe beyond --version
notes : str                  short factual remark surfaced in reports

Custom providers supplied with ``--providers <file.json>`` use the same fields;
any omitted field falls back to the documented default.
"""

from __future__ import annotations

import json
import os
import shlex

from tools_probe_lib import STATUS_MISSING, SkillError

DEFAULT_REQUIREMENTS = {"os": ["any"], "arch": ["any"], "gpu": False, "virtualization": False, "network": False}

PROVIDERS = [
    # --- governance / meta ---------------------------------------------------
    {
        "id": "python3",
        "label": "Python 3 (Skill Tool runtime)",
        "category": "Capability Discovery, Routing, and Governance",
        "tools": ["python3"],
        "version_cmd": "python3 --version",
        "version_min": "3.9",
        "requirements": {"os": ["any"]},
        "install_hint": "Install Python 3.9+ (apt install python3 | winget install Python.Python.3.12).",
        "notes": "Standard library only; no third-party packages required by this Skill Tool.",
    },
    {
        "id": "git",
        "label": "Git (provenance and source control)",
        "category": "Capability Discovery, Routing, and Governance",
        "tools": ["git"],
        "version_cmd": "git --version",
        "requirements": {"os": ["any"]},
        "install_hint": "Install Git (apt install git | winget install Git.Git).",
    },
    {
        "id": "sha256sum",
        "label": "SHA-256 hashing utility",
        "category": "Capability Discovery, Routing, and Governance",
        "tools": ["sha256sum", "shasum"],
        "version_cmd": "sha256sum --version",
        "requirements": {"os": ["any"]},
        "install_hint": "Provided by coreutils; install coreutils or use the built-in hashing fallback.",
    },
    # --- native build --------------------------------------------------------
    {
        "id": "gcc",
        "label": "GNU C compiler",
        "category": "Core Native and Cross-Platform Build Systems",
        "tools": ["gcc", "g++"],
        "version_cmd": "gcc --version",
        "version_min": "9.0",
        "requirements": {"os": ["any"]},
        "install_hint": "Install build-essential (apt) or gcc-toolchain.",
    },
    {
        "id": "clang",
        "label": "LLVM Clang compiler",
        "category": "Core Native and Cross-Platform Build Systems",
        "tools": ["clang", "clang++"],
        "version_cmd": "clang --version",
        "version_min": "14.0",
        "requirements": {"os": ["any"]},
        "install_hint": "Install clang and lld (apt install clang lld).",
    },
    {
        "id": "cmake",
        "label": "CMake build system",
        "category": "Core Native and Cross-Platform Build Systems",
        "tools": ["cmake"],
        "version_cmd": "cmake --version",
        "version_min": "3.20",
        "requirements": {"os": ["any"]},
        "install_hint": "Install CMake 3.20+ (apt install cmake).",
    },
    {
        "id": "ninja",
        "label": "Ninja build runner",
        "category": "Core Native and Cross-Platform Build Systems",
        "tools": ["ninja"],
        "version_cmd": "ninja --version",
        "version_min": "1.10",
        "requirements": {"os": ["any"]},
        "install_hint": "Install ninja-build.",
    },
    {
        "id": "make",
        "label": "GNU Make",
        "category": "Core Native and Cross-Platform Build Systems",
        "tools": ["make"],
        "version_cmd": "make --version",
        "requirements": {"os": ["any"]},
        "install_hint": "Install make / build-essential.",
    },
    {
        "id": "mingw-w64",
        "label": "MinGW-w64 Windows cross-compiler",
        "category": "Windows Native Build Workers and SDK Tooling",
        "tools": ["x86_64-w64-mingw32-gcc"],
        "version_cmd": "x86_64-w64-mingw32-gcc --version",
        "requirements": {"os": ["any"], "arch": ["x86_64", "amd64"]},
        "install_hint": "Install mingw-w64 on a Linux host (apt install mingw-w64).",
    },
    {
        "id": "dotnet-sdk",
        "label": ".NET SDK",
        "category": ".NET, Mono, and Managed Windows Development",
        "tools": ["dotnet"],
        "version_cmd": "dotnet --info",
        "version_regex": r"Version:\s*(\d+\.\d+\.\d+)",
        "requirements": {"os": ["any"]},
        "install_hint": "Install a supported .NET LTS SDK (dotnet-install script or packages.microsoft.com).",
    },
    {
        "id": "mono",
        "label": "Mono runtime and MSBuild",
        "category": ".NET, Mono, and Managed Windows Development",
        "tools": ["mono", "msbuild"],
        "version_cmd": "mono --version",
        "requirements": {"os": ["any"]},
        "install_hint": "Install mono-complete for legacy managed builds.",
    },
    {
        "id": "java",
        "label": "Java runtime and JDK tooling",
        "category": "Java, Python, Node.js, Electron, and Tauri Desktop Builds",
        "tools": ["java", "javac"],
        "version_cmd": "java -version",
        "version_regex": r'version "([^"]+)"',
        "requirements": {"os": ["any"]},
        "install_hint": "Install an LTS OpenJDK (apt install openjdk-17-jdk).",
    },
    {
        "id": "node",
        "label": "Node.js runtime",
        "category": "Java, Python, Node.js, Electron, and Tauri Desktop Builds",
        "tools": ["node"],
        "version_cmd": "node --version",
        "version_min": "18.0",
        "requirements": {"os": ["any"]},
        "install_hint": "Install Node.js LTS (nvm or the nodejs package).",
    },
    {
        "id": "npm",
        "label": "npm package manager",
        "category": "Java, Python, Node.js, Electron, and Tauri Desktop Builds",
        "tools": ["npm"],
        "version_cmd": "npm --version",
        "requirements": {"os": ["any"]},
        "install_hint": "Installed with Node.js; verify PATH and npm prefix permissions.",
    },
    # --- archives and media --------------------------------------------------
    {
        "id": "7zip",
        "label": "7-Zip archive suite",
        "category": "Archives, Compression, Optical Media, WIM, and Disk Images",
        "tools": ["7z", "7za", "7zz"],
        "version_cmd": "7z i",
        "requirements": {"os": ["any"]},
        "install_hint": "Install p7zip-full (Linux) or 7-Zip (Windows).",
    },
    {
        "id": "zip",
        "label": "ZIP archive utility",
        "category": "Archives, Compression, Optical Media, WIM, and Disk Images",
        "tools": ["zip", "unzip"],
        "version_cmd": "unzip -v",
        "requirements": {"os": ["any"]},
        "install_hint": "Install zip and unzip.",
    },
    {
        "id": "ffmpeg",
        "label": "FFmpeg media toolkit",
        "category": "Audio, Video, Images, 3D Models, and Game Data",
        "tools": ["ffmpeg", "ffprobe"],
        "version_cmd": "ffmpeg -version",
        "requirements": {"os": ["any"]},
        "install_hint": "Install ffmpeg from your distribution or an official build.",
    },
    {
        "id": "imagemagick",
        "label": "ImageMagick image toolkit",
        "category": "Audio, Video, Images, 3D Models, and Game Data",
        "tools": ["magick", "convert"],
        "version_cmd": "magick -version",
        "requirements": {"os": ["any"]},
        "install_hint": "Install imagemagick (verify policy.xml before handling untrusted files).",
    },
    {
        "id": "exiftool",
        "label": "ExifTool metadata reader",
        "category": "Audio, Video, Images, 3D Models, and Game Data",
        "tools": ["exiftool"],
        "version_cmd": "exiftool -ver",
        "requirements": {"os": ["any"]},
        "install_hint": "Install libimage-exiftool-perl for read-only metadata inspection.",
    },
    {
        "id": "sqlite3",
        "label": "SQLite command line",
        "category": "Databases, Application State, Browsers, and Local Data",
        "tools": ["sqlite3"],
        "version_cmd": "sqlite3 --version",
        "requirements": {"os": ["any"]},
        "install_hint": "Install sqlite3; open local state databases read-only.",
    },
    # --- binary inspection ---------------------------------------------------
    {
        "id": "readelf",
        "label": "GNU/LLVM ELF and PE inspection tools",
        "category": "PE, COFF, EXE, DLL, and Native Binary Inspection",
        "tools": ["readelf", "objdump", "llvm-readobj", "llvm-objdump"],
        "version_cmd": "readelf --version",
        "requirements": {"os": ["any"]},
        "install_hint": "Install binutils or the LLVM toolchain.",
    },
    {
        "id": "file",
        "label": "libmagic file type identification",
        "category": "PE, COFF, EXE, DLL, and Native Binary Inspection",
        "tools": ["file"],
        "version_cmd": "file --version",
        "requirements": {"os": ["any"]},
        "install_hint": "Install the file utility and libmagic.",
    },
    {
        "id": "strings",
        "label": "strings extractor",
        "category": "PE, COFF, EXE, DLL, and Native Binary Inspection",
        "tools": ["strings"],
        "version_cmd": "strings --version",
        "requirements": {"os": ["any"]},
        "install_hint": "Install binutils.",
    },
    {
        "id": "pefile",
        "label": "Python pefile PE parser",
        "category": "PE, COFF, EXE, DLL, and Native Binary Inspection",
        "tools": ["python3"],
        "version_cmd": "python3 -c import pefile; print(pefile.__version__)",
        "requires_module": "pefile",
        "requirements": {"os": ["any"]},
        "install_hint": "pip install pefile (needed only for PE metadata parsing).",
    },
    {
        "id": "lief",
        "label": "LIEF binary parser",
        "category": "PE, COFF, EXE, DLL, and Native Binary Inspection",
        "tools": ["python3"],
        "version_cmd": "python3 -c import lief; print(lief.__version__)",
        "requires_module": "lief",
        "requirements": {"os": ["any"]},
        "install_hint": "pip install lief (needed only for cross-format parsing/rewriting).",
    },
    {
        "id": "radare2",
        "label": "radare2 reverse engineering framework",
        "category": "Debugging, Crash Dumps, Symbols, Tracing, and Profiling",
        "tools": ["r2", "radare2"],
        "version_cmd": "r2 -v",
        "requirements": {"os": ["any"]},
        "install_hint": "Install radare2 for authorized static inspection only.",
    },
    {
        "id": "detect-it-easy",
        "label": "Detect It Easy binary identifier",
        "category": "PE, COFF, EXE, DLL, and Native Binary Inspection",
        "tools": ["diec", "die"],
        "version_cmd": "diec --version",
        "requirements": {"os": ["any"]},
        "install_hint": "Install Detect It Easy from the vendor release; requires licence acceptance.",
        "license_required": True,
    },
    # --- compatibility / virtualisation --------------------------------------
    {
        "id": "wine",
        "label": "Wine compatibility layer",
        "category": "Windows Execution, Wine, Proton, and Compatibility Layers",
        "tools": ["wine"],
        "version_cmd": "wine --version",
        "requirements": {"os": ["linux"]},
        "install_hint": "Install wine (and winetricks) on a Linux host with a dedicated prefix.",
    },
    {
        "id": "qemu-system",
        "label": "QEMU system emulator",
        "category": "Virtual Machines, Emulation, Sandboxes, and Historical PCs",
        "tools": ["qemu-system-x86_64"],
        "version_cmd": "qemu-system-x86_64 --version",
        "version_min": "6.0",
        "requirements": {"os": ["any"]},
        "install_hint": "Install qemu-system-x86 for emulation; KVM adds acceleration only when present.",
    },
    {
        "id": "qemu-img",
        "label": "QEMU disk image utility",
        "category": "Virtual Machines, Emulation, Sandboxes, and Historical PCs",
        "tools": ["qemu-img"],
        "version_cmd": "qemu-img --version",
        "requirements": {"os": ["any"]},
        "install_hint": "Install qemu-utils for read-only image inspection and conversion.",
    },
    {
        "id": "kvm",
        "label": "KVM hardware virtualization",
        "category": "Virtual Machines, Emulation, Sandboxes, and Historical PCs",
        "tools": [],
        "version_cmd": "",
        "requirements": {"os": ["linux"], "virtualization": True},
        "install_hint": "Expose /dev/kvm (enable VT-x/AMD-V, add the user to the kvm group) or use a hosted VM worker.",
        "notes": "Detected from host state rather than an executable.",
    },
    # --- packaging and signing ----------------------------------------------
    {
        "id": "osslsigncode",
        "label": "osslsigncode Authenticode signer",
        "category": "Installers, Packaging, Signing, and Distribution",
        "tools": ["osslsigncode"],
        "version_cmd": "osslsigncode --version",
        "requirements": {"os": ["any"]},
        "install_hint": "Install osslsigncode; signing needs a key you own (see approval policy).",
    },
    {
        "id": "nsis",
        "label": "NSIS installer builder",
        "category": "Installers, Packaging, Signing, and Distribution",
        "tools": ["makensis"],
        "version_cmd": "makensis -VERSION",
        "requirements": {"os": ["any"]},
        "install_hint": "Install nsis (makensis).",
    },
    {
        "id": "signtool",
        "label": "Windows Authenticode signing tool",
        "category": "Installers, Packaging, Signing, and Distribution",
        "tools": ["signtool"],
        "version_cmd": "signtool /?",
        "requirements": {"os": ["windows"]},
        "license_required": True,
        "install_hint": "Install the Windows SDK on a Windows signing worker with your own certificate.",
    },
    # --- network / security --------------------------------------------------
    {
        "id": "openssl",
        "label": "OpenSSL CLI",
        "category": "Networking, TLS, Remote Access, and Service Diagnostics",
        "tools": ["openssl"],
        "version_cmd": "openssl version",
        "requirements": {"os": ["any"]},
        "install_hint": "Install openssl for local TLS material inspection.",
    },
    {
        "id": "curl",
        "label": "curl HTTP client",
        "category": "Networking, TLS, Remote Access, and Service Diagnostics",
        "tools": ["curl"],
        "version_cmd": "curl --version",
        "requirements": {"os": ["any"], "network": True},
        "install_hint": "Install curl; network use requires approval under the execution policy.",
    },
    {
        "id": "syft",
        "label": "Syft SBOM generator",
        "category": "Source Code Navigation, Quality, Testing, and Supply Chain",
        "tools": ["syft"],
        "version_cmd": "syft version",
        "requirements": {"os": ["any"]},
        "install_hint": "Install Syft for local SBOM generation (offline DB updates are separate).",
    },
    {
        "id": "trivy",
        "label": "Trivy vulnerability scanner",
        "category": "Source Code Navigation, Quality, Testing, and Supply Chain",
        "tools": ["trivy"],
        "version_cmd": "trivy --version",
        "requirements": {"os": ["any"], "network": True},
        "install_hint": "Install Trivy; database refresh needs approved network access.",
    },
    # --- containers ----------------------------------------------------------
    {
        "id": "docker",
        "label": "Docker container runtime",
        "category": "Containers, WSL, Linux Desktop, and Cross-Platform Integration",
        "tools": ["docker"],
        "version_cmd": "docker --version",
        "requirements": {"os": ["any"], "virtualization": False},
        "install_hint": "Install Docker Engine/Desktop or use a remote builder approved by policy.",
        "notes": "Privileged runtime: container execution needs explicit approval.",
    },
    {
        "id": "wsl",
        "label": "Windows Subsystem for Linux",
        "category": "Containers, WSL, Linux Desktop, and Cross-Platform Integration",
        "tools": ["wsl"],
        "version_cmd": "wsl --version",
        "requirements": {"os": ["windows"]},
        "install_hint": "Enable WSL2 on the Windows worker.",
    },
]

PROVIDER_INDEX = {}


def providers(extra_path=None):
    """Return the provider list, optionally merged with a user provider file."""
    table = [dict(entry) for entry in PROVIDERS]
    if extra_path:
        custom = _load_custom(extra_path)
        known = {entry["id"] for entry in table}
        for entry in custom:
            if entry["id"] in known:
                table = [entry if old["id"] == entry["id"] else old for old in table]
            else:
                table.append(entry)
    for entry in table:
        _normalise(entry)
    return table


def _load_custom(path):
    if not os.path.isfile(path):
        raise SkillError("INVALID_INPUT", f"provider file not found: {path}")
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except json.JSONDecodeError as exc:
        raise SkillError("INVALID_INPUT", f"provider file is not valid JSON: {exc.msg}", {"line": exc.lineno})
    entries = data.get("providers") if isinstance(data, dict) else data
    if not isinstance(entries, list):
        raise SkillError("INVALID_INPUT", "provider file must contain a list or {'providers': [...]}")
    out = []
    for index, raw in enumerate(entries):
        if not isinstance(raw, dict):
            raise SkillError("INVALID_INPUT", f"provider entry {index} must be an object")
        if not raw.get("id") or not isinstance(raw["id"], str):
            raise SkillError("INVALID_INPUT", f"provider entry {index} needs a string 'id'")
        out.append(dict(raw))
    return out


def _normalise(entry):
    entry.setdefault("label", entry.get("id", "unknown"))
    entry.setdefault("category", "Capability Discovery, Routing, and Governance")
    entry.setdefault("tools", [])
    entry.setdefault("version_cmd", "")
    entry.setdefault("version_regex", None)
    entry.setdefault("version_min", None)
    entry.setdefault("version_max", None)
    entry.setdefault("requirements", dict(DEFAULT_REQUIREMENTS))
    entry.setdefault("license_required", False)
    entry.setdefault("missing_status", STATUS_MISSING)
    entry.setdefault("install_hint", "")
    entry.setdefault("verify_cmd", None)
    entry.setdefault("notes", "")
    entry.setdefault("requires_module", None)
    entry.setdefault("timeout_seconds", 10)
    req = entry["requirements"] or {}
    merged = dict(DEFAULT_REQUIREMENTS)
    merged.update({k: v for k, v in req.items() if v not in (None, "")})
    entry["requirements"] = merged
    if entry["version_cmd"]:
        entry["_argv"] = shlex.split(entry["version_cmd"], posix=(os.name != "nt"))
    else:
        entry["_argv"] = []
    if entry.get("verify_cmd"):
        entry["_verify_argv"] = shlex.split(entry["verify_cmd"], posix=(os.name != "nt"))
    else:
        entry["_verify_argv"] = []
    return entry
