package functional;

import appeng.api.AEApi;
import appeng.api.config.Actionable;
import appeng.api.networking.security.BaseActionSource;
import appeng.api.networking.storage.IStorageGrid;
import appeng.api.parts.IPart;
import appeng.api.parts.PartItemStack;
import appeng.api.storage.IMEInventory;
import appeng.api.storage.IMEMonitor;
import appeng.api.storage.StorageChannel;
import appeng.api.storage.data.IAEFluidStack;
import appeng.tile.networking.TileCableBus;
import cpw.mods.fml.common.registry.GameRegistry;
import net.minecraft.block.Block;
import net.minecraft.block.material.Material;
import net.minecraft.init.Blocks;
import net.minecraft.item.ItemStack;
import net.minecraft.nbt.NBTTagCompound;
import net.minecraft.world.ChunkCoordIntPair;
import net.minecraft.world.World;
import net.minecraft.world.WorldServer;
import net.minecraftforge.common.ForgeChunkManager;
import net.minecraftforge.common.util.ForgeDirection;
import net.minecraftforge.fluids.BlockFluidClassic;
import net.minecraftforge.fluids.Fluid;
import net.minecraftforge.fluids.FluidRegistry;
import net.minecraftforge.fluids.FluidStack;
import net.minecraftforge.fluids.IFluidBlock;

import java.util.HashMap;
import java.util.LinkedHashMap;
import java.util.Map;

import static functional.FunctionalRegression.check;

final class FluidPlaneEdgeCases {
    private static final BaseActionSource ACTION = new BaseActionSource();
    private static final String PENDING = "pendingFluid";
    private static final int RESTART_BANK = 26;
    private static DrainFixture testBlock;

    static void preInit() {
        if (System.getProperty("ec.fluidplane.edge") == null)
            return;
        Fluid fluid = new Fluid("ec_drain_fixture");
        check(FluidRegistry.registerFluid(fluid), "Fixture fluid name collision");
        testBlock = new DrainFixture(fluid);
        GameRegistry.registerBlock(testBlock, "ec_drain_fixture");
    }

