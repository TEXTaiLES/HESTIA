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
