import logging
import os
import subprocess # without letting trimesh crash the main Flask process
import sys

logger = logging.getLogger(__name__)

def convert_obj_to_glb(input_path: str, output_path: str) -> bool:
    """
    Converts an OBJ file to a Draco-compressed GLB file.
    Runs in a subprocess to isolate crashes from the Flask process.
    """
    converter_script = os.path.join(os.path.dirname(__file__), '_obj_converter.py')

    # Write the converter script inline if it doesn't exist
    if not os.path.exists(converter_script):
        script = """
import sys
import trimesh

input_path, output_path = sys.argv[1], sys.argv[2]
try:
    scene = trimesh.load(input_path, process=True)
    if isinstance(scene, trimesh.Trimesh):
        import trimesh
        scene = trimesh.Scene(scene)
    scene.export(output_path, file_type='glb', extension_draco=True)
    if __import__('os').path.getsize(output_path) > 0:
        sys.exit(0)
    sys.exit(1)
except Exception as e:
    print(f"Conversion error: {e}", file=sys.stderr)
    sys.exit(1)
"""
        with open(converter_script, 'w') as f:
            f.write(script)

    try:
        logger.info(f"Converting {input_path} to GLB (subprocess)...")
        result = subprocess.run(
            [sys.executable, converter_script, input_path, output_path],
            timeout=120,
            capture_output=True,
            text=True
        )
        if result.returncode == 0 and os.path.exists(output_path) and os.path.getsize(output_path) > 0:
            logger.info(f"GLB saved to {output_path} ({os.path.getsize(output_path)} bytes)")
            return True
        else:
            logger.error(f"GLB conversion failed: {result.stderr}")
            return False
    except subprocess.TimeoutExpired:
        logger.error("GLB conversion timed out after 120s")
        return False
    except Exception as e:
        logger.error(f"Failed to convert OBJ to GLB: {e}")
        return False