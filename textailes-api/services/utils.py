import logging
import trimesh
import os
import numpy as np

logger = logging.getLogger(__name__)

def convert_obj_to_glb(input_path: str, output_path: str) -> bool:
    """
    Converts an OBJ file to a Draco-compressed GLB file.

    Args:
        input_path (str): Path to the source .obj file.
        output_path (str): Path where the .glb should be saved.

    Returns:
        bool: True if successful, False otherwise.
    """
    try:
        logger.info(f"Loading mesh from {input_path} for Draco conversion...")

        scene = trimesh.load(input_path, process=True)

        if isinstance(scene, trimesh.Trimesh):
            scene = trimesh.Scene(scene)

        logger.info("Exporting to GLB with Draco compression...")

        scene.export(
            output_path,
            file_type='glb',
            extension_draco=True
        )

        # Verification
        if os.path.exists(output_path) and os.path.getsize(output_path) > 0:
            logger.info(f"Compressed GLB saved to {output_path} ({os.path.getsize(output_path)} bytes)")
            return True
        else:
            logger.error("Export failed: Output file is missing or empty.")
            return False

    except ImportError:
        logger.error("DracoPy is missing! Install it to support compression.")
        return False
    except Exception as e:
        logger.error(f"Failed to convert OBJ to GLB: {e}")
        return False