/**
 * Patch Simulation Modal
 *
 * Bootstrap modal with the form fields required by
 * POST /dynamo/patch-simulations. Warp and weft are fixed sides
 * (rendered as two non-removable cards). Only structureType is
 * pre-filled ("Patch"); every other field is empty so the submitter
 * fills it in.
 */

export const renderPatchSimulationModal = () => `
<div class="modal fade" id="patchSimulationModal" tabindex="-1" aria-labelledby="patchSimulationModalLabel" aria-hidden="true">
    <div class="modal-dialog modal-lg modal-dialog-scrollable">
        <div class="modal-content">
            <div class="modal-header">
                <h5 class="modal-title" id="patchSimulationModalLabel">Patch Simulation</h5>
                <button type="button" class="btn-close" data-bs-dismiss="modal" aria-label="Close"></button>
            </div>
            <div class="modal-body">
                <form id="patchSimulationForm">
                    <div id="patchSimulationAlert"></div>

                    <h6 class="border-bottom pb-2 mb-3">General</h6>
                    <div class="row g-3 mb-3">
                        <div class="col-md-6">
                            <label class="form-label">Structure Type</label>
                            <input type="text" class="form-control" name="structureType" value="Patch" required>
                        </div>
                        <div class="col-md-6">
                            <label class="form-label">Weave Pattern</label>
                            <select class="form-select" name="weavePattern" required>
                                <option value="" selected disabled>Select…</option>
                                <option value="Canvas">Canvas</option>
                                <option value="Twill">Twill</option>
                                <option value="Satin">Satin</option>
                            </select>
                        </div>
                    </div>

                    <div class="row g-3 mb-3">
                        <div class="col-md-6">
                            <label class="form-label">Pattern Repetition (Warp)</label>
                            <input type="number" class="form-control" name="patternRepetitionCountWarp" min="1">
                        </div>
                        <div class="col-md-6">
                            <label class="form-label">Pattern Repetition (Weft)</label>
                            <input type="number" class="form-control" name="patternRepetitionCountWeft" min="1">
                        </div>
                    </div>

                    <div class="row g-3 mb-4">
                        <div class="col-md-6">
                            <label class="form-label">Intermediate Element Count</label>
                            <input type="number" class="form-control" name="intermediateElementCount" min="0">
                        </div>
                    </div>

                    <div id="patchSidesContainer"></div>
                </form>
            </div>
            <div class="modal-footer">
                <button type="button" class="btn btn-secondary" data-bs-dismiss="modal">Cancel</button>
                <button type="submit" form="patchSimulationForm" id="patchSimulationSubmitBtn" class="btn btn-red">
                    <i class="fas fa-paper-plane"></i> Submit
                </button>
            </div>
        </div>
    </div>
</div>

<script>
(function () {
    // Per-side fields use data-field instead of name= to avoid name
    // collisions between the warp and weft cards.
    const valueUnitPairHtmlForSide = (label, field, valueDefault, unitDefault) => \`
        <div class="col-md-6">
            <label class="form-label">\${label}</label>
            <div class="input-group" data-field="\${field}">
                <input type="number" step="any" class="form-control" data-part="value" value="\${valueDefault}" placeholder="value">
                <input type="text" class="form-control" data-part="unit" value="\${unitDefault}" placeholder="unit" style="max-width: 80px;">
            </div>
        </div>\`;

    const sideCardHtml = (sideKey, sideLabel) => \`
        <div class="card mb-3 patch-side-card" data-side="\${sideKey}">
            <div class="card-header py-2"><strong>\${sideLabel}</strong></div>
            <div class="card-body">
                <div class="row g-3 mb-2">
                    <div class="col-md-6">
                        <label class="form-label">Material</label>
                        <input type="text" class="form-control" data-field="material">
                    </div>
                </div>
                <div class="row g-3 mb-2">
                    \${valueUnitPairHtmlForSide("Young's Modulus", 'youngsModulus', '', '')}
                    \${valueUnitPairHtmlForSide('Poisson Ratio', 'poissonRatio', '', '')}
                </div>
                <div class="row g-3 mb-2">
                    \${valueUnitPairHtmlForSide('Thread Radius', 'yarnRadius', '', '')}
                    \${valueUnitPairHtmlForSide('Thread Radius Ratio', 'yarnRadiusRatio', '', '')}
                </div>
                <div class="row g-3">
                    \${valueUnitPairHtmlForSide('Thread Count Per Distance', 'yarnCountPerDistance', '', '')}
                    \${valueUnitPairHtmlForSide('Thread Friction', 'yarnFriction', '', '')}
                </div>
            </div>
        </div>\`;

    const sidesContainer = document.getElementById('patchSidesContainer');
    // Warp + Weft are fixed; render both up-front.
    sidesContainer.insertAdjacentHTML('beforeend', sideCardHtml('warp', 'Warp'));
    sidesContainer.insertAdjacentHTML('beforeend', sideCardHtml('weft', 'Weft'));

    // Hide any fields already known from artefact metadata (top-level +
    // both side cards).
    if (window.HestiaMetaPrefill) {
        window.HestiaMetaPrefill.applyPatch(document.getElementById('patchSimulationForm'));
    }

    // ----- Submit handler -----
    const form = document.getElementById('patchSimulationForm');
    const alertBox = document.getElementById('patchSimulationAlert');
    const submitBtn = document.getElementById('patchSimulationSubmitBtn');

    function num(v) {
        if (v === '' || v === null || v === undefined) return null;
        const n = Number(v);
        return Number.isFinite(n) ? n : null;
    }

    function str(v) {
        return (v === '' || v === null || v === undefined) ? null : v;
    }

    function readSide(card) {
        const get = (field) => {
            const el = card.querySelector('[data-field="' + field + '"]');
            if (!el) return null;
            if (el.classList.contains('input-group')) {
                const v = el.querySelector('[data-part="value"]');
                const u = el.querySelector('[data-part="unit"]');
                const value = num(v ? v.value : null);
                const unit = str(u ? u.value : null);
                if (value === null && unit === null) return null;
                return { unit: unit, value: value };
            }
            return str(el.value);
        };
        return {
            material: get('material'),
            youngsModulus: get('youngsModulus'),
            poissonRatio: get('poissonRatio'),
            yarnRadius: get('yarnRadius'),
            yarnRadiusRatio: get('yarnRadiusRatio'),
            yarnCountPerDistance: get('yarnCountPerDistance'),
            yarnFriction: get('yarnFriction'),
        };
    }

    function buildPayload() {
        const fd = new FormData(form);
        const warpCard = sidesContainer.querySelector('.patch-side-card[data-side="warp"]');
        const weftCard = sidesContainer.querySelector('.patch-side-card[data-side="weft"]');

        const simulationInput = {
            weavePattern: str(fd.get('weavePattern')),
            patternRepetitionCountWarp: num(fd.get('patternRepetitionCountWarp')),
            patternRepetitionCountWeft: num(fd.get('patternRepetitionCountWeft')),
            warpInput: readSide(warpCard),
            weftInput: readSide(weftCard),
            discretization: {
                intermediateElementCount: num(fd.get('intermediateElementCount')),
            },
        };

        return {
            structureType: str(fd.get('structureType')) || 'Patch',
            simulationInput: simulationInput,
        };
    }

    function showAlert(type, msg) {
        alertBox.innerHTML = '<div class="alert alert-' + type + ' alert-dismissible fade show" role="alert">'
            + msg + '<button type="button" class="btn-close" data-bs-dismiss="alert" aria-label="Close"></button></div>';
    }

    form.addEventListener('submit', async (e) => {
        e.preventDefault();
        alertBox.innerHTML = '';
        const originalText = submitBtn.innerHTML;
        submitBtn.disabled = true;
        submitBtn.innerHTML = '<i class="fas fa-spinner fa-spin"></i> Submitting...';

        try {
            const payload = buildPayload();
            const res = await fetch('/archive/dynamo/patch-simulations', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload),
            });
            const data = await res.json().catch(() => ({}));
            if (!res.ok) {
                const parts = [];
                if (data.error) parts.push(data.error);
                if (data.message) parts.push(data.message);
                if (!parts.length) parts.push(res.statusText || ('HTTP ' + res.status));
                showAlert('danger', 'Error: ' + parts.join(' — '));
            } else {
                const simId = data.simulation_id;
                const m = window.location.pathname.match(/\\/artefacts\\/(\\d+)/);
                const artefactId = m ? m[1] : null;
                if (simId && artefactId) {
                    sessionStorage.setItem('patch_simulation_' + artefactId, simId);
                }
                showAlert('success',
                    'Submitted. Simulation ID: <code>' + (simId || '—') + '</code>. '
                    + 'Loading visualization placeholder…'
                );
                // Bounce to the same page with ?patch_simulation=<id> so the
                // visualization section activates with the new sim id.
                if (simId) {
                    const url = new URL(window.location.href);
                    url.searchParams.set('patch_simulation', simId);
                    setTimeout(() => { window.location.href = url.toString(); }, 800);
                }
            }
        } catch (err) {
            showAlert('danger', 'Error: ' + err.message);
        } finally {
            submitBtn.disabled = false;
            submitBtn.innerHTML = originalText;
        }
    });
})();
</script>
`;
