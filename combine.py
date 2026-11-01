#!/usr/bin/env python3
"""
Combine the split APKs of two or more variants of the SAME app version into one set that an older or 32-bit
Android TV can install, without changing a single file.

Some developers publish one version in several variants that differ only in which processor they carry native
code for and which Android version their base APK declares as the minimum, for example:

    arm64-v8a    base needs Android 10+   native code 64-bit only
    armeabi-v7a  base needs Android 12L+  native code 32-bit

A 32-bit TV on Android 11 can install neither. Both variants are the same package, the same versionCode and the
same signature, so their pieces are interchangeable: the base that accepts the TV's Android plus the native split
for the TV's processor installs as one app, every piece still the developer's original, signed file.

This tool:
  1. reads every APK in the bundles (.apkm, .apks, .xapk, a folder or loose .apk files);
  2. refuses to go on unless every piece is the same package, the same versionCode and signed by the same
     certificate (apksigner), and says whether Google's source stamp is there;
  3. reads the device (Android version, processor, screen density, language) over adb, or takes it from flags;
  4. picks the pieces: the base with the lowest minimum the device meets, the native split for the device's
     first supported processor, the nearest density split and the language splits;
  5. prints the plan, and with `install` installs it with an install session over adb.

It never edits, re-signs or repacks an APK, and it never downloads one: you get the bundles yourself, from the
developer's own distribution or a mirror that keeps the original signatures.

Usage:
  combine.py plan    BUNDLE BUNDLE... [--sdk 30 --abi armeabi-v7a --density 320 --lang it]
  combine.py extract BUNDLE BUNDLE... --out DIR [device flags]
  combine.py install BUNDLE BUNDLE... [--serial SERIAL] [--allow-unverified]

Needs Python 3.8+, and from the Android SDK build-tools `apksigner` and `aapt2` (found on PATH, in $ANDROID_HOME
or with --build-tools DIR). `install` and reading the device need `adb`.
"""

import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile

DENSITIES = {"ldpi": 120, "mdpi": 160, "tvdpi": 213, "hdpi": 240, "xhdpi": 320, "xxhdpi": 480, "xxxhdpi": 640}
ABIS = ["arm64_v8a", "armeabi_v7a", "armeabi", "x86_64", "x86", "mips", "mips64"]


class Stop(Exception):
    """A check failed: say why and change nothing."""


# ---- tools --------------------------------------------------------------------------------------------------------

def find_tool(name, build_tools):
    if build_tools:
        path = os.path.join(build_tools, name)
        if os.path.exists(path):
            return path
    found = shutil.which(name)
    if found:
        return found
    for home in (os.environ.get("ANDROID_HOME"), os.environ.get("ANDROID_SDK_ROOT")):
        root = os.path.join(home or "", "build-tools")
        if home and os.path.isdir(root):
            for version in sorted(os.listdir(root), reverse=True):
                path = os.path.join(root, version, name)
                if os.path.exists(path):
                    return path
    raise Stop(f"{name} not found: put the Android SDK build-tools on PATH, set ANDROID_HOME or pass --build-tools")


def run(cmd, check=True):
    done = subprocess.run(cmd, capture_output=True, text=True)
    if check and done.returncode != 0:
        raise Stop(f"{' '.join(cmd[:2])} failed:\n{done.stdout}{done.stderr}")
    return done


# ---- reading the bundles -------------------------------------------------------------------------------------------

class Piece:
    """One APK: the base or a split, with what its manifest and signature say."""

    def __init__(self, path, bundle):
        self.path = path
        self.bundle = bundle
        self.name = os.path.basename(path)
        self.package = self.version = self.split = None
        self.min_sdk = 1
        self.native = []
        self.signer = None
        self.stamp = False

    @property
    def is_base(self):
        return self.split is None

    @property
    def abi(self):
        s = (self.split or "").replace("config.", "")
        return s if s in ABIS else None

    @property
    def density(self):
        s = (self.split or "").replace("config.", "")
        return DENSITIES.get(s)

    @property
    def lang(self):
        s = (self.split or "").replace("config.", "")
        return s if re.fullmatch(r"[a-z]{2,3}(_[A-Za-z]+)?", s) and s not in DENSITIES and s not in ABIS else None

    def __repr__(self):
        return f"{self.bundle}:{self.name}"


def unpack(bundle, into):
    """The .apk files of a bundle (zip), a folder or a lone .apk."""
    if os.path.isdir(bundle):
        return [os.path.join(bundle, f) for f in sorted(os.listdir(bundle)) if f.endswith(".apk")]
    if bundle.endswith(".apk"):
        return [bundle]
    if not zipfile.is_zipfile(bundle):
        raise Stop(f"{bundle}: not a bundle, folder or .apk")
    out = os.path.join(into, re.sub(r"\W", "_", os.path.basename(bundle)))
    os.makedirs(out)
    with zipfile.ZipFile(bundle) as z:
        names = [n for n in z.namelist() if n.endswith(".apk") and "/" not in n.strip("/")]
        if not names:
            raise Stop(f"{bundle}: no APKs inside (an encrypted or unusual bundle?)")
        for n in names:
            z.extract(n, out)
    return [os.path.join(out, n) for n in sorted(names)]


