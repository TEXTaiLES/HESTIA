"""
Static plot rendering for simulation downloads.

These plots ship inside the download ZIP as report-quality PNGs. They deliberately
look different from the interactive Chart.js version on the artefact page: this is
the printable/embed-in-a-paper version, so we lean on matplotlib defaults with
publication DPI and a clean axis style.

The Agg backend is set before pyplot import so matplotlib runs headless inside
gunicorn/Flask without pulling in any GUI toolkit.
"""
import io
import logging
import math

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

logger = logging.getLogger(__name__)


def render_force_elongation_png(elongations, forces, elongation_unit='%', force_unit='N', dpi=150):
    """Render a Force-Elongation curve as PNG bytes.

    Returns None if the input arrays are missing, empty, or mismatched — callers
    should treat that as "no plot available" and skip embedding it in the ZIP.
    """
    if not elongations or not forces:
        return None
    if len(elongations) != len(forces):
        logger.warning(
            f"Force-Elongation arrays length mismatch: elongations={len(elongations)}, forces={len(forces)}"
        )
        return None

    fig, ax = plt.subplots(figsize=(7, 4.5), dpi=dpi)
    ax.plot(elongations, forces, color='#d62728', linewidth=2, marker='o', markersize=4)
    ax.set_xlabel(f'Elongation ({elongation_unit})')
    ax.set_ylabel(f'Force ({force_unit})')
    ax.set_title('Force-Elongation Diagram')
    ax.grid(True, linestyle='--', alpha=0.4)
    ax.margins(x=0.02)

    buf = io.BytesIO()
    fig.tight_layout()
    fig.savefig(buf, format='png', bbox_inches='tight')
    plt.close(fig)
    return buf.getvalue()


def render_polar_stiffness_png(
    angles, values, angle_unit='deg', value_unit='', title='', color='#1f77b4', dpi=150
):
    """Render a polar plot of stiffness vs. angle as PNG bytes.

    `angles` and `values` are same-length numeric arrays. Angles must be in
    'deg' or 'rad' (matches the plotDataAngles.unit schema field). We close
    the curve by repeating the first sample so the polygon loops cleanly.

    Convention (from PDF page 2 & 6): 0° = X-direction (weft), 90° = Y (warp).
    Matplotlib polar defaults already put 0° on the right and increase CCW,
    so no rotation is needed for that convention.

    Returns None if inputs are missing/empty/mismatched.
    """
    if not angles or not values:
        return None
    if len(angles) != len(values):
        logger.warning(
            f"Polar plot arrays length mismatch: angles={len(angles)}, values={len(values)}"
        )
        return None

    if angle_unit and angle_unit.lower() in ('deg', 'degrees'):
        theta = [math.radians(a) for a in angles]
    else:
        theta = list(angles)

    # Close the loop so the polygon doesn't have a gap.
    theta_closed = theta + [theta[0]]
    values_closed = list(values) + [values[0]]

    fig = plt.figure(figsize=(6, 6), dpi=dpi)
    ax = fig.add_subplot(111, projection='polar')
    ax.plot(theta_closed, values_closed, color=color, linewidth=2)
    ax.fill(theta_closed, values_closed, color=color, alpha=0.12)
    if title:
        ax.set_title(title, pad=20)
    if value_unit:
        # Small annotation for the radial unit; matplotlib doesn't have a
        # native radial-axis label, so we tuck it into the top-left corner.
        fig.text(0.02, 0.98, f'r: {value_unit}', ha='left', va='top', fontsize=9, color='#555')
    ax.grid(True, alpha=0.4)

    buf = io.BytesIO()
    fig.tight_layout()
    fig.savefig(buf, format='png', bbox_inches='tight')
    plt.close(fig)
    return buf.getvalue()
