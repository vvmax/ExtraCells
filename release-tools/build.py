#!/usr/bin/env python3
"""Compile the supplied Extra Cells overlays and package them with the pinned b200 base."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
from zipfile import ZipFile, ZIP_DEFLATED


ROOT = Path(__file__).resolve().parents[1]
INPUTS = Path(__file__).with_name("inputs.json")
BASE = "ExtraCells-1.7.10-2.3.14b200.jar"
REMAPPER_SHA256 = "42ad9fb7d7ac95101a6203fe10c84d7e21fe3ea06fa05b145c4e21b34c9ccaee"
METADATA = {"mcmod.info", "version.properties", "META-INF/MANIFEST.MF"}


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def reobfuscate(input_jar, output_jar, classpath, args):
    subprocess.run([
        "java", "-cp", os.pathsep.join([str(args.special_source), str(input_jar), classpath]),
        "net.md_5.specialsource.SpecialSource", "-q", "-l", "-i", input_jar,
        "-o", output_jar, "-m", args.mcp_srg,
    ], check=True)


def compile_sources(side, class_dir, classpath, args):
    sources = ROOT / side / "src/main"
    common = ROOT / "common/src/main"
    javac_inputs = sorted([*common.rglob("*.java"), *sources.rglob("*.java")])
    scala_inputs = sorted([*common.rglob("*.scala"), *sources.rglob("*.scala")])
    class_dir.mkdir()
    if javac_inputs:
        subprocess.run([
            args.java8 / "javac", "-source", "8", "-target", "8", "-encoding", "UTF-8",
            "-cp", classpath, "-d", class_dir, *javac_inputs,
        ], check=True)
    if scala_inputs:
        subprocess.run([
            args.java8 / "java", "-cp", args.scala_classpath, "scala.tools.nsc.Main",
            "-target:jvm-1.8", "-encoding", "UTF-8",
            "-classpath", os.pathsep.join([str(class_dir), classpath]),
            "-d", class_dir, *scala_inputs,
        ], check=True)


def package(base_path, patch_path, output, version, side):
    with ZipFile(base_path) as base, ZipFile(patch_path) as patch, ZipFile(output, "w", ZIP_DEFLATED) as out:
        replacements = {name: patch.read(name) for name in patch.namelist() if name.endswith(".class")}
        assert replacements and all(name.startswith(("extracells/", "com/gamerforea/extracells/"))
                                    for name in replacements), "Unexpected entries in compiled overlay"
        info = json.loads(base.read("mcmod.info"))
        mod = next(row for row in info if row["modid"] == "extracells")
        mod["version"] = version
        replacements["mcmod.info"] = (json.dumps(info, indent=2) + "\n").encode()
        replacements["version.properties"] = f"extracells.version={version}\n".encode()
        replacements["META-INF/MANIFEST.MF"] = (
            f"Manifest-Version: 1.0\r\nImplementation-Version: {version}\r\n"
            f"ExtraCells-Distribution: {side}\r\nExtraCells-Base: 2.3.14b200\r\n\r\n"
        ).encode()

        touched = set(replacements)
        for entry in base.infolist():
            name = entry.filename
            data = replacements.pop(name) if name in replacements else base.read(entry)
            out.writestr(entry, data)
        for name, data in sorted(replacements.items()):
            out.writestr(name, data)
    with ZipFile(base_path) as base, ZipFile(output) as out:
        assert out.testzip() is None
        assert set(out.namelist()) == set(base.namelist()) | touched
        for name in set(base.namelist()) - touched:
            assert base.read(name) == out.read(name), name
    return sorted(touched - METADATA)


def build(side, lock, version, args):
    libraries = args.inputs / side / "sources/lib"
    dependencies = []
    for filename, digest in lock["lib"].items():
        path = libraries / filename
        assert sha(path) == digest, f"Dependency checksum mismatch: {path}"
        if filename != BASE:
            dependencies.append(str(path))

    classpath = os.pathsep.join([args.minecraft_classpath, *dependencies])
    with tempfile.TemporaryDirectory(prefix=side + "-", dir=args.output) as tmp:
        tmp = Path(tmp)
        classes = tmp / "classes"
        compile_sources(side, classes, classpath, args)
        dev = tmp / "overlay-dev.jar"
        with ZipFile(dev, "w", ZIP_DEFLATED) as jar:
            for path in sorted(classes.rglob("*.class")):
                jar.write(path, path.relative_to(classes))
        patch = tmp / "overlay-srg.jar"
        reobfuscate(dev, patch, classpath, args)
        output = args.output / f"ExtraCells-1.7.10-{version}-{side}.jar"
        overlay = package(libraries / BASE, patch, output, version, side)
        expected = args.inputs / side / BASE
        assert sha(expected) == lock["supplied_jar_sha256"], expected
        with ZipFile(expected) as archived, ZipFile(output) as rebuilt, ZipFile(libraries / BASE) as base:
            assert set(archived.namelist()) - set(rebuilt.namelist()) <= {
                name for name in archived.namelist() if name.endswith("/")}, side
            recorded_changes = {name for name in archived.namelist() if name.endswith(".class")
                                and (name not in base.namelist() or archived.read(name) != base.read(name))}
            assert recorded_changes <= set(overlay), recorded_changes - set(overlay)
            if version == "1.0.0":
                assert set(overlay) <= set(archived.namelist()), set(overlay) - set(archived.namelist())

    print(f"{side} {version}  {sha(output)}  {output.name}", flush=True)
    return side, dict(jar=output.name, sha256=sha(output), overlay_classes=overlay,
                      source_archive_sha256=lock["sources_archive_sha256"],
                      supplied_jar_sha256=lock["supplied_jar_sha256"])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("inputs", "output", "java8", "mcp-srg", "special-source"):
        parser.add_argument("--" + name, type=lambda v: Path(v).resolve(), required=True)
    parser.add_argument("--minecraft-classpath", required=True)
    parser.add_argument("--scala-classpath", required=True, help="Scala 2.11 compiler, library and reflect JARs")
    args = parser.parse_args()
    assert sha(args.special_source) == REMAPPER_SHA256
    version = (ROOT / "VERSION").read_text().strip()
    assert re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", version), version
    args.output.mkdir(parents=True, exist_ok=True)
    lock = json.loads(INPUTS.read_text())
    with ThreadPoolExecutor(2) as pool:
        builds = dict(pool.map(lambda side: build(side, lock[side], version, args), ("client", "server")))
    report = dict(version=version, minecraft="1.7.10", forge="10.13.4.1614", mcp="stable_12",
                  inputs_sha256=sha(INPUTS), mcp_srg_sha256=sha(args.mcp_srg),
                  special_source_sha256=sha(args.special_source), artifacts=builds)
    (args.output / "build-provenance.json").write_text(json.dumps(report, indent=2) + "\n")


if __name__ == "__main__":
    main()
