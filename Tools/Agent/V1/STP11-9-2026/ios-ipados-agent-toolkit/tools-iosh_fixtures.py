#!/usr/bin/env python3
"""tools-iosh_fixtures.py -- regenerate the bundled lawful test fixtures.

The asset-fixture-* files are synthesised, not extracted from any Apple
product: they contain no Apple code, SDK content, certificate material or user
data. They exist so that the parsers in this Skill Tool can be proven end to end
without a Mac, a device or an Apple account.

Usage:
  python3 tools-iosh_fixtures.py                  # regenerate the asset-fixture-* files in place
  python3 tools-iosh_fixtures.py --out /tmp/fx --check   # only verify a directory
"""
from __future__ import annotations

import argparse
import json
import plistlib
import struct
import sys
import zipfile
from pathlib import Path
from typing import Any, Dict

HERE = Path(__file__).resolve().parent
def _bootstrap_modules() -> None:
    """Load the sibling tools-* modules.

    The required ``tools-`` filename prefix cannot be a Python identifier, so the
    modules are loaded from their exact filenames and registered under importable
    aliases (tools_iosh_core, tools_iosh_apple, tools_iosh_ops) in the documented
    load order.
    """
    import importlib.util
    for filename, module_name in (
        ("tools-iosh_core.py", "tools_iosh_core"),
        ("tools-iosh_apple.py", "tools_iosh_apple"),
        ("tools-iosh_ops.py", "tools_iosh_ops"),
    ):
        if module_name in sys.modules:
            continue
        path = HERE / filename
        if not path.exists():
            raise SystemExit(f"missing required Skill Tool module: {path.as_posix()}")
        spec = importlib.util.spec_from_file_location(module_name, path)
        assert spec and spec.loader
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        spec.loader.exec_module(module)


_bootstrap_modules()

from tools_iosh_core import Limits, default_limits, InputError  # noqa: E402
import tools_iosh_apple as apple  # noqa: E402

FIXTURE_BUNDLE_ID = "com.example.fixture"

# Flat, prefixed names: every Skill Tool file lives directly in the folder, and a
# fixture file must never look like Apple product content.
NAMES = {
    "ipa": "asset-fixture-app.ipa",
    "dylib": "asset-fixture-arm64.dylib",
    "fat": "asset-fixture-fat-macho",
    "info": "asset-fixture-info.plist",
    "info_broken": "asset-fixture-info-broken.plist",
    "privacy": "asset-fixture-privacy.xcprivacy",
    "profile": "asset-fixture.mobileprovision",
    "crash": "asset-fixture-crash.ips",
    "pbxproj": "asset-fixture-project.pbxproj",
    "scheme": "asset-fixture-scheme.xcscheme",
    "xcconfig": "asset-fixture-config.xcconfig",
    "source": "asset-fixture-appdelegate.swift",
    "results": "asset-fixture-xctest-results.json",
}
FIXTURE_PROFILE_UUID = "11111111-2222-3333-4444-555555555555"
FIXTURE_DYLIB_UUID = "0F1E2D3C-4B5A-6978-8796-A5B4C3D2E1F0"


# --------------------------------------------------------------------------
# Mach-O synthesis (arm64, iOS 14.0, with LC_UUID / LC_BUILD_VERSION / dylibs)
# --------------------------------------------------------------------------
def _lc(cmd: int, payload: bytes) -> bytes:
    return struct.pack("<II", cmd, 8 + len(payload)) + payload


def _lc_str(cmd: int, text: str, extra: bytes = b"\x00\x00\x00\x00") -> bytes:
    """Build a dylib/rpath-style load command with an embedded string.

    Layout per <mach-o/loader.h>: cmd, cmdsize, union lc_str offset, then the
    command-specific fields (timestamp/versions), then the string. The offset is
    measured from the start of the load command.
    """
    str_off = 8 + 4 + len(extra)
    payload = struct.pack("<I", str_off) + extra + text.encode() + b"\x00"
    pad = (-(8 + len(payload))) % 8
    total = 8 + len(payload) + pad
    return struct.pack("<II", cmd, total) + payload + b"\x00" * pad


