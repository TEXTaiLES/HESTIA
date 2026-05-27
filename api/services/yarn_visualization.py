"""
Yarn simulation visualization: merge multiple OBJ frames (sharing identical
topology) into a single glTF-Binary (.glb) file with morph-target animation.

Each input OBJ is treated as one frame of a deformation animation:
- Frame 0 is the base mesh.
- Frames 1..N-1 are stored as morph targets (delta positions vs. base).
- One animation channel drives the morph weights via LINEAR keyframes so
  model-viewer plays it natively.

Output: bytes of a valid GLB ready to write to disk / upload to MinIO / serve.
"""

from __future__ import annotations

import json
import logging
import struct
from dataclasses import dataclass

import numpy as np

logger = logging.getLogger(__name__)

# glTF component types
GL_UNSIGNED_INT = 5125
GL_FLOAT = 5126

# glTF buffer-view targets
ARRAY_BUFFER = 34962         # vertex attributes
ELEMENT_ARRAY_BUFFER = 34963  # indices

# OBJ files coming out of the simulator can contain n-gons. For glTF we
# triangulate using fan triangulation (works for convex polygons, which
# yarn-mesh quads/n-gons are).


@dataclass
class ObjFrame:
    """Per-frame OBJ data, parsed but not yet unified into glTF vertex layout."""
    positions: np.ndarray   # (V, 3) float32 — `v` lines
    normals: np.ndarray     # (N, 3) float32 — `vn` lines (may be empty)
    # face_vertex_keys[i] is a tuple (pos_idx0, tex_idx_or_-1, nrm_idx_or_-1) for the
    # i-th face-corner of frame 0. Stored only on frame 0 since topology is shared.
    face_vertex_keys: list | None = None
    # triangulated_indices: flat list of corner-indices (into face_vertex_keys)
    # forming triangles. Same for every frame.
    triangulated_indices: list | None = None


def _parse_obj(text: str, capture_topology: bool) -> ObjFrame:
    """
    Parse an OBJ text. Returns positions, normals, and (only if capture_topology)
    the face-corner data needed to build the glTF index buffer.

    Face syntax handled: `v`, `v/vt`, `v//vn`, `v/vt/vn`. 1-based indexes.
    n-gons are fan-triangulated.
    """
    positions: list[tuple[float, float, float]] = []
    normals: list[tuple[float, float, float]] = []
    face_vertex_keys: list[tuple[int, int, int]] = []
    triangulated_indices: list[int] = []

    # Local handle to avoid attribute lookups in the hot loop.
    p_append = positions.append
    n_append = normals.append

    for raw in text.splitlines():
        if not raw:
            continue
        c0 = raw[0]
        if c0 == '#' or c0 == 'm' or c0 == 'u' or c0 == 'o' or c0 == 'g' or c0 == 's':
            continue
        if c0 == 'v':
            parts = raw.split()
            tag = parts[0]
            if tag == 'v':
                # v x y z
                p_append((float(parts[1]), float(parts[2]), float(parts[3])))
            elif tag == 'vn':
                # vn x y z
                n_append((float(parts[1]), float(parts[2]), float(parts[3])))
            # ignore `vt` for now — we don't need texcoords for the morph viewer
            continue
        if c0 == 'f' and capture_topology:
            parts = raw.split()
            corners = parts[1:]
            # Each corner is "v", "v/vt", "v//vn", or "v/vt/vn". 1-based.
            keys = []
            for corner in corners:
                bits = corner.split('/')
                p = int(bits[0]) - 1
                t = int(bits[1]) - 1 if len(bits) >= 2 and bits[1] else -1
                n = int(bits[2]) - 1 if len(bits) >= 3 and bits[2] else -1
                keys.append((p, t, n))

            base = len(face_vertex_keys)
            face_vertex_keys.extend(keys)
            # Fan-triangulate: (0, i-1, i) for i in 2..len-1
            for i in range(2, len(keys)):
                triangulated_indices.append(base)
                triangulated_indices.append(base + i - 1)
                triangulated_indices.append(base + i)

    pos = np.asarray(positions, dtype=np.float32)
    nrm = np.asarray(normals, dtype=np.float32) if normals else np.empty((0, 3), dtype=np.float32)

    if capture_topology:
        return ObjFrame(positions=pos, normals=nrm,
                        face_vertex_keys=face_vertex_keys,
                        triangulated_indices=triangulated_indices)
    return ObjFrame(positions=pos, normals=nrm)


def _validate_topology(frames: list[ObjFrame]) -> None:
    base = frames[0]
    for i, f in enumerate(frames[1:], start=1):
        if f.positions.shape != base.positions.shape:
            raise ValueError(
                f"Frame {i} has {f.positions.shape[0]} vertices but base has "
                f"{base.positions.shape[0]} — topology must be identical for morph-target animation."
            )
        if f.normals.shape != base.normals.shape:
            raise ValueError(
                f"Frame {i} has {f.normals.shape[0]} normals but base has "
                f"{base.normals.shape[0]}."
            )


