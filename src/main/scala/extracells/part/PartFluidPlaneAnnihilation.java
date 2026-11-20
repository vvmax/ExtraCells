package extracells.part;

import appeng.api.config.Actionable;
import appeng.api.config.SecurityPermissions;
import appeng.api.networking.IGridNode;
import appeng.api.networking.events.MENetworkChannelChanged;
import appeng.api.networking.events.MENetworkEventSubscribe;
import appeng.api.networking.events.MENetworkPowerStatusChange;
import appeng.api.networking.security.MachineSource;
import appeng.api.networking.ticking.IGridTickable;
import appeng.api.networking.ticking.TickRateModulation;
import appeng.api.networking.ticking.TickingRequest;
import appeng.api.parts.IPart;
import appeng.api.parts.IPartCollisionHelper;
import appeng.api.parts.IPartHost;
import appeng.api.parts.IPartRenderHelper;
import appeng.api.parts.PartItemStack;
import appeng.api.storage.IMEMonitor;
import appeng.api.storage.data.IAEFluidStack;
import appeng.api.util.AEColor;
import appeng.util.Platform;
import com.google.common.collect.HashMultimap;
import com.google.common.collect.Multimaps;
import com.google.common.collect.SetMultimap;
import cpw.mods.fml.relauncher.Side;
import cpw.mods.fml.relauncher.SideOnly;
import extracells.gridblock.ECBaseGridBlock;
import extracells.render.TextureManager;
import extracells.util.FluidUtil;
import extracells.util.PermissionUtil;
import net.minecraft.block.Block;
import net.minecraft.client.renderer.RenderBlocks;
import net.minecraft.client.renderer.Tessellator;
import net.minecraft.entity.player.EntityPlayer;
import net.minecraft.init.Blocks;
import net.minecraft.item.ItemStack;
import net.minecraft.nbt.NBTTagCompound;
import net.minecraft.tileentity.TileEntity;
import net.minecraft.util.IIcon;
import net.minecraft.util.Vec3;
import net.minecraft.world.ChunkPosition;
import net.minecraft.world.World;
import net.minecraftforge.common.util.ForgeDirection;
import net.minecraftforge.fluids.FluidContainerRegistry;
import net.minecraftforge.fluids.FluidRegistry;
import net.minecraftforge.fluids.FluidStack;
import net.minecraftforge.fluids.IFluidBlock;

import java.util.Collections;

public class PartFluidPlaneAnnihilation extends PartECBase implements IGridTickable {

	private static final String PENDING_FLUID = "pendingFluid";
	private static final SetMultimap<World, ChunkPosition> ACTIVE_SOURCES =
			Multimaps.synchronizedSetMultimap(HashMultimap.<World, ChunkPosition>create());

	private FluidStack pendingFluid;

	@Override
	public int cableConnectionRenderTo() {
		return 2;
	}

	@SuppressWarnings("unused")
	@MENetworkEventSubscribe
	public void channelChanged(MENetworkChannelChanged e) {
		if (e.node == getGridNode())
			onNeighborChanged();
	}

	@Override
	public void getBoxes(IPartCollisionHelper bch) {
		bch.addBox(2, 2, 14, 14, 14, 16);
		bch.addBox(5, 5, 13, 11, 11, 14);
	}

	@Override
	public int getLightLevel() {
		return 0;
	}

	@Override
	public double getPowerUsage() {
		return 1.0D;
	}

	@Override
	public boolean onActivate(EntityPlayer player, Vec3 pos) {
		if (PermissionUtil.hasPermission(player, SecurityPermissions.BUILD,
				(IPart) this)) {
			return super.onActivate(player, pos);
		}
		return false;
	}

	@Override
	public void onNeighborChanged() {
		transferFluid(true);
	}

