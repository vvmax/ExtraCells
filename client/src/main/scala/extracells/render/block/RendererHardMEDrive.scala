package extracells.render.block

import cpw.mods.fml.client.registry.{ISimpleBlockRenderingHandler, RenderingRegistry}
import net.minecraft.block.Block
import net.minecraft.client.renderer.{RenderBlocks, Tessellator}
import net.minecraft.world.IBlockAccess
import org.lwjgl.opengl.GL11

object RendererHardMEDrive extends ISimpleBlockRenderingHandler {
  private var renderID = 0
  private val InventoryFacing = 3

  def registerRenderer(): Unit = {
    renderID = RenderingRegistry.getNextAvailableRenderId
    RenderingRegistry.registerBlockHandler(this)
  }

  override def getRenderId: Int = renderID

  override def shouldRender3DInInventory(modelId: Int): Boolean = true

  override def renderWorldBlock(world: IBlockAccess, x: Int, y: Int, z: Int, block: Block,
                                modelId: Int, renderer: RenderBlocks): Boolean = {
    // The chunk renderer owns the active tessellator batch and the block atlas.
    renderer.renderStandardBlock(block, x, y, z)
  }

  override def renderInventoryBlock(block: Block, metadata: Int, modelId: Int, renderer: RenderBlocks): Unit = {
    val tessellator = Tessellator.instance
    renderer.setRenderBounds(0.0D, 0.0D, 0.0D, 1.0D, 1.0D, 1.0D)

    GL11.glPushMatrix()
    try {
      GL11.glTranslatef(-0.5F, -0.5F, -0.5F)
      tessellator.startDrawingQuads()

      tessellator.setNormal(0.0F, -1.0F, 0.0F)
      renderer.renderFaceYNeg(block, 0.0D, 0.0D, 0.0D, renderer.getBlockIconFromSideAndMetadata(block, 0, InventoryFacing))
      tessellator.setNormal(0.0F, 1.0F, 0.0F)
      renderer.renderFaceYPos(block, 0.0D, 0.0D, 0.0D, renderer.getBlockIconFromSideAndMetadata(block, 1, InventoryFacing))
      tessellator.setNormal(0.0F, 0.0F, -1.0F)
      renderer.renderFaceZNeg(block, 0.0D, 0.0D, 0.0D, renderer.getBlockIconFromSideAndMetadata(block, 2, InventoryFacing))
      tessellator.setNormal(0.0F, 0.0F, 1.0F)
      renderer.renderFaceZPos(block, 0.0D, 0.0D, 0.0D, renderer.getBlockIconFromSideAndMetadata(block, 3, InventoryFacing))
      tessellator.setNormal(-1.0F, 0.0F, 0.0F)
      renderer.renderFaceXNeg(block, 0.0D, 0.0D, 0.0D, renderer.getBlockIconFromSideAndMetadata(block, 4, InventoryFacing))
      tessellator.setNormal(1.0F, 0.0F, 0.0F)
      renderer.renderFaceXPos(block, 0.0D, 0.0D, 0.0D, renderer.getBlockIconFromSideAndMetadata(block, 5, InventoryFacing))

      tessellator.draw()
    } finally {
      GL11.glPopMatrix()
    }
  }
}