def build_thin_macho(*, filetype: int = 6, uuid_hex: str = FIXTURE_DYLIB_UUID,
                     platform: int = 2, minos: int = (14 << 16), dylibs=("/usr/lib/libSystem.B.dylib",),
                     install_name: str | None = "@/rpath/libfixture.dylib",
                     cputype: int = 0x0100000C, cpusubtype: int = 0) -> bytes:
    cmds = []
    cmds.append(_lc(0x1B, bytes.fromhex(uuid_hex.replace("-", ""))))          # LC_UUID
    cmds.append(_lc(0x32, struct.pack("<IIII", platform, minos, (17 << 16), 0)))  # LC_BUILD_VERSION
    cmds.append(_lc(0x19, b"__TEXT".ljust(16, b"\x00")
                    + struct.pack("<QQQQ", 0, 0x4000, 0, 0x4000)
                    + struct.pack("<iiII", 5, 5, 1, 0)))                       # LC_SEGMENT_64
    cmds.append(_lc(0x19, b"__LINKEDIT".ljust(16, b"\x00")
                    + struct.pack("<QQQQ", 0x4000, 0x400, 0x4000, 0x400)
                    + struct.pack("<iiII", 1, 1, 0, 0)))
    if install_name:
        cmds.append(_lc_str(0xD, install_name, struct.pack("<III", 0, 0x10000, 0x10000)))
    for d in dylibs:
        cmds.append(_lc_str(0xC, d, struct.pack("<III", 0, 0x10000, 0x10000)))
    cmds.append(_lc(0x1D, struct.pack("<II", 0x4000, 0)))                      # LC_CODE_SIGNATURE
    if filetype == 2:
        cmds.append(_lc(0x80000028, struct.pack("<QQ", 0x1000, 0)))            # LC_MAIN
    body = b"".join(cmds)
    header = struct.pack("<IIIIIIII", 0xFEEDFACF, cputype, cpusubtype, filetype,
                         len(cmds), len(body), 0, 0)
    pad = (1 << 12) - ((len(header) + len(body)) % (1 << 12))
    return header + body + (b"\x00" * (pad if pad < (1 << 12) else 0)) + b"FIXTURE\x00" * 8