	private void transferFluid(boolean drainSource) {
		TileEntity hostTile = getHostTile();
		ECBaseGridBlock gridBlock = getGridBlock();
		if (hostTile == null || gridBlock == null)
			return;
		World world = hostTile.getWorldObj();
		ForgeDirection side = getSide();
		if (world == null || world.isRemote || side == ForgeDirection.UNKNOWN)
			return;
		IMEMonitor<IAEFluidStack> monitor = gridBlock.getFluidMonitor();
		if (monitor == null)
			return;

		ChunkPosition source = new ChunkPosition(hostTile.xCoord + side.offsetX,
				hostTile.yCoord + side.offsetY, hostTile.zCoord + side.offsetZ);
		// World changes and ME notifications can call any plane facing this source.
		if (!ACTIVE_SOURCES.put(world, source))
			return;
		try {
			MachineSource actionSource = new MachineSource(this);
			if (pendingFluid == null && drainSource
					&& world.blockExists(source.chunkPosX, source.chunkPosY, source.chunkPosZ))
				acquireFluid(world, source, monitor, actionSource);
			if (pendingFluid != null)
				insertPendingFluid(monitor, actionSource);
		} finally {
			ACTIVE_SOURCES.remove(world, source);
		}
	}

	private void acquireFluid(World world, ChunkPosition source, IMEMonitor<IAEFluidStack> monitor, MachineSource actionSource) {
		int x = source.chunkPosX;
		int y = source.chunkPosY;
		int z = source.chunkPosZ;
		Block block = world.getBlock(x, y, z);
		IFluidBlock forgeBlock = block instanceof IFluidBlock ? (IFluidBlock) block : null;
		FluidStack simulated;
		if (forgeBlock != null) {
			simulated = forgeBlock.drain(world, x, y, z, false);
		} else if (world.getBlockMetadata(x, y, z) == 0 && block == Blocks.flowing_water) {
			simulated = new FluidStack(FluidRegistry.WATER, FluidContainerRegistry.BUCKET_VOLUME);
		} else if (world.getBlockMetadata(x, y, z) == 0 && block == Blocks.flowing_lava) {
			simulated = new FluidStack(FluidRegistry.LAVA, FluidContainerRegistry.BUCKET_VOLUME);
		} else {
			return;
		}
		if (simulated == null || simulated.amount <= 0)
			return;

		IAEFluidStack rejected = monitor.injectItems(FluidUtil.createAEFluidStack(simulated.copy()),
				Actionable.SIMULATE, actionSource);
		if (rejected != null && rejected.getStackSize() >= simulated.amount)
			return;

		FluidStack drained;
		if (forgeBlock != null) {
			drained = forgeBlock.drain(world, x, y, z, true);
		} else {
			drained = world.setBlockToAir(x, y, z) ? simulated : null;
		}
		if (drained != null && drained.amount > 0) {
			pendingFluid = drained.copy();
			saveData();
		}
	}

	private void insertPendingFluid(IMEMonitor<IAEFluidStack> monitor, MachineSource actionSource) {
		IAEFluidStack rejected = monitor.injectItems(FluidUtil.createAEFluidStack(pendingFluid.copy()),
				Actionable.MODULATE, actionSource);
		int remaining = rejected == null ? 0 : (int) rejected.getStackSize();
		if (remaining != pendingFluid.amount) {
			if (remaining <= 0)
				pendingFluid = null;
			else
				pendingFluid.amount = remaining;
			saveData();
		}
	}

	@Override
	public TickingRequest getTickingRequest(IGridNode node) {
		return new TickingRequest(5, 20, false, false);
	}

	@Override
	public TickRateModulation tickingRequest(IGridNode node, int ticksSinceLastCall) {
		if (pendingFluid != null)
			transferFluid(false);
		return TickRateModulation.SLOWER;
	}

	@Override
	public void readFromNBT(NBTTagCompound data) {
		pendingFluid = FluidStack.loadFluidStackFromNBT(data.getCompoundTag(PENDING_FLUID));
		super.readFromNBT(data);
	}

	@Override
	public void writeToNBT(NBTTagCompound data) {
		super.writeToNBT(data);
		if (pendingFluid != null)
			data.setTag(PENDING_FLUID, pendingFluid.writeToNBT(new NBTTagCompound()));
		else
			data.removeTag(PENDING_FLUID);
	}

