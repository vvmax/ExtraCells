# Build Extra Cells for Minecraft 1.7.10

The version in `VERSION` identifies this release line. The `server` source overlay comes from the supplied FakePlayers archive and the `client` overlay comes from the supplied Client archive. Unchanged Extra Cells classes and resources come from the b200 archive referenced in both source projects. The build compiles each overlay against its supplied development libraries, remaps the result to SRG and packages it with the pinned b200 runtime. It changes Forge's version metadata to the release version. The included manifests record the source archive and runtime binary checksums.

The FakePlayers server build depends on the server's EventHelper installation. The Crucible fixture uses EventHelper 1.11. The client build has its own rendering and pattern changes and uses the client overlay.

## Inputs

Use JDK 8, Scala 2.11 (compiler, library and reflect), Python 3.9+, and the SpecialSource 1.11.5 shaded JAR with a current Java. Prepare deobfuscated Forge 1.7.10-10.13.4.1614 and MCP stable 12 mappings. Extract the supplied outer archives into `$INPUTS/server` and `$INPUTS/client`, and extract each nested `sources.7z` into a `sources` subdirectory there. The packager reads the supplied JARs and `sources/lib` relative to those paths and validates their checksums from [inputs.json](inputs.json).

```sh
python3 release-tools/build.py \
  --inputs "$INPUTS" --output "./build/releases/$(cat VERSION)" \
  --java8 "$JAVA8/bin" --mcp-srg "$MCP_SRG" --special-source "$SPECIAL_SOURCE" \
  --minecraft-classpath "$MC1710_MCP_CLASSPATH" --scala-classpath "$SCALA_211_CLASSPATH"
```

The result contains separate client and server JARs with a build provenance manifest. Install only the JAR intended for the side you are updating.

## Verification on Crucible

Use the official Crucible 1.7.10-5.4 JAR and unpack its official library ZIP to `libraries/` in the kernel directory. Check out the [AE2 audit](https://github.com/vvmax/AppliedEnergistics2/tree/cfc1d010c1646a2f9c2da5ea580509bfbef5e79f), supply the audit's three pinned fluid fixture mods, and provide a Selective 1.0.2 server JAR. The runner loads both release artifacts in real Crucible JVMs. It checks Forge's runtime version, archives and the liquid balance through the real cell, world and plane buffer. The same fixture runs against both AE2 variants.

```sh
python3 tests/fluid-planes/verify.py \
  --audit "$AUDIT" --work-dir "./build/checks/$(cat VERSION)" \
  --mod-dir "./build/releases/$(cat VERSION)" --version "$(cat VERSION)" \
  --crucible "$CRUCIBLE_54" --java8 "$JAVA8/bin" \
  --mcp-srg "$MCP_SRG" --special-source "$SPECIAL_SOURCE" \
  --fixture-mods "$FLUID_FIXTURE_MODS" --selective-jar "$SELECTIVE_102" \
  --classpath "$MC1710_MCP_CLASSPATH"
```

The supplied EventHelper 1.6 development archive is a compile input. When run on the clean Crucible fixture, 1.6 requires additional WorldGuard setup and throws in `WGReflection.setWG` during startup. The verified runs use EventHelper 1.11 with both AE2 builds.