def _build_unified_vertices(frame0_keys: list[tuple[int, int, int]],
                            triangulated_indices: list[int]):
    """
    glTF requires each vertex to be a single tuple (POSITION, NORMAL, ...).
    OBJ allows different indexes per attribute per face-corner. We dedupe
    unique (pos_idx, normal_idx) tuples (texcoord ignored) into glTF vertices,
    and rewrite the triangle indices accordingly.

    Returns:
      unified_pos_idx (V'): for each glTF vertex, which OBJ position to read.
      unified_nrm_idx (V'): for each glTF vertex, which OBJ normal to read (-1 if none).
      gltf_indices (T): triangulated index buffer rewritten to point at glTF vertices.
    """
    unique: dict[tuple[int, int], int] = {}
    pos_lookup: list[int] = []
    nrm_lookup: list[int] = []

    # triangulated_indices: [(pos1,nrm8), (pos2,nrm9), (pos3,nrm10), (pos1,nrm8), (pos3,nrm10), (pos4,nrm11)]
    # gltf_indices: [0, 1, 2, 0, 2, 3]
    gltf_indices = np.empty(len(triangulated_indices), dtype=np.uint32)
    for out_i, corner_i in enumerate(triangulated_indices):
        p, _t, n = frame0_keys[corner_i]
        key = (p, n)
        idx = unique.get(key)
        if idx is None:
            idx = len(pos_lookup)
            unique[key] = idx
            pos_lookup.append(p)
            nrm_lookup.append(n)
        gltf_indices[out_i] = idx

    return np.asarray(pos_lookup, dtype=np.int64), np.asarray(nrm_lookup, dtype=np.int64), gltf_indices


def _pad4(buf: bytearray, pad_byte: int = 0) -> None:
    """glTF requires 4-byte alignment for each chunk."""
    n = (-len(buf)) & 3
    if n:
        buf.extend([pad_byte] * n)