def read(piece, aapt2, apksigner):
    badging = run([aapt2, "dump", "badging", piece.path]).stdout
    m = re.search(r"package: name='([^']+)' versionCode='(\d+)'", badging)
    if not m:
        raise Stop(f"{piece}: aapt2 could not read the manifest")
    piece.package, piece.version = m.group(1), m.group(2)
    split = re.search(r" split='([^']+)'", badging.splitlines()[0])
    piece.split = split.group(1) if split else None
    sdk = re.search(r"^(?:minSdkVersion|sdkVersion):'(\d+)'", badging, re.M)
    piece.min_sdk = int(sdk.group(1)) if sdk else 1
    with zipfile.ZipFile(piece.path) as z:
        piece.native = sorted({n.split("/")[1] for n in z.namelist() if n.startswith("lib/") and n.endswith(".so")})
    certs = run([apksigner, "verify", "--print-certs", piece.path], check=False)
    if certs.returncode != 0:
        raise Stop(f"{piece}: the signature does not verify:\n{certs.stdout}{certs.stderr}")
    signer = re.search(r"Signer #1 certificate SHA-256 digest: ([0-9a-f]+)", certs.stdout)
    piece.signer = signer.group(1) if signer else None
    piece.stamp = "Source Stamp Signer certificate" in certs.stdout


def load(bundles, workdir, aapt2, apksigner):
    pieces = []
    for b in bundles:
        for path in unpack(b, workdir):
            p = Piece(path, os.path.basename(b))
            read(p, aapt2, apksigner)
            pieces.append(p)
    packages = {p.package for p in pieces}
    versions = {p.version for p in pieces}
    signers = {p.signer for p in pieces}
    if len(packages) != 1:
        raise Stop(f"the bundles are different apps: {sorted(packages)}")
    if len(versions) != 1:
        raise Stop(f"the bundles are different versions: {sorted(versions)} - download the same version of each variant")
    if len(signers) != 1 or None in signers:
        raise Stop(f"the pieces are not all signed by the same certificate: {sorted(map(str, signers))}")
    return pieces


# ---- the device ----------------------------------------------------------------------------------------------------

class Device:
    def __init__(self, sdk, abis, density, langs):
        self.sdk, self.abis, self.density, self.langs = sdk, abis, density, langs


def adb(args, serial, *cmd):
    base = [args.adb] + (["-s", serial] if serial else [])
    return run(base + list(cmd)).stdout.strip()


def read_device(args):
    if args.sdk and args.abi:
        return Device(args.sdk, [a.replace("-", "_") for a in args.abi.split(",")], args.density or 320,
                      (args.lang or "en").split(","))
    shell = lambda c: adb(args, args.serial, "shell", c)
    sdk = int(shell("getprop ro.build.version.sdk"))
    abis = [a.replace("-", "_") for a in shell("getprop ro.product.cpu.abilist").split(",") if a]
    dens = re.findall(r"(\d+)", shell("wm density"))
    density = int(dens[-1]) if dens else 320
    locale = shell("getprop persist.sys.locale") or shell("getprop ro.product.locale") or "en"
    return Device(args.sdk or sdk, abis, args.density or density, (args.lang or locale.split("-")[0]).split(","))


# ---- choosing ------------------------------------------------------------------------------------------------------

def choose(pieces, device):
    """The base, native, density and language pieces for the device; Stop when no combination fits."""
    bases = sorted((p for p in pieces if p.is_base), key=lambda p: p.min_sdk)
    fitting = [p for p in bases if p.min_sdk <= device.sdk]
    if not fitting:
        lowest = bases[0].min_sdk if bases else "?"
        raise Stop(f"no base APK accepts Android API {device.sdk}: the lowest minimum among them is {lowest}")
    base = fitting[0]
    plan = [base]
    if base.native and not set(base.native) & set(device.abis):
        raise Stop(f"the base carries native code only for {base.native}, the device runs {device.abis}")
    native = [p for p in pieces if p.abi]
    if native and not base.native:
        for abi in device.abis:
            match = [p for p in native if p.abi == abi]
            if match:
                plan.append(match[0])
                break
        else:
            raise Stop(f"no native split for the device's processors {device.abis}: "
                       f"the bundles have {sorted({p.abi for p in native})}")
    dens = [p for p in pieces if p.density]
    if dens:
        # As Android does: the nearest density at or above the screen's (scaled down stays sharp), else the largest.
        above = [p for p in dens if p.density >= device.density]
        plan.append(min(above, key=lambda p: p.density) if above else max(dens, key=lambda p: p.density))
    langs = [p for p in pieces if p.lang]
    for lang in dict.fromkeys(device.langs + ["en"]):
        match = [p for p in langs if p.lang.split("_")[0] == lang]
        if match:
            plan.append(match[0])
    return plan