    static void register(FunctionalRegression suite) {
        if ("reload".equals(System.getProperty("ec.fluidplane.edge"))) {
            add(suite, "restart", new Bank(suite, RESTART_BANK, true, false), (bank, facts, tick) -> {
                if (!bank.ready())
                    return false;
                bank.expect(facts, "from_disk", bank.capacity(), 0, 600);
                finishBuffered(suite, bank, facts, tick);
                return true;
            });
            return;
        }

        add(suite, "shared-source", new Bank(suite, 20, false, true), (bank, facts, tick) -> {
            if (!bank.ready())
                return false;
            DrainState source = bank.fixtureSource();
            bank.expect(facts, "initial", 0, 1000, 0);
            bank.signal();
            check(source.calls == 1 && source.nestedNotifications == 3,
                    "Source was drained more than once or nested updates were missing");
            for (int i = 0; i < 5; ++i)
                bank.signal();
            facts.put("actual_drain_calls", source.calls);
            facts.put("nested_notifications", source.nestedNotifications);
            bank.expect(facts, "after_notifications", 1000, 0, 0);
            bank.extract(1000);
            return true;
        });

        add(suite, "full-cell", new Bank(suite, 21, false, false), (bank, facts, tick) -> {
            if (!bank.ready())
                return false;
            long capacity = bank.capacity();
            bank.insert(capacity);
            bank.realSource();
            for (int i = 0; i < 3; ++i)
                bank.signal();
            bank.expect(facts, "full", capacity, 1000, 0);
            bank.extract(1000);
            bank.signal();
            bank.expect(facts, "after_freeing_space", capacity, 0, 0);
            bank.extract(capacity);
            return true;
        });

        add(suite, "partial-cell", new Bank(suite, 22, false, false), (bank, facts, tick) -> {
            if (!bank.ready())
                return false;
            long initial = bank.capacity() - 400;
            bank.insert(initial);
            bank.realSource();
            bank.signal();
            bank.expect(facts, "partially_accepted", bank.capacity(), 0, 600);
            assertDropData(bank, 600);
            for (int i = 0; i < 3; ++i)
                bank.signal();
            bank.expect(facts, "repeated_updates", bank.capacity(), 0, 600);
            finishBuffered(suite, bank, facts, tick);
            return true;
        });

        add(suite, "refused-and-short-drain", new Bank(suite, 23, false, false), (bank, facts, tick) -> {
            if (!bank.ready())
                return false;
            DrainState source = bank.fixtureSource();
            source.drainLimit = 0;
            bank.signal();
            bank.expect(facts, "after_refusal", 0, 1000, 0);
            source.drainLimit = 375;
            bank.signal();
            bank.expect(facts, "short_actual_drain", 375, 625, 0);
            source.drainLimit = 1000;
            bank.signal();
            bank.expect(facts, "final", 1000, 0, 0);
            bank.extract(1000);
            check(source.calls == 3, "Unexpected number of actual drain attempts");
            return true;
        });

        add(suite, "late-refusal", new Bank(suite, 24, false, false), (bank, facts, tick) -> {
            if (!bank.ready())
                return false;
            DrainState source = bank.fixtureSource();
            long capacity = bank.capacity();
            source.beforeDrain = () -> bank.insert(capacity);
            bank.signal();
            bank.expect(facts, "real_insert_refused", capacity, 0, 1000);
            bank.extract(capacity);
            bank.signal();
            bank.expect(facts, "retried", 1000, 0, 0);
            bank.extract(1000);
            return true;
        });

        add(suite, "guard-after-exception", new Bank(suite, 25, false, true), (bank, facts, tick) -> {
            if (!bank.ready())
                return false;
            DrainState source = bank.fixtureSource();
            source.failSimulation = true;
            bank.plane().onNeighborChanged();
            source.failSimulation = false;
            check(source.calls == 0, "Failed simulation attempted an actual drain");
            bank.expect(facts, "after_simulation_exception", 0, 1000, 0);

            source.beforeDrain = () -> { throw new IllegalStateException("fixture drain failure"); };
            try {
                bank.plane().onNeighborChanged();
                throw new AssertionError("Expected actual drain exception");
            } catch (IllegalStateException expected) {
                check("fixture drain failure".equals(expected.getMessage()), "Wrong exception");
            }
            bank.expect(facts, "after_exception", 0, 1000, 0);
            bank.secondPlane().onNeighborChanged();
            bank.expect(facts, "other_plane_after_exception", 1000, 0, 0);
            bank.extract(1000);
            check(source.calls == 2, "Guard did not allow one subsequent drain");
            return true;
        });

        add(suite, "save-for-restart", new Bank(suite, RESTART_BANK, false, false), (bank, facts, tick) -> {
            if (!bank.ready())
                return false;
            bank.insert(bank.capacity() - 400);
            bank.realSource();
            bank.signal();
            bank.expect(facts, "before_shutdown", bank.capacity(), 0, 600);
            return true;
        });

        add(suite, "chunk-reload", new Bank(suite, 27, false, false), (bank, facts, tick) -> {
            int x = bank.x >> 4;
            int z = bank.z >> 4;
            if (!facts.containsKey("before_unload")) {
                if (!bank.ready())
                    return false;
                bank.insert(bank.capacity() - 400);
                bank.realSource();
                bank.signal();
                bank.expect(facts, "before_unload", bank.capacity(), 0, 600);
                bank.previous = bank.plane();
                ChunkCoordIntPair chunk = new ChunkCoordIntPair(x, z);
                for (ForgeChunkManager.Ticket ticket : suite.fixture.tickets)
                    ForgeChunkManager.unforceChunk(ticket, chunk);
                suite.fixture.chunks.remove(chunk);
                bank.world.theChunkProviderServer.unloadChunksIfNotNearSpawn(x, z);
                return false;
            }
            if (!facts.containsKey("unloaded")) {
                if (bank.world.theChunkProviderServer.chunkExists(x, z))
                    return false;
                // The chunk has been unregistered, but the Anvil write can still be queued.
                bank.world.getSaveHandler().flush();
                facts.put("unloaded", true);
                suite.fixture.force(bank.x, bank.z);
                return false;
            }
            if (!facts.containsKey("reloaded")) {
                if (bank.world.getTileEntity(bank.x, bank.y, bank.z) == null
                        || bank.world.getTileEntity(bank.x, bank.y, bank.z - 1) == null) {
                    suite.detail = "Waiting for the persisted controller and drive after chunk reload";
                    return false;
                }
                bank.physical = suite.fixture.restore(bank.index);
                bank.firstActive = -1;
                check(bank.plane() != bank.previous, "Original plane survived the chunk unload");
                facts.put("reloaded", true);
                return false;
            }
            if (!bank.ready())
                return false;
            bank.expect(facts, "after_reload", bank.capacity(), 0, 600);
            finishBuffered(suite, bank, facts, tick);
            return true;
        });
    }