def build_fat_macho() -> bytes:
    """Two slices (arm64 + x86_64 simulator) to exercise fat parsing."""
    arm = build_thin_macho(platform=2, minos=(14 << 16))
    sim_uuid = "AABBCCDD-EEFF-0011-2233-445566778899"
    sim = build_thin_macho(platform=7, minos=(14 << 16), uuid_hex=sim_uuid,
                           cputype=0x01000007, cpusubtype=3)
    align = 1 << 12
    off_arm = align
    off_sim = off_arm + ((len(arm) + align - 1) // align) * align
    header = struct.pack(">II", 0xCAFEBABE, 2)
    header += struct.pack(">IIIII", 0x0100000C, 0, off_arm, len(arm), 12)
    header += struct.pack(">IIIII", 0x01000007, 3, off_sim, len(sim), 12)
    blob = bytearray(header)
    blob += b"\x00" * (off_arm - len(blob))
    blob += arm
    blob += b"\x00" * (off_sim - len(blob))
    blob += sim
    return bytes(blob)


# --------------------------------------------------------------------------
# fixtures
# --------------------------------------------------------------------------
def fixture_broken_info_plist() -> Dict[str, Any]:
    """A deliberately non-compliant Info.plist used to prove the auditors fire."""
    broken = fixture_info_plist()
    broken.update({
        "CFBundleIdentifier": "$(PRODUCT_BUNDLE_IDENTIFIER)",
        "CFBundleShortVersionString": "",
        "UIDeviceFamily": [1],
        "UIRequiredDeviceCapabilities": ["armv7"],
        "NSCameraUsageDescription": "",
        "NSPhotoLibraryUsageDescription": "tbd",
    })
    broken.pop("UILaunchScreen", None)
    broken.pop("ITSAppUsesNonExemptEncryption", None)
    return broken


def fixture_info_plist() -> Dict[str, Any]:
    return {
        "CFBundleIdentifier": FIXTURE_BUNDLE_ID,
        "CFBundleExecutable": "Fixture",
        "CFBundleName": "Fixture",
        "CFBundleDisplayName": "Fixture App",
        "CFBundlePackageType": "APPL",
        "CFBundleShortVersionString": "1.4.2",
        "CFBundleVersion": "142",
        "UIDeviceFamily": [1, 2],
        "MinimumOSVersion": "14.0",
        "DTPlatformName": "iphoneos",
        "DTSDKName": "iphoneos17.0",
        "ITSAppUsesNonExemptEncryption": False,
        "UILaunchScreen": {"UIColorName": "AccentColor"},
        "UIApplicationSceneManifest": {
            "UIApplicationSupportsMultipleScenes": True,
            "UISceneConfigurations": {},
        },
        "UIBackgroundModes": ["fetch"],
        "UIRequiredDeviceCapabilities": ["arm64"],
        "CFBundleURLTypes": [{"CFBundleURLName": "com.example.fixture", "CFBundleURLSchemes": ["fixture"]}],
        "NSCameraUsageDescription": "The fixture app uses the camera to demonstrate a purpose string.",
        "NSPhotoLibraryUsageDescription": "",
        "CFBundleLocalizations": ["en", "de"],
        "BuildMachine": "fixture-host",
    }


def fixture_privacy_manifest() -> Dict[str, Any]:
    return {
        "NSPrivacyTracking": False,
        "NSPrivacyTrackingDomains": [],
        "NSPrivacyCollectedDataTypes": [
            {"NSPrivacyCollectedDataType": "NSPrivacyCollectedDataTypeDeviceID",
             "NSPrivacyCollectedDataTypeLinked": False,
             "NSPrivacyCollectedDataTypeTracking": False,
             "NSPrivacyCollectedDataTypePurposes": ["NSPrivacyCollectedDataTypePurposeAppFunctionality"]},
        ],
        "NSPrivacyAccessedAPITypes": [
            {"NSPrivacyAccessedAPIType": "NSPrivacyAccessedAPICategoryUserDefaults",
             "NSPrivacyAccessedAPITypeReasons": ["CA92.1"]},
            {"NSPrivacyAccessedAPIType": "NSPrivacyAccessedAPICategoryDiskSpace",
             "NSPrivacyAccessedAPITypeReasons": ["E174.1"]},
        ],
    }


def fixture_profile() -> bytes:
    payload = {
        "AppIDName": "Fixture App",
        "ApplicationIdentifierPrefix": ["ABCDE12345"],
        "CreationDate": __import__("datetime").datetime(2025, 1, 5, 12, 0, 0),
        "Platform": ["iOS"],
        "ExpirationDate": __import__("datetime").datetime(2027, 1, 5, 12, 0, 0),
        "Name": "Fixture Development Profile",
        "ProvisionedDevices": ["00008030-000000000000000E"],
        "TeamIdentifier": ["ABCDE12345"],
        "TeamName": "Fixture Team",
        "TimeToLive": 365,
        "UUID": FIXTURE_PROFILE_UUID,
        "Version": 1,
        "DeveloperCertificates": [b"\x30\x82\x03\x00FIXTURE-CERTIFICATE-PLACEHOLDER"],
        "Entitlements": {
            "application-identifier": "ABCDE12345.com.example.fixture",
            "com.apple.developer.team-identifier": "ABCDE12345",
            "get-task-allow": True,
            "keychain-access-groups": ["ABCDE12345.*"],
            "aps-environment": "development",
            "beta-reports-active": True,
        },
    }
    body = plistlib.dumps(payload, fmt=plistlib.FMT_XML)
    # CMS-like envelope: DER prefix + payload + sentinel; parsers must find the plist
    return b"\x30\x82\x0f\xff" + body + b"\x00" * 32


def fixture_crash_ips() -> str:
    header = {
        "app_name": "Fixture", "timestamp": "2026-02-01 10:00:00.00 +0000",
        "app_version": "1.4.2", "slice_uuid": FIXTURE_DYLIB_UUID,
        "build_version": "142", "platform": 2, "bundleID": FIXTURE_BUNDLE_ID,
        "os_version": "iPhone OS 17.4 (21E219)", "modelCode": "iPhone15,2",
        "incident_id": "FIXTURE-INCIDENT-0001",
    }
    body = {
        "procName": "Fixture", "pid": 4242, "modelCode": "iPhone15,2",
        "exception": {"type": 6, "signal": 5, "subtype": "KERN_INVALID_ADDRESS",
                      "codes": "0x0000000000000000, 0x0000000000000000"},
        "termination": {"namespace": "SIGNAL", "indicator": "Segmentation fault: 11",
                        "code": 11, "reasons": []},
        "faultingThread": 0,
        "usedImages": [
            {"name": "Fixture", "uuid": FIXTURE_DYLIB_UUID, "arch": "arm64",
             "path": "/private/var/containers/Bundle/Application/Fixture.app/Fixture"},
            {"name": "libsystem_kernel.dylib", "uuid": "AAAAAAAA-BBBB-CCCC-DDDD-EEEEEEEEEEEE",
             "arch": "arm64", "path": "/usr/lib/system/libsystem_kernel.dylib"},
        ],
        "threads": [
            {"triggered": True, "id": 1, "name": "main", "queue": "com.apple.main-thread",
             "frames": [
                 {"imageIndex": 0, "imageOffset": 8192, "symbol": "Fixture.forceUnwrap",
                  "symbolLocation": 24, "sourceFile": "Fixture.swift", "sourceLine": 42},
                 {"imageIndex": 0, "imageOffset": 16384, "symbol": "Fixture.load"},
                 {"imageIndex": 1, "imageOffset": 1024, "symbol": "__pthread_kill"},
             ]},
        ],
        "asi": {"libsystem_c.dylib": ["Fatal error: Unexpectedly found nil while unwrapping "
                                      "an Optional value"]},
    }
    return json.dumps(header) + "\n" + json.dumps(body) + "\n"


PBXPROJ = """// !$*UTF8*$!
{
	archiveVersion = 1;
	classes = {
	};
	objectVersion = 56;
	objects = {

/* Begin PBXNativeTarget section */
		AA0000000000000000000001 /* Fixture */ = {
			isa = PBXNativeTarget;
			buildConfigurationList = AA0000000000000000000002 /* Build configuration list */;
			buildPhases = (
				AA0000000000000000000003 /* Sources */,
				AA0000000000000000000004 /* Resources */,
				AA0000000000000000000005 /* ShellScript */,
			);
			dependencies = (
			);
			name = Fixture;
			productName = Fixture;
			productType = "com.apple.product-type.application";
		};
/* End PBXNativeTarget section */

/* Begin PBXSourceFilesBuildPhase section */
		AA0000000000000000000003 /* Sources */ = {
			isa = PBXSourcesBuildPhase;
			files = (
				AA0000000000000000000010 /* AppDelegate.swift in Sources */,
			);
			runOnlyForDeploymentPostprocessing = 0;
		};
		AA0000000000000000000004 /* Resources */ = {
			isa = PBXResourcesBuildPhase;
			files = (
			);
			runOnlyForDeploymentPostprocessing = 0;
		};
		AA0000000000000000000005 /* ShellScript */ = {
			isa = PBXShellScriptBuildPhase;
			shellPath = /bin/sh;
			shellScript = "echo \\"fixture phase\\" && exit 0";
		};
/* End PBXSourceFilesBuildPhase section */

/* Begin PBXFileReference section */
		AA0000000000000000000011 /* AppDelegate.swift */ = {
			isa = PBXFileReference;
			path = asset-fixture-appdelegate.swift;
			sourceTree = "<group>";
		};
/* End PBXFileReference section */

/* Begin XCBuildConfiguration section */
		AA0000000000000000000020 /* Debug */ = {
			isa = XCBuildConfiguration;
			buildSettings = {
				IPHONEOS_DEPLOYMENT_TARGET = 14.0;
				PRODUCT_BUNDLE_IDENTIFIER = com.example.fixture;
				SWIFT_VERSION = 5.0;
				SUPPORTS_MACCATALYST = NO;
			};
			name = Debug;
		};
		AA0000000000000000000021 /* Release */ = {
			isa = XCBuildConfiguration;
			buildSettings = {
				IPHONEOS_DEPLOYMENT_TARGET = 14.0;
				PRODUCT_BUNDLE_IDENTIFIER = com.example.fixture;
				SWIFT_VERSION = 5.0;
				SWIFT_OPTIMIZATION_LEVEL = "-O";
			};
			name = Release;
		};
/* End XCBuildConfiguration section */

/* Begin XCConfigurationList section */
		AA0000000000000000000002 /* Build configuration list */ = {
			isa = XCConfigurationList;
			buildConfigurations = (
				AA0000000000000000000020 /* Debug */,
				AA0000000000000000000021 /* Release */,
			);
			defaultConfigurationName = Release;
		};
/* End XCConfigurationList section */
	};
	rootObject = AA0000000000000000000001;
}
"""

XCCONFIG = """// fixture xcconfig
BASE_SDK = iphoneos
APP_SUFFIX = fixture
PRODUCT_BUNDLE_IDENTIFIER = com.example.$(APP_SUFFIX)
CODE_SIGN_STYLE = Automatic
SWIFT_VERSION = 5.0
"""


def build_ipa(dylib: bytes, fat: bytes) -> bytes:
    buf = __import__("io").BytesIO()
    info = fixture_info_plist()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        base = "Payload/Fixture.app/"
        zf.writestr(base + "Info.plist", plistlib.dumps(info, fmt=plistlib.FMT_BINARY))
        zf.writestr(base + "Fixture", build_thin_macho(filetype=2, uuid_hex=FIXTURE_DYLIB_UUID,
                                                      platform=2, install_name=None))
        zf.writestr(base + "PrivacyInfo.xcprivacy",
                    plistlib.dumps(fixture_privacy_manifest(), fmt=plistlib.FMT_XML))
        zf.writestr(base + "en.lproj/InfoPlist.strings", plistlib.dumps(
            {"CFBundleDisplayName": "Fixture", "NSCameraUsageDescription":
             "The fixture app uses the camera to demonstrate a purpose string."},
            fmt=plistlib.FMT_BINARY))
        zf.writestr(base + "de.lproj/InfoPlist.strings", plistlib.dumps(
            {"CFBundleDisplayName": "Fixture DE"}, fmt=plistlib.FMT_BINARY))
        zf.writestr(base + "Assets.car", b"FIXTURE-ASSET-CATALOG")
        zf.writestr(base + "Frameworks/FixtureKit.framework/FixtureKit", dylib)
        zf.writestr(base + "Frameworks/FixtureKit.framework/Info.plist", plistlib.dumps(
            {"CFBundleIdentifier": "com.example.fixturekit", "CFBundleExecutable": "FixtureKit",
             "CFBundlePackageType": "FMWK", "MinimumOSVersion": "14.0"}, fmt=plistlib.FMT_BINARY))
        zf.writestr(base + "Frameworks/libheavy.dylib", dylib)
        zf.writestr(base + "PlugIns/FixtureWidget.appex/Info.plist", plistlib.dumps(
            {"CFBundleIdentifier": "com.example.fixture.widget", "CFBundleExecutable": "FixtureWidget",
             "CFBundlePackageType": "XPC!", "NSExtension": {
                 "NSExtensionPointIdentifier": "com.apple.widgetkit-extension",
                 "NSExtensionPrincipalClass": "WidgetBundle"}}, fmt=plistlib.FMT_BINARY))
        zf.writestr(base + "PlugIns/FixtureWidget.appex/FixtureWidget", dylib)
        zf.writestr(base + "embedded.mobileprovision", fixture_profile())
    return buf.getvalue()


def build_project_fixture(out: Path) -> None:
    """Writes the flat Xcode-project fixture family into `out`."""
    (out / NAMES["pbxproj"]).write_text(PBXPROJ, encoding="utf-8")
    (out / NAMES["scheme"]).write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n<Scheme LastUpgradeVersion="1500">'
        '<BuildAction><BuildActionEntries><BuildActionEntry><BuildableReference '
        'BlueprintName="Fixture" BlueprintIdentifier="AA0000000000000000000001" '
        'BuildableName="Fixture.app" ReferencedContainer="container:asset-fixture-project.pbxproj"/>'
        '</BuildActionEntry></BuildActionEntries></BuildAction></Scheme>\n', encoding="utf-8")
    (out / NAMES["xcconfig"]).write_text(XCCONFIG, encoding="utf-8")
    (out / NAMES["source"]).write_text(
        'import UIKit\n\n@main\nfinal class AppDelegate: UIResponder, UIApplicationDelegate {\n'
        '    func application(_ app: UIApplication, didFinishLaunchingWithOptions '
        'options: [UIApplication.LaunchOptionsKey: Any]?) -> Bool { return true }\n}\n',
        encoding="utf-8")