def describe(plan, pieces, device):
    print(f"device: Android API {device.sdk}, processors {', '.join(device.abis)}, {device.density} dpi, "
          f"languages {', '.join(device.langs)}")
    p0 = plan[0]
    print(f"app:    {p0.package} versionCode {p0.version}, {len(pieces)} pieces read, one signer:")
    print(f"        SHA-256 {p0.signer}")
    print(f"        Google source stamp on {sum(p.stamp for p in pieces)} of {len(pieces)} pieces")
    print("plan:")
    for p in plan:
        what = "base" if p.is_base else p.split
        extra = f"  min API {p.min_sdk}" if p.is_base else ""
        extra += f"  native {', '.join(p.native)}" if p.native else ""
        print(f"  {what:24} {p.bundle} / {p.name}{extra}")
    mixed = len({p.bundle for p in plan}) > 1
    if mixed:
        print("note:   the pieces come from different variants of the same version; every file is unchanged")


# ---- installing ----------------------------------------------------------------------------------------------------

def install(args, plan):
    shell = lambda c: adb(args, args.serial, "shell", c)
    remote = []
    for i, p in enumerate(plan):
        dest = f"/data/local/tmp/split-combine-{i}.apk"
        adb(args, args.serial, "push", p.path, dest)
        remote.append((p, dest))
    previous = shell("settings get global verifier_verify_adb_installs")
    try:
        if args.allow_unverified:
            # Only for this install, and only because the pieces were verified above: the setting is put back below.
            shell("settings put global verifier_verify_adb_installs 0")
        out = shell("pm install-create -r")
        session = re.search(r"\[(\d+)\]", out)
        if not session:
            raise Stop(f"pm install-create: {out}")
        sid = session.group(1)
        for i, (p, dest) in enumerate(remote):
            size = os.path.getsize(p.path)
            out = shell(f"pm install-write -S {size} {sid} {i}_{p.name} {dest}")
            if "Success" not in out:
                shell(f"pm install-abandon {sid}")
                raise Stop(f"pm install-write {p.name}: {out}")
        out = shell(f"pm install-commit {sid}")
        print(out)
        if "Success" not in out:
            hint = ("\nhint: the device's verifier refused it; --allow-unverified turns USB install verification off "
                    "for this install only") if "VERIFICATION" in out and not args.allow_unverified else ""
            raise Stop(f"install failed{hint}")
    finally:
        if args.allow_unverified:
            if previous in ("", "null"):
                shell("settings delete global verifier_verify_adb_installs")
            else:
                shell(f"settings put global verifier_verify_adb_installs {previous}")
        shell("rm -f /data/local/tmp/split-combine-*.apk")
    info = shell(f"dumpsys package {plan[0].package} | grep -E 'versionName|primaryCpuAbi' | head -2")
    print(info)


# ---- main ----------------------------------------------------------------------------------------------------------

def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0].strip(),
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=["plan", "extract", "install"])
    ap.add_argument("bundles", nargs="+", help=".apkm/.apks/.xapk files, folders of .apk, or .apk files")
    ap.add_argument("--out", help="extract: the folder to copy the chosen pieces to")
    ap.add_argument("--serial", help="adb device serial")
    ap.add_argument("--adb", default=shutil.which("adb") or "adb", help="path to adb")
    ap.add_argument("--build-tools", help="folder holding apksigner and aapt2")
    ap.add_argument("--sdk", type=int, help="the device's Android API level instead of asking adb (30 = Android 11)")
    ap.add_argument("--abi", help="the device's processors, best first, e.g. armeabi-v7a")
    ap.add_argument("--density", type=int, help="the device's screen density in dpi")
    ap.add_argument("--lang", help="languages to keep, e.g. it,en")
    ap.add_argument("--allow-unverified", action="store_true",
                    help="install: turn adb install verification off for this install only, then restore it")
    args = ap.parse_args(argv)
    try:
        aapt2 = find_tool("aapt2", args.build_tools)
        apksigner = find_tool("apksigner", args.build_tools)
        with tempfile.TemporaryDirectory() as work:
            pieces = load(args.bundles, work, aapt2, apksigner)
            device = read_device(args)
            plan = choose(pieces, device)
            describe(plan, pieces, device)
            if args.command == "extract":
                if not args.out:
                    raise Stop("extract needs --out DIR")
                os.makedirs(args.out, exist_ok=True)
                for p in plan:
                    shutil.copy2(p.path, os.path.join(args.out, ("base" if p.is_base else p.split) + ".apk"))
                print(f"copied {len(plan)} pieces to {args.out}")
            elif args.command == "install":
                install(args, plan)
    except Stop as e:
        print(f"stopped: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