    private static void assertDropData(Bank bank, int amount) {
        for (PartItemStack type : PartItemStack.values()) {
            ItemStack item = bank.plane().getItemStack(type);
            FluidStack carried = item.hasTagCompound() ? pending(item.getTagCompound()) : null;
            if (type == PartItemStack.Break || type == PartItemStack.Wrench)
                check(carried != null && carried.amount == amount, "Physical drop lost its fluid");
            else
                check(carried == null, "Non-drop part stack copied buffered fluid: " + type);
        }
    }

    private static void finishBuffered(FunctionalRegression suite, Bank bank, Map<String, Object> facts,
                                       int[] tick) throws Exception {
        if (tick[0] == 0) {
            long extracted = bank.extract(bank.capacity());
            facts.put("first_extracted_mb", extracted);
            tick[0] = suite.tick;
        }
        // Subsequent attempts are performed by the real grid tick manager.
    }

    private interface Test {
        boolean run(Bank bank, Map<String, Object> facts, int[] tick) throws Exception;
    }

    private static void add(FunctionalRegression suite, String name, Bank bank, Test test) {
        String id = "extracells." + name;
        Map<String, Object> facts = new LinkedHashMap<>();
        suite.facts.put(id, facts);
        int[] tick = {0};
        suite.add(id, name, () -> bank.install(), () -> {
            if (tick[0] != 0) {
                if (suite.tick - tick[0] < 40)
                    return false;
                bank.expect(facts, "after_tick_retry", 600, 0, 0);
                long recovered = bank.extract(600);
                facts.put("final_extracted_mb", ((Number) facts.get("first_extracted_mb")).longValue() + recovered);
                bank.expect(facts, "after_extraction", 0, 0, 0);
                return true;
            }
            return test.run(bank, facts, tick) && tick[0] == 0;
        });
    }

    private static FluidStack pending(NBTTagCompound tag) {
        return FluidStack.loadFluidStackFromNBT(tag.getCompoundTag(PENDING));
    }

    private static final class Bank {
        final Fixture fixture;
        final FunctionalRegression suite;
        final WorldServer world;
        final int index;
        final boolean restore;
        final boolean multiPlane;
        final int x, y = 96, z = 256;
        Fixture.Bank physical;
        Fluid fluid;
        int firstActive = -1;
        IPart previous;

        Bank(FunctionalRegression suite, int index, boolean restore, boolean multiPlane) {
            this.fixture = suite.fixture;
            this.world = fixture.world;
            this.suite = suite;
            this.index = index;
            this.restore = restore;
            this.multiPlane = multiPlane;
            this.x = 256 + index * 64;
            this.fluid = FluidRegistry.getFluid("nutrient_distillation");
            check(fluid != null, "Real Ender IO fluid missing");
        }

        void install() throws Exception {
            physical = restore ? fixture.restore(index) : fixture.bank(index);
            if (restore)
                return;
            for (int[] offset : new int[][] {{3, 0, 1}, {5, 0, 1}, {4, -1, 1}, {4, 1, 1}, {4, 0, 2}})
                fixture.block(x + offset[0], y + offset[1], z + offset[2], Blocks.stone);
            fixture.part(physical.host(4), FluidPlaneCases.part("FLUIDPANEANNIHILATION"), ForgeDirection.SOUTH);
            if (multiPlane) {
                fixture.part(fixture.cable(x + 3, y, z + 1, false),
                        FluidPlaneCases.part("FLUIDPANEANNIHILATION"), ForgeDirection.EAST);
            }
        }

        IPart plane() {
            return physical.host(4).getCableBus().getPart(ForgeDirection.SOUTH);
        }

        IPart secondPlane() {
            return ((TileCableBus) world.getTileEntity(x + 3, y, z + 1))
                    .getCableBus().getPart(ForgeDirection.EAST);
        }

        boolean ready() throws Exception {
            if (!physical.ready() || !plane().getGridNode().isActive()
                    || (multiPlane && !secondPlane().getGridNode().isActive()))
                return false;
            if (firstActive < 0) {
                if (!restore && previous == null) {
                    ItemStack cell = new ItemStack(FluidPlaneCases.item("FLUIDSTORAGE"), 1, 0);
                    physical.drive.getInternalInventory().setInventorySlotContents(0, cell);
                }
                firstActive = suite.tick;
            }
            return suite.tick - firstActive >= 20
                    && physical.drive.getCellArray(StorageChannel.FLUIDS).size() == 1;
        }

        IMEMonitor<IAEFluidStack> monitor() throws Exception {
            return physical.grid().<IStorageGrid>getCache(IStorageGrid.class).getFluidInventory();
        }