def build_morph_target_glb(obj_texts: list[str]) -> bytes:
    """
    Build a single GLB file with morph-target animation from N OBJ frames.

    Parameters
    ----------
    obj_texts : list of str
        Raw text of each OBJ file, in playback order (frame 0 first).

    Returns
    -------
    bytes
        Binary glTF (.glb) content.

    Raises
    ------
    ValueError
        If frames have different topology, or fewer than 2 frames supplied.
    """
    if len(obj_texts) < 2:
        raise ValueError("Need at least 2 frames to build a morph animation.")

    logger.info(f"[yarn-viz] parsing {len(obj_texts)} OBJ frames")

    # Parse frame 0 with full topology capture; other frames just need positions/normals.
    frames: list[ObjFrame] = []
    for i, text in enumerate(obj_texts):
        frames.append(_parse_obj(text, capture_topology=(i == 0)))
        logger.info(f"[yarn-viz] frame {i}: {len(frames[-1].positions)} verts, "
                    f"{len(frames[-1].normals)} normals")

    _validate_topology(frames)

    # Dedupe unique (position, normal) pairs from frame 0 → glTF vertex layout.
    pos_lookup, nrm_lookup, indices = _build_unified_vertices(
        frames[0].face_vertex_keys, frames[0].triangulated_indices
    )
    V = len(pos_lookup)
    has_normals = frames[0].normals.shape[0] > 0 and (nrm_lookup >= 0).all()

    logger.info(f"[yarn-viz] glTF vertex count: {V}, triangles: {len(indices)//3}, "
                f"normals: {'yes' if has_normals else 'no'}")

    # Build per-frame unified position arrays.
    def lift_positions(frame: ObjFrame) -> np.ndarray:
        return frame.positions[pos_lookup]

    def lift_normals(frame: ObjFrame) -> np.ndarray:
        return frame.normals[nrm_lookup]

    base_positions = lift_positions(frames[0])
    base_normals = lift_normals(frames[0]) if has_normals else None

    morph_position_deltas = [
        (lift_positions(f) - base_positions).astype(np.float32)
        for f in frames[1:]
    ]
    morph_normal_deltas = (
        [(lift_normals(f) - base_normals).astype(np.float32) for f in frames[1:]]
        if has_normals else None
    )

    # ----- Build binary buffer + accessors for glTF -----
    bin_buf = bytearray()
    buffer_views = []
    accessors = []

    def add_buffer_view(data: bytes, target: int | None) -> int:
        # 4-byte align before appending each view (glTF spec).
        _pad4(bin_buf)
        offset = len(bin_buf)
        bin_buf.extend(data)
        view = {
            'buffer': 0,
            'byteOffset': offset,
            'byteLength': len(data),
        }
        if target is not None:
            view['target'] = target
        buffer_views.append(view)
        return len(buffer_views) - 1

    def add_accessor(view_idx: int, component_type: int, count: int,
                     type_: str, with_minmax: np.ndarray | None = None) -> int:
        acc = {
            'bufferView': view_idx,
            'componentType': component_type,
            'count': count,
            'type': type_,
        }
        if with_minmax is not None:
            acc['min'] = with_minmax.min(axis=0).tolist()
            acc['max'] = with_minmax.max(axis=0).tolist()
        accessors.append(acc)
        return len(accessors) - 1

    # POSITION (base)
    pos_view = add_buffer_view(base_positions.tobytes(), ARRAY_BUFFER)
    pos_accessor = add_accessor(pos_view, GL_FLOAT, V, 'VEC3', with_minmax=base_positions)

    # NORMAL (base) — optional
    nrm_accessor = None
    if has_normals:
        nrm_view = add_buffer_view(base_normals.tobytes(), ARRAY_BUFFER)
        nrm_accessor = add_accessor(nrm_view, GL_FLOAT, V, 'VEC3')

    # Indices
    idx_view = add_buffer_view(indices.astype(np.uint32).tobytes(), ELEMENT_ARRAY_BUFFER)
    idx_accessor = add_accessor(idx_view, GL_UNSIGNED_INT, len(indices), 'SCALAR')

    # Morph targets: one POSITION delta accessor per frame >= 1 (and NORMAL delta if applicable).
    morph_targets = []
    for i, delta in enumerate(morph_position_deltas):
        view = add_buffer_view(delta.tobytes(), ARRAY_BUFFER)
        acc = add_accessor(view, GL_FLOAT, V, 'VEC3', with_minmax=delta)
        target = {'POSITION': acc}
        if morph_normal_deltas is not None:
            nview = add_buffer_view(morph_normal_deltas[i].tobytes(), ARRAY_BUFFER)
            nacc = add_accessor(nview, GL_FLOAT, V, 'VEC3')
            target['NORMAL'] = nacc
        morph_targets.append(target)

    morph_count = len(morph_targets)

    # Animation: one keyframe per frame. At keyframe k, weights[k-1] = 1, others = 0.
    # (keyframe 0 = base, weights all zero. LINEAR interpolation between keyframes
    # gives smooth deformation playback.)
    seconds_per_frame = 1.0
    timeline = np.arange(len(obj_texts), dtype=np.float32) * seconds_per_frame
    # Output weights matrix: (num_keyframes, morph_count). One-hot rows shifted by 1.
    weights = np.zeros((len(obj_texts), morph_count), dtype=np.float32)
    for k in range(1, len(obj_texts)):
        weights[k, k - 1] = 1.0

    time_view = add_buffer_view(timeline.tobytes(), None)
    time_accessor = add_accessor(time_view, GL_FLOAT, len(timeline), 'SCALAR',
                                 with_minmax=timeline.reshape(-1, 1))

    weights_flat = weights.reshape(-1)  # glTF wants samples flat
    weights_view = add_buffer_view(weights_flat.tobytes(), None)
    weights_accessor = add_accessor(weights_view, GL_FLOAT, len(weights_flat), 'SCALAR')

    # ----- Assemble glTF JSON -----
    primitive = {
        'attributes': {'POSITION': pos_accessor},
        'indices': idx_accessor,
        'targets': morph_targets,
    }
    if has_normals:
        primitive['attributes']['NORMAL'] = nrm_accessor

    gltf = {
        'asset': {'version': '2.0', 'generator': 'HESTIA yarn-visualization'},
        'scene': 0,
        'scenes': [{'nodes': [0]}],
        'nodes': [{'mesh': 0}],
        'meshes': [{
            'primitives': [primitive],
            'weights': [0.0] * morph_count,
        }],
        'animations': [{
            'name': 'yarn-deformation',
            'channels': [{
                'sampler': 0,
                'target': {'node': 0, 'path': 'weights'}
            }],
            'samplers': [{
                'input': time_accessor,
                'output': weights_accessor,
                'interpolation': 'LINEAR',
            }],
        }],
        'buffers': [{'byteLength': len(bin_buf)}],
        'bufferViews': buffer_views,
        'accessors': accessors,
    }

    # ----- Pack into GLB binary container -----
    json_bytes = json.dumps(gltf, separators=(',', ':')).encode('utf-8')
    json_pad = (-len(json_bytes)) & 3
    json_bytes += b' ' * json_pad   # JSON chunk padded with spaces (spec)

    bin_pad = (-len(bin_buf)) & 3
    bin_bytes = bytes(bin_buf) + (b'\x00' * bin_pad)

    total_len = 12 + 8 + len(json_bytes) + 8 + len(bin_bytes)

    out = bytearray()
    # Header
    out += struct.pack('<III', 0x46546C67, 2, total_len)   # 'glTF', version 2
    # JSON chunk
    out += struct.pack('<II', len(json_bytes), 0x4E4F534A)  # length, 'JSON'
    out += json_bytes
    # BIN chunk
    out += struct.pack('<II', len(bin_bytes), 0x004E4942)   # length, 'BIN\0'
    out += bin_bytes

    logger.info(f"[yarn-viz] built GLB: {total_len} bytes ({len(morph_targets)} morph targets)")
    return bytes(out)