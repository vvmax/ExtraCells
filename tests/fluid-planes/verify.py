#!/usr/bin/env python3
"""Verify both Extra Cells release artifacts on official Crucible 5.4."""
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
from zipfile import ZipFile, ZIP_DEFLATED


HERE = Path(__file__).resolve().parent
AUDIT_REV = "cfc1d010c1646a2f9c2da5ea580509bfbef5e79f"
STOCK_SHA = "0ec8cd1ede7f7bbbf73030eba8b06ebcc0583045ff4cc9aec080b1736581da71"
REMAPPER_SHA = "42ad9fb7d7ac95101a6203fe10c84d7e21fe3ea06fa05b145c4e21b34c9ccaee"
FLUIDS = ("nutrient_distillation", "ic2uumatter")
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
    subprocess.run([args.java8 / "javac", "-source", "8", "-target", "8",
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


def build_fixture(args, classpath):
    source_dir = args.audit / "tests/functional"
    runner = (source_dir / "FunctionalRegression.java").read_text()
    runner = replace_once(runner, "FluidPlaneCases.preInit();",
                          "FluidPlaneCases.preInit();FluidPlaneEdgeCases.preInit();")
    runner = replace_once(runner,
                          'if(Boolean.getBoolean("ae2.fluidplane.tests"))FluidPlaneCases.register(this);',
                          'if(System.getProperty("ec.fluidplane.edge")!=null)FluidPlaneEdgeCases.register(this);\n'
                          '                else if(Boolean.getBoolean("ae2.fluidplane.tests"))FluidPlaneCases.register(this);')
    runner = replace_once(runner, "fixture=new Fixture(world,this);", """
                fixture=new Fixture(world,this);
                ModContainer extraCells = Loader.instance().getIndexedModList().get("extracells");
                check(extraCells != null && System.getProperty("ec.version").equals(extraCells.getVersion()),
                      "Unexpected Extra Cells runtime version " + (extraCells == null ? "missing" : extraCells.getVersion()));
                """)
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
    return remap(dev, "fixture", args, classpath)


def test_run(r, args, world, switches, fixture, production, helper):
    original = {p.name: sha(p) for p in [*production, helper]}
    with ZipFile(fixture) as fixture_jar:
        test_classes = {name for name in fixture_jar.namelist() if name.endswith(".class")}
    for path in [*production, helper]:
        with ZipFile(path) as jar:
            assert test_classes.isdisjoint(jar.namelist()), "Production class shadowed by fixture"
    out = world / "functional-results.json"
    out.unlink(missing_ok=True)
    r.native.run_server([args.java8 / "java", "-Xms256M", "-Xmx1G", *switches,
                         "-Djava.awt.headless=true", "-Djline.terminal=jline.UnsupportedTerminal",
                         "-jar", "Crucible-1.7.10-5.4.jar", "nogui"], world,
                        sorted(os.sched_getaffinity(0))[:4])
    installed = world / "mods/1.7.10"
    for path in [*production, helper]:
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


def init_world(r, args, name, fixture, production, helper):
    world = r.LAB / name
    r.world_setup(world, args.crucible, production[0], fixture.name)
    for jar in [*production[1:], helper]:
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
        facts = data["facts"][key]
        duplicate = baseline and key.endswith("-emitter-100mb") and facts["recovered_mb"] > 1000
        assert (key in by_id) == duplicate
        if duplicate:
            assert "duplicated or lost fluid" in by_id[key]["error"], by_id[key]
        else:
            assert facts["recovered_mb"] == 1000, facts
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
    for name in ("audit", "work-dir", "crucible", "java8", "mcp-srg", "special-source",
                 "mod-dir", "fixture-mods", "selective-jar"):
        parser.add_argument("--" + name, type=lambda value: Path(value).resolve(), required=True)
    parser.add_argument("--stock-eventhelper", type=lambda value: Path(value).resolve(),
                        help="EventHelper 1.6 development archive from the FakePlayers project")
    parser.add_argument("--classpath", required=True, help="MCP Forge 1.7.10 JAR and compile dependencies")
    parser.add_argument("--version", choices=["1.0.0", "1.0.1"], required=True)
    parser.add_argument("--edge-only", action="store_true",
                        help="Run the edge and restart suites against the fixed release")
    args = parser.parse_args()
    if args.edge_only and args.version != "1.0.1":
        parser.error("--edge-only requires a fixed release")
    args.work_dir.mkdir(parents=True, exist_ok=True)
    assert sha(args.special_source) == REMAPPER_SHA
    revision = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=args.audit, text=True).strip()
    assert revision == AUDIT_REV
    subprocess.run(["git", "diff", "--exit-code", "HEAD", "--", "server", "tests", "VERSION", "tools"],
                   cwd=args.audit, check=True)
    stock = args.audit / "server/lib/appliedenergistics2-rv3-beta-6.jar"
    assert sha(stock) == STOCK_SHA
    with ZipFile(args.selective_jar) as jar:
        assert b"AE2-Pa4ok-Version: 1.0.2" in jar.read("META-INF/MANIFEST.MF")

    path = args.audit / "tools/test-crucible-regressions.py"
    spec = importlib.util.spec_from_file_location("regressions", path)
    r = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(r)
    r.LAB = args.work_dir / "worlds"
    r.LAB.mkdir(exist_ok=True)
    assert sha(args.crucible / "Crucible-1.7.10-5.4.jar") == r.native.KERNEL_SHA
    assert sha(args.crucible / "libraries.zip") == r.native.LIBRARIES_SHA

    pins = args.audit / "tests/addons/fluid-plane-dependencies.json"
    mods = []
    for pin in json.loads(pins.read_text())["files"]:
        path = args.fixture_mods / pin["file"]
        assert sha(path) == pin["sha256"], path
        mods.append(path)
    dev = sorted((args.audit / "server/lib").glob("*dev.jar"))
    classpath = os.pathsep.join([args.classpath, *map(str, dev)])
    helper_11 = remap(args.audit / "server/lib/EventHelper-1.7.10-1.11-dev.jar",
                      "EventHelper-1.11", args, classpath)
    helper_6 = (remap(args.stock_eventhelper, "EventHelper-1.6", args, classpath)
                if args.stock_eventhelper else helper_11)
    # world_setup expects the legacy placeholder; force each world to use its selected real JAR.
    (r.LAB / "eventhelper-dev.jar").write_bytes(helper_11.read_bytes())
    fixture = build_fixture(args, classpath)
    shutil.copy2(fixture, r.LAB / fixture.name)

    provenance = dict(audit_commit=AUDIT_REV, runner_sha256=sha(Path(__file__)),
                      edge_source_sha256=sha(HERE / "FluidPlaneEdgeCases.java"),
                      version=args.version, fixture_mod_lock_sha256=sha(pins))
    def run_variant(target):
        label, ae2, helper, side = target
        ec = args.mod_dir / f"ExtraCells-1.7.10-{args.version}-{side}.jar"
        production = [ae2, ec, *mods]
        name = f"{label}-{side}"
        version = "-Dec.version=" + args.version
        if not args.edge_only:
            world = init_world(r, args, name, fixture, production, helper)
            (world / "mods/1.7.10/EventHelper.jar").unlink()
            report = test_run(r, args, world, [version, "-Dae2.fluidplane.tests=true",
                              "-Dae2.fluidplane.fluids=" + ",".join(FLUIDS)], fixture, production, helper)
            report["provenance"] = provenance
            write_json(args.work_dir / (name + ".json"), report)
            validate_basic(report, args.version == "1.0.0")
            amounts = [item["recovered_mb"] for item in report["result"]["facts"].values()]
            print(f"{args.version} {name} verified {amounts}", flush=True)
        if args.version == "1.0.0":
            return
        name += "-edge"
        world = init_world(r, args, name, fixture, production, helper)
        (world / "mods/1.7.10/EventHelper.jar").unlink()
        reports = []
        for stage in ("fresh", "reload"):
            report = test_run(r, args, world, [version, "-Dec.fluidplane.edge=" + stage],
                              fixture, production, helper)
            shutil.copy2(world / "server.log", world / (stage + "-server.log"))
            reports.append(dict(report, stage=stage))
            write_json(args.work_dir / (name + ".json"), dict(provenance=provenance, stages=reports))
            validate_edge(report, stage == "reload")
            print(f"{args.version} {name} {stage} verified", flush=True)

    variants = [(label, ae2, helper, side) for label, ae2, helper in
                [("rv3-beta-6", stock, helper_6), ("selective-1.0.2", args.selective_jar, helper_11)]
                for side in ("server", "client")]
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(run_variant, variants))


if __name__ == "__main__":
    main()