def write_fixtures(out: Path) -> Dict[str, Any]:
    out.mkdir(parents=True, exist_ok=True)
    dylib = build_thin_macho()
    (out / NAMES["dylib"]).write_bytes(dylib)
    (out / NAMES["fat"]).write_bytes(build_fat_macho())
    (out / NAMES["info"]).write_bytes(
        plistlib.dumps(fixture_info_plist(), fmt=plistlib.FMT_XML, sort_keys=True))
    (out / NAMES["info_broken"]).write_bytes(
        plistlib.dumps(fixture_broken_info_plist(), fmt=plistlib.FMT_XML, sort_keys=True))
    (out / NAMES["privacy"]).write_bytes(
        plistlib.dumps(fixture_privacy_manifest(), fmt=plistlib.FMT_XML, sort_keys=True))
    (out / NAMES["profile"]).write_bytes(fixture_profile())
    (out / NAMES["crash"]).write_text(fixture_crash_ips(), encoding="utf-8")
    (out / NAMES["ipa"]).write_bytes(build_ipa(dylib, build_fat_macho()))
    build_project_fixture(out)
    (out / NAMES["results"]).write_text(json.dumps({
        "destination": "iPhone 15 / iOS 17.4",
        "retries": 1,
        "tests": [
            {"identifier": "FixtureTests.testLaunch", "status": "passed", "duration": 1.9,
             "attempt": 1},
            {"identifier": "FixtureTests.testPurchase", "status": "failed", "duration": 0.4,
             "attempt": 1, "message": "XCTAssertEqual failed: (0) is not equal to (1)",
             "attachments": ["failure-1.png"]},
            {"identifier": "FixtureTests.testPurchase", "status": "passed", "duration": 0.5,
             "attempt": 2},
        ]}, indent=1), encoding="utf-8")
    return {"files": sorted(p.name for p in out.iterdir() if p.name.startswith("asset-"))}


