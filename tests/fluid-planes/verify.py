#!/usr/bin/env python3
"""Build the two Extra Cells fixes against b200 and test them on official Crucible 5.4."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
from urllib.request import urlretrieve
from zipfile import ZipFile, ZIP_DEFLATED


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
AUDIT_REV = "cfc1d010c1646a2f9c2da5ea580509bfbef5e79f"
SELECTIVE_REV = "78f6da2e858d9c7ac6f5e6b1b76b7036931bc3f6"
STOCK_SHA = "0ec8cd1ede7f7bbbf73030eba8b06ebcc0583045ff4cc9aec080b1736581da71"
PUBLISHED_AUDIT_SELECTIVE_SHA = "cb2a5efdb1a5b54a0ee19fef84ddf79166475ae8875bb261c584aebd7c81e615"
REMAPPER_SHA = "42ad9fb7d7ac95101a6203fe10c84d7e21fe3ea06fa05b145c4e21b34c9ccaee"
FLUIDS = ("nutrient_distillation", "ic2uumatter")
CHANGED_CLASSES = {"extracells/part/PartFluidPlaneAnnihilation",
                   "extracells/inventory/HandlerItemStorageFluid"}
IC2_ERROR = (
    "The mod IC2 is expecting signature de041f9f6187debbc77034a344134053277aa3b0 "
    "for source industrialcraft-2-2.2.828-experimental.jar, however there is no signature matching that description"
)


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_json(path, data):
    path.write_text(json.dumps(data, indent=2) + "\n")


def replace_once(text, old, new):
    assert text.count(old) == 1, old
    return text.replace(old, new)


def build_jar(name, sources, args, classpath):
    directory = args.work_dir / (name + "-classes")
    if directory.exists():
        shutil.rmtree(directory)
    directory.mkdir()
    target = "1.6" if name == "extra-cells" else "8"
    subprocess.run([args.java8 / "javac", "-source", target, "-target", target,
                    "-encoding", "UTF-8", "-cp", classpath, "-d", directory, *sources], check=True)
    output = args.work_dir / (name + "-dev.jar")
    with ZipFile(output, "w", ZIP_DEFLATED) as jar:
        for path in sorted(directory.rglob("*.class")):
            jar.writestr(path.relative_to(directory).as_posix(), path.read_bytes())
    return output


def remap(dev, name, args, classpath):
    output = args.work_dir / (name + "-srg.jar")
    output.unlink(missing_ok=True)
    subprocess.run(["java", "-cp", os.pathsep.join([str(args.special_source), str(dev), classpath]),
                    "net.md_5.specialsource.SpecialSource", "-q", "-l", "-i", dev,
                    "-o", output, "-m", args.mcp_srg], check=True)
    return output


def pack(base, patch, result, extra_entries=None):
    with ZipFile(base) as original, ZipFile(patch) as compiled, ZipFile(result, "w", ZIP_DEFLATED) as out:
        changed = {name: compiled.read(name) for name in compiled.namelist() if name.endswith(".class")}
        if extra_entries:
            changed.update(extra_entries)
        for name in original.namelist():
            out.writestr(name, changed.pop(name) if name in changed else original.read(name))
        for name, data in sorted(changed.items()):
            out.writestr(name, data)


def build_fixture(args, classpath, selective_dev):
    source_dir = args.audit / "tests/functional"
    runner = (source_dir / "FunctionalRegression.java").read_text()
    runner = replace_once(runner, "FluidPlaneCases.preInit();",
                          "FluidPlaneCases.preInit();FluidPlaneEdgeCases.preInit();")
    runner = replace_once(runner,
                          'if(Boolean.getBoolean("ae2.fluidplane.tests"))FluidPlaneCases.register(this);',
                          'if(System.getProperty("ec.fluidplane.edge")!=null)FluidPlaneEdgeCases.register(this);\n'
                          '                else if(Boolean.getBoolean("ae2.fluidplane.tests"))FluidPlaneCases.register(this);')
    (args.work_dir / "FunctionalRegression.java").write_text(runner)
    cases = (source_dir / "FluidPlaneCases.java").read_text()
    cases = replace_once(cases, 'samples.add(sample);', '''
                    net.minecraft.world.World world = s.fixture.world;
                    net.minecraft.block.Block source = world.getBlock(bank.x+4, bank.y, bank.z+1);
                    FluidStack inWorld = source instanceof IFluidBlock ?
                            ((IFluidBlock) source).drain(world, bank.x+4, bank.y, bank.z+1, false) : null;
                    NBTTagCompound planeTag = new NBTTagCompound();
                    annihilation.writeToNBT(planeTag);
                    FluidStack pending = FluidStack.loadFluidStackFromNBT(planeTag.getCompoundTag("pendingFluid"));
                    int worldAmount = inWorld == null ? 0 : inWorld.amount;
                    int bufferAmount = pending == null ? 0 : pending.amount;
                    sample.put("world_mb", worldAmount); sample.put("buffer_mb", bufferAmount);
                    sample.put("total_mb", amount + worldAmount + bufferAmount);
                    samples.add(sample);
                ''')
    cases = replace_once(cases, 'FunctionalRegression.check(count(actual,fluid)==0,',
                         '''NBTTagCompound finalTag = new NBTTagCompound();
                annihilation.writeToNBT(finalTag);
                FluidStack finalBuffer = FluidStack.loadFluidStackFromNBT(finalTag.getCompoundTag("pendingFluid"));
                facts.put("buffer_remaining_mb", finalBuffer == null ? 0 : finalBuffer.amount);
                facts.put("world_remaining_mb", 0);
                FunctionalRegression.check(finalBuffer == null, "Loop left buffered fluid");
                FunctionalRegression.check(count(actual,fluid)==0,''')
    (args.work_dir / "FluidPlaneCases.java").write_text(cases)
    sources = sorted(set(source_dir.glob("*.java")) -
                     {source_dir / "FunctionalRegression.java", source_dir / "FluidPlaneCases.java"})
    sources.extend([args.work_dir / "FunctionalRegression.java", args.work_dir / "FluidPlaneCases.java",
                    HERE / "FluidPlaneEdgeCases.java"])
    dev = build_jar("fixture", sources, args, classpath)
    return remap(dev, "fixture", args, os.pathsep.join([str(selective_dev), classpath]))


def test_run(r, args, world, switches, fixture, production):
    original = {p.name: sha(p) for p in production}
    with ZipFile(fixture) as fixture_jar:
        test_classes = {name for name in fixture_jar.namelist() if name.endswith(".class")}
    for path in production:
        with ZipFile(path) as jar:
            assert test_classes.isdisjoint(jar.namelist()), "Production class shadowed by fixture"
    out = world / "functional-results.json"
    out.unlink(missing_ok=True)
    r.native.run_server([args.java8 / "java", "-Xms256M", "-Xmx1G", *switches,
                         "-Djava.awt.headless=true", "-Djline.terminal=jline.UnsupportedTerminal",
                         "-jar", "Crucible-1.7.10-5.4.jar", "nogui"], world,
                        sorted(os.sched_getaffinity(0))[:4])
    installed = world / "mods/1.7.10"
    for path in production:
        target = installed / ("ae2.jar" if path == production[0] else path.name)
        assert sha(path) == original[path.name] == sha(target), target
    log_path = world / "server.log"
    log = log_path.read_text(errors="replace")
    errors = [line for line in log.splitlines() if re.search(r"\b(?:ERROR|FATAL)\]", line)]
    unexpected = [line for line in errors if not line.endswith(IC2_ERROR)]
    return dict(result=json.loads(out.read_text()), server_log_sha256=sha(log_path),
                fixture_sha256=sha(fixture), kernel_sha256=r.native.KERNEL_SHA,
                libraries_sha256=r.native.LIBRARIES_SHA, production_sha256=original,
                log_errors=errors, unexpected_log_errors=unexpected,
                inherited_csv_warning="NoSuchMethodError: net.minecraft.item.Item.func_77640_w()" in log)


def init_world(r, args, name, fixture, production):
    world = r.LAB / name
    r.world_setup(world, args.crucible, production[0], fixture.name)
    for jar in production[1:]:
        shutil.copy2(jar, world / "mods/1.7.10" / jar.name)
    for name in ["banned-players.json", "banned-ips.json", "ops.json", "whitelist.json"]:
        (world / name).write_text("[]\n")
    return world


def validate_basic(report, baseline):
    data = report["result"]
    by_id = {row["id"]: row for row in data["errors"]}
    expected = {"extracells.fluid-planes-" + fluid + "-" + mode
                for fluid in FLUIDS for mode in ("emitter-100mb", "without-emitter")}
    passed = {row["id"] for row in data["passed"]}
    assert passed.isdisjoint(by_id) and passed | set(by_id) == expected
    assert len(data["passed"]) + len(data["errors"]) == len(expected)
    for key in expected:
        duplicate = baseline and key.endswith("-emitter-100mb")
        facts = data["facts"][key]
        assert (key in by_id) == duplicate
        assert facts["recovered_mb"] == (13000 if duplicate else 1000), facts
        assert facts["cell_remaining_mb"] == facts["buffer_remaining_mb"] == facts["world_remaining_mb"] == 0
        assert [point["ticks"] for point in facts["samples"]] == list(range(20, 121, 20))
        if not duplicate:
            assert all(point["total_mb"] == 1000 and point["network_mb"] == point["cell_mb"]
                       for point in facts["samples"]), facts
    assert not report["unexpected_log_errors"], report["unexpected_log_errors"]


def validate_edge(report, reload):
    data = report["result"]
    expected = {"restart"} if reload else {
        "shared-source", "full-cell", "partial-cell", "refused-and-short-drain",
        "late-refusal", "guard-after-exception", "save-for-restart", "chunk-reload",
    }
    assert not report["unexpected_log_errors"], report["unexpected_log_errors"]
    assert not data["errors"], data["errors"]
    assert {row["id"] for row in data["passed"]} == {"extracells." + k for k in expected}
    assert len(data["passed"]) == len(expected)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("audit", "work-dir", "crucible", "java8", "mcp-srg", "special-source"):
        parser.add_argument("--" + name, type=lambda value: Path(value).resolve(), required=True)
    parser.add_argument("--classpath", required=True, help="MCP Forge 1.7.10 JAR and its compile dependencies")
    parser.add_argument("--selective-jar", type=lambda value: Path(value).resolve(),
                        help="Exact Selective 1.0.2 audit binary, if available. Otherwise build from audited source.")
    args = parser.parse_args()
    args.work_dir.mkdir(parents=True, exist_ok=True)
    assert sha(args.special_source) == REMAPPER_SHA
    revision = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=args.audit, text=True).strip()
    assert revision == AUDIT_REV
    subprocess.run(["git", "diff", "--exit-code", "HEAD", "--", "server", "tests", "VERSION", "tools"],
                   cwd=args.audit, check=True)
    assert (args.audit / "VERSION").read_text().strip() == "1.0.2"
    stock = args.audit / "server/lib/appliedenergistics2-rv3-beta-6.jar"
    assert sha(stock) == STOCK_SHA

    path = args.audit / "tools/test-crucible-regressions.py"
    spec = importlib.util.spec_from_file_location("regressions", path)
    r = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(r)
    r.LAB = args.work_dir / "worlds"
    r.LAB.mkdir(exist_ok=True)
    assert sha(args.crucible / "Crucible-1.7.10-5.4.jar") == r.native.KERNEL_SHA
    assert sha(args.crucible / "libraries.zip") == r.native.LIBRARIES_SHA

    lock = args.audit / "tests/addons"
    pins = json.loads((lock / "dependencies.lock.json").read_text())["files"][:1]
    pins += json.loads((lock / "fluid-plane-dependencies.json").read_text())["files"]
    mods = []
    for pin in pins:
        path = args.work_dir / pin["file"]
        if not path.exists():
            temporary = path.with_suffix(".partial")
            urlretrieve(pin["url"], temporary)
            assert sha(temporary) == pin["sha256"], temporary
            temporary.replace(path)
        assert sha(path) == pin["sha256"], path
        mods.append(path)
    base, *mods = mods
    libraries = sorted((args.audit / "server/lib").glob("*dev.jar"))
    classpath = os.pathsep.join([args.classpath, *map(str, libraries), str(base)])

    sources = [ROOT / "src/main/scala" / (name + ".java") for name in sorted(CHANGED_CLASSES)]
    fixed_classes = remap(build_jar("extra-cells", sources, args, classpath),
                          "extra-cells", args, classpath)
    fixed = args.work_dir / "ExtraCells-1.7.10-2.3.14b200-fluid-plane-fix.jar"
    with ZipFile(fixed_classes) as patch:
        assert {entry for entry in patch.namelist() if entry.endswith(".class")} == {
            name + ".class" for name in CHANGED_CLASSES}
    pack(base, fixed_classes, fixed)
    with ZipFile(base) as original, ZipFile(fixed) as rebuilt:
        assert set(original.namelist()) == set(rebuilt.namelist())
        assert {name for name in original.namelist() if original.read(name) != rebuilt.read(name)} == {
            name + ".class" for name in CHANGED_CLASSES}
    print(f"Extra Cells build {fixed}  {sha(fixed)}", flush=True)

    selective_dev = build_jar("selective", sorted((args.audit / "server/src/main/java").rglob("*.java")),
                              args, classpath)
    if args.selective_jar:
        selective = args.selective_jar
        assert sha(selective) == PUBLISHED_AUDIT_SELECTIVE_SHA
    else:
        selective = args.work_dir / "AppliedEnergistics2-1.7.10-Pa4ok-Selective-1.0.2-server.jar"
        selective_srg = remap(selective_dev, "selective", args, classpath)
        manifest = ("Manifest-Version: 1.0\r\nImplementation-Version: 1.0.2\r\n"
                    "AE2-Pa4ok-Version: 1.0.2\r\nAE2-Pa4ok-Release-Line: selective\r\n"
                    "FMLCorePlugin: appeng.transformer.AppEngCore\r\nFMLCorePluginContainsFMLMod: true\r\n\r\n")
        pack(stock, selective_srg, selective, {"META-INF/MANIFEST.MF": manifest.encode()})
    helper = remap(args.audit / "server/lib/EventHelper-1.7.10-1.11-dev.jar", "eventhelper", args, classpath)
    shutil.copy2(helper, r.LAB / "eventhelper-dev.jar")
    fixture = build_fixture(args, classpath, selective_dev)
    shutil.copy2(fixture, r.LAB / fixture.name)

    provenance = dict(audit_commit=AUDIT_REV, selective_source_commit=SELECTIVE_REV,
                      selective_rebuilt=args.selective_jar is None, runner_sha256=sha(Path(__file__)),
                      edge_source_sha256=sha(HERE / "FluidPlaneEdgeCases.java"),
                      mcp_srg_sha256=sha(args.mcp_srg), patched_source_sha256={p.name: sha(p) for p in sources})
    def run_variant(pair):
        label, ae2 = pair
        for tag, ec in (("before", base), ("after", fixed)):
            name = f"{label}-{tag}"
            production = [ae2, ec, *mods]
            world = init_world(r, args, name, fixture, production)
            report = test_run(r, args, world, ["-Dae2.fluidplane.tests=true",
                              "-Dae2.fluidplane.fluids=" + ",".join(FLUIDS)], fixture, production)
            report["provenance"] = provenance
            write_json(args.work_dir / (name + ".json"), report)
            validate_basic(report, tag == "before")
            print(f"{name} verified", flush=True)
        production = [ae2, fixed, *mods]
        name = f"{label}-edge"
        world = init_world(r, args, name, fixture, production)
        reports = []
        for stage in ("fresh", "reload"):
            report = test_run(r, args, world, ["-Dec.fluidplane.edge=" + stage], fixture, production)
            backup = world / (stage + "-server.log")
            shutil.copy2(world / "server.log", backup)
            reports.append(dict(report, stage=stage))
            write_json(args.work_dir / (name + ".json"), dict(provenance=provenance, stages=reports))
            validate_edge(report, stage == "reload")
            print(f"{name} {stage} verified", flush=True)

    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(run_variant, [("rv3-beta-6", stock), ("selective-1.0.2", selective)]))


if __name__ == "__main__":
    main()
