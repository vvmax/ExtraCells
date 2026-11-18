# Fluid-plane regression on Crucible 1.7.10-5.4

The suite extends the [AE2 audit fixture](https://github.com/vvmax/AppliedEnergistics2/tree/cfc1d010c1646a2f9c2da5ea580509bfbef5e79f). It checks live ME networks, Extra Cells parts and fluid cells with the actual CurseForge Ender IO and IC2 fluids. A separately registered Forge fluid block exercises drain refusal, short drain, repeated callbacks during a drain and an exception before the world changes.

The runner tests official AE2 rv3-beta-6 and the Selective 1.0.2 source revision. Each AE2 variant gets two fresh worlds for the original and fixed Extra Cells, followed by a fresh world for the edge cases and a second JVM that opens that same world. Both Extra Cells binaries are tested with the same AE2 binary and fixture in each comparison. The runner compares the ME balance with fresh cell NBT and adds the measured world and plane buffer amounts. Final extraction uses the real storage operation.

In the partial-capacity case, a cell with 400 mB free takes 400 mB from a 1,000 mB source. The other 600 mB remain in the plane through repeated notifications, chunk unload or a clean server stop. After capacity is freed, a real grid tick delivers those 600 mB. The fixture also verifies the buffer NBT in wrench and break drop stacks and checks that pick-block and network item stacks carry no fluid.

## Run

Use Python 3.9+, JDK 8, a current Java for SpecialSource 1.11.5, deobfuscated Forge 1.7.10-10.13.4.1614 with MCP stable 12 mappings, and the official [Crucible v5.4 release](https://github.com/CrucibleMC/Crucible/releases/tag/v5.4). Unzip the Crucible libraries into the kernel directory's `libraries/` subdirectory. The runner takes the compile classpath for MCP Minecraft and its libraries and adds the AE2 development libraries from the audit checkout.

```sh
git clone --branch audit/selective-extracells-fluid-planes \
  https://github.com/vvmax/AppliedEnergistics2.git ae2-audit
git -C ae2-audit checkout cfc1d010c1646a2f9c2da5ea580509bfbef5e79f
python3 tests/fluid-planes/verify.py \
  --audit ./ae2-audit \
  --work-dir ./build/fluid-plane-verification \
  --crucible /path/to/crucible-5.4 \
  --java8 /path/to/jdk8/bin \
  --mcp-srg /path/to/mcp-srg.srg \
  --special-source /path/to/SpecialSource-1.11.5-shaded.jar \
  --classpath "$MC1710_MCP_CLASSPATH"
```

The runner verifies the official b200 CurseForge archive, compiles the changed Extra Cells sources, remaps the two classes to SRG and packages them into b200. It checks every other uncompressed JAR entry against the official archive. For the 1.0.2 candidate, the default builds the server sources at the audit revision. Those sources match `78f6da2e858d9c7ac6f5e6b1b76b7036931bc3f6`. To reuse the exact binary from the previous audit, pass it with `--selective-jar`.

The working directory receives the fixed JAR, JSON reports and server logs. Before/after reports require all four combinations to run, with the original emitter variants recovering 13,000 mB and every fixed variant recovering 1,000 mB. The runner records the inherited IC2 signature diagnostic and rejects unexpected server errors.