def verify_fixtures(out: Path) -> Dict[str, Any]:
    limits = default_limits()
    checks = {}
    dylib = out / NAMES["dylib"]
    info = apple.parse_macho(dylib.read_bytes(), path=dylib.as_posix())
    checks["thin_macho"] = info["slices"][0]["arch"] == "arm64" and \
        info["slices"][0]["uuid"] == FIXTURE_DYLIB_UUID and \
        str(info["slices"][0]["min_os"]).startswith("14.0")
    fat = apple.parse_macho((out / NAMES["fat"]).read_bytes(), path="fat")
    checks["fat_macho"] = sorted(fat["architectures"]) == ["arm64", "x86_64"]
    plist = apple.load_plist_file(out / NAMES["info"], limits)
    checks["plist_roundtrip"] = apple.parse_plist_bytes(
        apple.convert_plist((out / NAMES["info"]).read_bytes(), "binary"))["value"] == plist["value"]
    ipa = apple.inspect_ipa(out / NAMES["ipa"], limits)
    checks["ipa"] = ipa["bundle"]["bundle_id"] == FIXTURE_BUNDLE_ID and \
        ipa["bundle"]["minimum_os"] == "14.0" and len(ipa["extensions"]) == 1 and \
        len(ipa["frameworks"]) == 2
    prof = apple.parse_mobileprovision(out / NAMES["profile"], limits)
    checks["profile"] = prof["summary"]["uuid"] == FIXTURE_PROFILE_UUID and \
        prof["summary"]["distribution_method"] == "development"
    crash = apple.parse_crash_report(out / NAMES["crash"], limits)
    checks["crash"] = crash["format"] == "ips" and len(crash["frames"]) == 3
    import tools_iosh_ops as ops
    proj = ops.inspect_project(out / NAMES["pbxproj"], limits, xcconfig=True)
    checks["project"] = proj["summary"]["targets"] == 1 and proj["summary"]["shared_schemes"] == 1
    checks["all_ok"] = all(checks.values())
    return checks


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="regenerate or verify the bundled fixtures")
    ap.add_argument("--out", default=str(HERE))
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args(argv)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    if args.check:
        result = verify_fixtures(out)
        print(json.dumps(result, indent=2))
        return 0 if result["all_ok"] else 1
    summary = write_fixtures(out)
    print(json.dumps({"written_to": out.as_posix(), **summary}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