	@Override
	public ItemStack getItemStack(PartItemStack type) {
		ItemStack item = super.getItemStack(type);
		// Only the physical drop can carry fluid. World NBT has separate part data.
		if (type == PartItemStack.Break && pendingFluid != null) {
			item.setTagInfo(PENDING_FLUID, pendingFluid.writeToNBT(new NBTTagCompound()));
		} else if (type != PartItemStack.Wrench && item.hasTagCompound()) {
			item.getTagCompound().removeTag(PENDING_FLUID);
		}
		return item;
	}

	@Override
	public void securityBreak() {
		TileEntity hostTile = getHostTile();
		ItemStack drop = pendingFluid == null ? null : getItemStack(PartItemStack.Break);
		super.securityBreak();
		if (drop != null)
			Platform.spawnDrops(hostTile.getWorldObj(), hostTile.xCoord, hostTile.yCoord, hostTile.zCoord,
					Collections.singletonList(drop));
	}

	@SideOnly(Side.CLIENT)
	@Override
	public void renderInventory(IPartRenderHelper rh, RenderBlocks renderer) {
		IIcon side = TextureManager.PANE_SIDE.getTexture();
		rh.setTexture(side, side, side, TextureManager.BUS_BORDER.getTexture(),
				side, side);
		rh.setBounds(2, 2, 14, 14, 14, 16);
		rh.renderInventoryBox(renderer);
		rh.setBounds(3, 3, 14, 13, 13, 16);
		rh.setInvColor(AEColor.Cyan.blackVariant);
		rh.renderInventoryFace(TextureManager.PANE_FRONT.getTextures()[0],
				ForgeDirection.SOUTH, renderer);
		Tessellator.instance.setBrightness(13 << 20 | 13 << 4);
		rh.setInvColor(AEColor.Cyan.mediumVariant);
		rh.renderInventoryFace(TextureManager.PANE_FRONT.getTextures()[1],
				ForgeDirection.SOUTH, renderer);
		rh.setInvColor(AEColor.Cyan.whiteVariant);
		rh.renderInventoryFace(TextureManager.PANE_FRONT.getTextures()[2],
				ForgeDirection.SOUTH, renderer);

		rh.setBounds(5, 5, 13, 11, 11, 14);
		renderInventoryBusLights(rh, renderer);
	}

	@SideOnly(Side.CLIENT)
	@Override
	public void renderStatic(int x, int y, int z, IPartRenderHelper rh,
			RenderBlocks renderer) {
		Tessellator ts = Tessellator.instance;
		IIcon side = TextureManager.PANE_SIDE.getTexture();
		rh.setTexture(side, side, side, TextureManager.BUS_BORDER.getTexture(),
				side, side);
		rh.setBounds(2, 2, 14, 14, 14, 16);
		rh.renderBlock(x, y, z, renderer);
		rh.setBounds(3, 3, 14, 13, 13, 16);
		IPartHost host = getHost();
		if (host != null) {
			ts.setColorOpaque_I(host.getColor().blackVariant);
			rh.renderFace(x, y, z, TextureManager.PANE_FRONT.getTextures()[0],
					ForgeDirection.SOUTH, renderer);
			if (isActive())
				ts.setBrightness(13 << 20 | 13 << 4);
			ts.setColorOpaque_I(host.getColor().mediumVariant);
			rh.renderFace(x, y, z, TextureManager.PANE_FRONT.getTextures()[1],
					ForgeDirection.SOUTH, renderer);
			ts.setColorOpaque_I(host.getColor().whiteVariant);
			rh.renderFace(x, y, z, TextureManager.PANE_FRONT.getTextures()[2],
					ForgeDirection.SOUTH, renderer);
		}

		rh.setBounds(5, 5, 13, 11, 11, 14);
		renderStaticBusLights(x, y, z, rh, renderer);
	}

	@Override
	@SuppressWarnings("unused")
	@MENetworkEventSubscribe
	public void setPower(MENetworkPowerStatusChange notUsed) {
		super.setPower(notUsed);
		onNeighborChanged();
	}
}
