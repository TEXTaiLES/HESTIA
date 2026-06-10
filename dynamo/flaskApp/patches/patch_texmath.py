"""
Image patch: make TexMathToSTL.interpolateNormals tolerant of few-point inputs.

The shipped function unconditionally uses scipy's interp1d with kind='cubic',
which requires len(tContactNodes) >= 4. Yarn simulations with low
nodesPerPeriodCount (the only configurations where DynaMo's solver actually
converges on this image) produce fewer than 4 contact nodes per thread, so
post-processing crashes with:

    ValueError: The number of derivatives at boundaries does not match:
                expected 1, got 0+0

This patch adds a one-line fallback: if there aren't enough points for cubic,
drop to quadratic (>=3 points) or linear (>=2 points). The interpolation
quality is reduced only on inputs that would otherwise crash outright.

Run as part of the dynamo_service image build. Idempotent — re-running on an
already-patched file is a no-op.
"""
import sys
from pathlib import Path

TARGET = Path('/working_environment/ScriptCollection/InputProjects/TexMathToSTL.py')

OLD = '    return [interp.interp1d(tContactNodes, normals[:, i], kind=kind, fill_value=\'extrapolate\') for i in range(3)]'

NEW = '''    # PATCH (HESTIA): scipy's cubic interp1d needs >=4 points; fall back when fewer.
    if kind == "cubic" and len(tContactNodes) < 4:
        kind = "linear" if len(tContactNodes) < 3 else "quadratic"
    return [interp.interp1d(tContactNodes, normals[:, i], kind=kind, fill_value='extrapolate') for i in range(3)]'''

src = TARGET.read_text(encoding='utf-8')

if 'PATCH (HESTIA)' in src:
    print(f'{TARGET}: already patched, skipping')
    sys.exit(0)

if OLD not in src:
    print(f'{TARGET}: unexpected content — did the upstream image change?', file=sys.stderr)
    sys.exit(1)

TARGET.write_text(src.replace(OLD, NEW), encoding='utf-8')
print(f'{TARGET}: patched OK')