        IMEInventory<IAEFluidStack> cell() {
            ItemStack stack = physical.drive.getInternalInventory().getStackInSlot(0);
            return AEApi.instance().registries().cell().getCellInventory(stack, null, StorageChannel.FLUIDS);
        }

        long capacity() throws Exception {
            return ((Number) cell().getClass().getMethod("totalBytes").invoke(cell())).longValue();
        }

        void insert(long amount) throws Exception {
            check(monitor().injectItems(FluidPlaneCases.stack(fluid, amount), Actionable.MODULATE, ACTION) == null,
                    "Cell refused setup fluid");
        }

        long extract(long amount) throws Exception {
            IAEFluidStack actual = monitor().extractItems(FluidPlaneCases.stack(fluid, amount), Actionable.MODULATE, ACTION);
            check(actual != null && actual.getStackSize() == amount, "Unexpected actual extraction: " + actual);
            return actual.getStackSize();
        }

        void realSource() {
            check(world.setBlock(x + 4, y, z + 1, fluid.getBlock(), 0, 2), "Could not install real fluid source");
        }

        DrainState fixtureSource() {
            fluid = testBlock.getFluid();
            DrainState source = new DrainState();
            testBlock.states.put(x + 4, source);
            check(world.setBlock(x + 4, y, z + 1, testBlock, 0, 2), "Could not install test fluid block");
            return source;
        }

        void signal() {
            world.notifyBlocksOfNeighborChange(x + 4, y, z + 1, world.getBlock(x + 4, y, z + 1));
        }

        void expect(Map<String, Object> facts, String phase, long me, long inWorld, long buffered) throws Exception {
            Block block = world.getBlock(x + 4, y, z + 1);
            long actualMe = FluidPlaneCases.count(monitor(), fluid);
            long actualCell = FluidPlaneCases.count(cell(), fluid);
            FluidStack worldFluid = block instanceof IFluidBlock
                    ? ((IFluidBlock) block).drain(world, x + 4, y, z + 1, false) : null;
            long actualWorld = worldFluid == null ? 0 : worldFluid.amount;
            long actualBuffer = buffer(plane()) + (multiPlane ? buffer(secondPlane()) : 0);
            Map<String, Long> tally = new LinkedHashMap<>();
            tally.put("me_mb", actualMe);
            tally.put("cell_nbt_mb", actualCell);
            tally.put("world_mb", actualWorld);
            tally.put("buffer_mb", actualBuffer);
            tally.put("total_mb", actualMe + actualWorld + actualBuffer);
            facts.put(phase, tally);
            check(actualMe == me && actualCell == me && actualWorld == inWorld && actualBuffer == buffered,
                    phase + " expected ME/world/buffer " + me + "/" + inWorld + "/" + buffered + " but observed " + tally);
        }

        private long buffer(IPart part) {
            NBTTagCompound tag = new NBTTagCompound();
            part.writeToNBT(tag);
            FluidStack fluid = pending(tag);
            return fluid == null ? 0 : fluid.amount;
        }
    }

    private static final class DrainState {
        int remaining = 1000;
        int drainLimit = 1000;
        int calls;
        int nestedNotifications;
        boolean failSimulation;
        FunctionalRegression.Action beforeDrain;
    }

    // This additional, registered Forge fluid block exercises the drain contract.
    private static final class DrainFixture extends BlockFluidClassic {
        final Map<Integer, DrainState> states = new HashMap<>();

        DrainFixture(Fluid fluid) {
            super(fluid, Material.water);
        }

        @Override
        public FluidStack drain(World world, int x, int y, int z, boolean doDrain) {
            DrainState state = states.get(x);
            if (state == null || state.remaining == 0)
                return null;
            if (!doDrain) {
                if (state.failSimulation)
                    throw new NullPointerException("fixture drain simulation failure");
                return new FluidStack(getFluid(), state.remaining);
            }
            ++state.calls;
            if (state.calls == 1) {
                for (int i = 0; i < 3; ++i) {
                    ++state.nestedNotifications;
                    world.notifyBlocksOfNeighborChange(x, y, z, this);
                }
            }
            if (state.beforeDrain != null) {
                FunctionalRegression.Action callback = state.beforeDrain;
                state.beforeDrain = null;
                try {
                    callback.run();
                } catch (RuntimeException exception) {
                    throw exception;
                } catch (Exception exception) {
                    throw new AssertionError(exception);
                }
            }
            int drained = Math.min(state.drainLimit, state.remaining);
            if (drained == 0)
                return null;
            state.remaining -= drained;
            if (state.remaining == 0)
                check(world.setBlockToAir(x, y, z), "Fixture source failed to clear");
            return new FluidStack(getFluid(), drained);
        }
    }
}
