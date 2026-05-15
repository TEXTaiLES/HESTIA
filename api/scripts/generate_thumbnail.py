#!/usr/bin/env python3
"""
Standalone thumbnail generator for GLB files.
Adapted from render_single.py to generate a single thumbnail view from a 3D model.

Usage:
    python generate_thumbnail.py <path_to_glb_file> <output_path> [view_index]

Example:
    python generate_thumbnail.py model.glb thumbnail.png 7
"""
import os
import sys

os.environ['PYOPENGL_PLATFORM'] = 'egl'
os.environ['PYGLET_HEADLESS'] = '1'
os.environ['DISPLAY'] = ''

import numpy as np
import trimesh
from PIL import Image
import pyrender

# Configuration
DEFAULT_VIEW_INDEX = 7 # Front-ish view
RESOLUTION = 800


def generate_camera_positions(radius=3):
    """
    Generate 24 camera positions in 4 rings of 6 views each.
    ModelNet10 canonical orientation:
      - Z-axis = UP/DOWN (top/bottom)
      - -Y-axis = FRONT
      - X-axis = SIDES

    Elevation rings (from Z-axis):
    - Ring 1 (high upper): elevation +60°
    - Ring 2 (mid upper): elevation +30°
    - Ring 3 (low upper): elevation +10°
    - Ring 4 (lower): elevation -10°
    Each ring has 6 azimuth angles: 0°, 60°, 120°, 180°, 240°, 300°
    """
    positions = []

    # 4 elevation rings (in degrees, converted to radians)
    elevations = [60, 30, 10, -10]

    # 6 azimuth angles per ring (in degrees)
    num_azimuths = 6

    for elevation_deg in elevations:
        elevation_rad = np.deg2rad(elevation_deg)

        for k in range(num_azimuths):
            azimuth_rad = 2 * np.pi * k / num_azimuths

            # Convert spherical to Cartesian (Z-up)
            # Elevation is measured from XY-plane toward +Z
            # Azimuth rotates around Z-axis, starting from +X toward +Y
            x = radius * np.cos(elevation_rad) * np.cos(azimuth_rad)
            y = radius * np.cos(elevation_rad) * np.sin(azimuth_rad)
            z = radius * np.sin(elevation_rad)
            positions.append([x, y, z])

    return positions


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Usage: python generate_thumbnail.py <glb_path> <output_path> [view_index]")
        sys.exit(1)

    glb_path = sys.argv[1]
    output_path = sys.argv[2]
    view_index = int(sys.argv[3]) if len(sys.argv) > 3 else DEFAULT_VIEW_INDEX

    if not os.path.exists(glb_path):
        print(f"File not found: {glb_path}", file=sys.stderr)
        sys.exit(1)

    # Load mesh with trimesh
    mesh_data = trimesh.load(glb_path)

    # Handle both single mesh and scene
    if isinstance(mesh_data, trimesh.Scene):
        meshes = [g for g in mesh_data.geometry.values() if isinstance(g, trimesh.Trimesh)]
        if not meshes:
            print("No valid meshes in GLB", file=sys.stderr)
            sys.exit(1)
        mesh_data = trimesh.util.concatenate(meshes)
    
    # Center the mesh at origin
    mesh_data.apply_translation(-mesh_data.centroid)

    # Normalize to fit in a 2-unit sphere
    max_extent = np.max(mesh_data.extents)
    if max_extent > 0:
        mesh_data.apply_scale(2.0 / max_extent)

    # Generate camera positions
    camera_positions = generate_camera_positions()

    # Validate view index
    if view_index < 0 or view_index >= len(camera_positions):
        view_index = DEFAULT_VIEW_INDEX

    # Create pyrender scene
    mesh_pr = pyrender.Mesh.from_trimesh(mesh_data, smooth=False)

    # Create scene
    scene = pyrender.Scene(ambient_light=[0.3, 0.3, 0.3], bg_color=[255, 255, 255])
    scene.add(mesh_pr)

    # Add lights
    light = pyrender.DirectionalLight(color=[1.0, 1.0, 1.0], intensity=3.0)
    scene.add(light, pose=np.array([
        [1, 0, 0, 0],
        [0, 1, 0, 0],
        [0, 0, 1, 3],
        [0, 0, 0, 1]
    ]))

    # Camera setup
    camera = pyrender.PerspectiveCamera(yfov=np.pi / 3.0)
    
    # Create camera transformation matrix to look at origin
    cam_pos = np.array(camera_positions[view_index])
    target = np.array([0.0, 0.0, 0.0])
    up = np.array([0.0, 0.0, 1.0])

    # Compute camera axes
    z_axis = cam_pos - target
    z_axis /= np.linalg.norm(z_axis)
    x_axis = np.cross(up, z_axis)
    x_axis /= np.linalg.norm(x_axis)
    y_axis = np.cross(z_axis, x_axis)

    # Build camera pose matrix
    camera_pose = np.eye(4)
    camera_pose[:3, 0] = x_axis
    camera_pose[:3, 1] = y_axis
    camera_pose[:3, 2] = z_axis
    camera_pose[:3, 3] = cam_pos

    scene.add(camera, pose=camera_pose)

    # Render
    r = pyrender.OffscreenRenderer(RESOLUTION, RESOLUTION)
    color, _ = r.render(scene)
    r.delete()

    output_dir = os.path.dirname(output_path)
    if output_dir:
        os.makedirs(output_dir, exist_ok=True)

    # Convert to PIL image and save
    Image.fromarray(color).save(output_path)
    sys.exit(0)
