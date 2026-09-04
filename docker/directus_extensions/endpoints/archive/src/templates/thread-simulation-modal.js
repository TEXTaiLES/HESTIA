import { THREAD_MATERIAL_DEFAULTS, MATERIAL_NAMES, CUSTOM_MATERIAL_TOKEN } from '../utils/material-defaults.js';

/**
 * Thread Simulation Modal
 *
 * Bootstrap modal with the form fields required by POST /dynamo/thread-simulations.
 * Shape matches TEXTaiLES_DynaMo_Thread.scheme.json:
 *
 *   simulationInput:
 *     friction, adhesion, appliedElongation   — top-level {unit, value}
 *     discretization                          — {periodCount, nodesPerPeriodCount}
 *     structureInput:
 *       hierarchyLevel (1 = plied, 2 = re-plied)
 *       threadTotalDiameter, threadPitch, threadTwistDirection, threadFoldCount
 *       singleYarnDiameter, singleYarnMaterial, singleYarnYoungsModulus, singleYarnPoissonRatio
 *       # Only when hierarchyLevel = 2:
 *       plyTotalDiameter, plyPitch, plyTwistDirection, plyFoldCount
 *
 * The ply block reveals itself when hierarchyLevel switches to 2, and its
 * fields are only included in the submitted payload for level 2 (so a level 1
 * submission stays a clean level-1 payload even if the user typed values into
 * hidden ply inputs).
 */

export const renderThreadSimulationModal = () => `
<div class="modal fade" id="threadSimulationModal" tabindex="-1" aria-labelledby="threadSimulationModalLabel" aria-hidden="true">
    <div class="modal-dialog modal-lg modal-dialog-scrollable">
        <div class="modal-content">
            <div class="modal-header">
                <h5 class="modal-title" id="threadSimulationModalLabel">Thread Simulation</h5>
                <button type="button" class="btn-close" data-bs-dismiss="modal" aria-label="Close"></button>
            </div>
            <div class="modal-body">
                <form id="threadSimulationForm">
                    <div id="threadSimulationAlert"></div>

                    <h6 class="border-bottom pb-2 mb-3">General</h6>
                    <div class="row g-3 mb-3">
                        <div class="col-md-6">
                            <label class="form-label">Structure Type</label>
                            <input type="text" class="form-control" name="structureType" value="Thread" readonly>
                        </div>
                        <div class="col-md-6">
                            <label class="form-label">Hierarchy Level</label>
                            <select class="form-select" name="hierarchyLevel" required>
                                <option value="1" selected>1 &mdash; Plied thread</option>
                                <option value="2">2 &mdash; Re-plied thread</option>
                            </select>
                        </div>
                    </div>

                    <div class="row g-3 mb-4">
                        <div class="col-md-6">
                            <label class="form-label">Discretization Period Count</label>
                            <input type="number" class="form-control" name="discretizationPeriodCount" min="1">
                        </div>
                        <div class="col-md-6">
                            <label class="form-label">Nodes Per Period Count</label>
                            <input type="number" class="form-control" name="discretizationNodesPerPeriodCount" min="2">
                        </div>
                    </div>

                    <h6 class="border-bottom pb-2 mb-3">Loading &amp; Contact</h6>
                    <div class="row g-3 mb-3">
                        ${renderValueUnitPair('Friction', 'friction', '', '1')}
                        ${renderValueUnitPair('Adhesion', 'adhesion', '', '1')}
                    </div>
                    <div class="row g-3 mb-4">
                        ${renderValueUnitPair('Applied Elongation', 'appliedElongation', '', '%')}
                    </div>

                    <h6 class="border-bottom pb-2 mb-3">Thread Structure</h6>
                    <div class="row g-3 mb-3">
                        <div class="col-md-6">
                            <label class="form-label">Twist Direction</label>
                            <select class="form-select" name="threadTwistDirection">
                                <option value="" selected>—</option>
                                <option value="S">S</option>
                                <option value="Z">Z</option>
                            </select>
                        </div>
                        <div class="col-md-6">
                            <label class="form-label">Fold Count</label>
                            <input type="number" class="form-control" name="threadFoldCount" min="1">
                        </div>
                    </div>
                    <div class="row g-3 mb-4">
                        ${renderValueUnitPair('Total Diameter', 'threadTotalDiameter', '', 'mm')}
                        ${renderValueUnitPair('Pitch', 'threadPitch', '', 'mm')}
                    </div>

                    <h6 class="border-bottom pb-2 mb-3">Single Yarn</h6>
                    <div class="row g-3 mb-3">
                        <div class="col-md-6">
                            <label class="form-label">Material</label>
                            <select class="form-select" name="singleYarnMaterial" id="threadMaterialSelect">
                                <option value="" selected>&mdash; Select material &mdash;</option>
                                ${MATERIAL_NAMES.map(m => `<option value="${m}">${m}</option>`).join('')}
                                <option value="${CUSTOM_MATERIAL_TOKEN}">Other (specify)&hellip;</option>
                            </select>
                            <input type="text" class="form-control mt-2" name="singleYarnMaterialCustom" id="threadMaterialCustom" placeholder="Custom material name" style="display:none;">
                            <div class="form-text">Picking a preset fills the rest of the form with example defaults.</div>
                        </div>
                        ${renderValueUnitPair('Diameter', 'singleYarnDiameter', '', 'mm')}
                    </div>
                    <div class="row g-3 mb-4">
                        ${renderValueUnitPair("Young's Modulus", 'singleYarnYoungsModulus', '', 'MPa')}
                        ${renderValueUnitPair('Poisson Ratio', 'singleYarnPoissonRatio', '', '1')}
                    </div>

                    <div id="threadPlySection" style="display:none;">
                        <h6 class="border-bottom pb-2 mb-3">Ply Structure <span class="text-muted small">(hierarchy level 2 only)</span></h6>
                        <div class="row g-3 mb-3">
                            <div class="col-md-6">
                                <label class="form-label">Ply Twist Direction</label>
                                <select class="form-select" name="plyTwistDirection">
                                    <option value="" selected>—</option>
                                    <option value="S">S</option>
                                    <option value="Z">Z</option>
                                </select>
                            </div>
                            <div class="col-md-6">
                                <label class="form-label">Ply Fold Count</label>
                                <input type="number" class="form-control" name="plyFoldCount" min="1">
                            </div>
                        </div>
                        <div class="row g-3 mb-4">
                            ${renderValueUnitPair('Ply Total Diameter', 'plyTotalDiameter', '', 'mm')}
                            ${renderValueUnitPair('Ply Pitch', 'plyPitch', '', 'mm')}
                        </div>
                    </div>
                </form>
            </div>
            <div class="modal-footer">
                <button type="button" class="btn btn-secondary" data-bs-dismiss="modal">Cancel</button>
                <button type="submit" form="threadSimulationForm" id="threadSimulationSubmitBtn" class="btn btn-red">
                    <i class="fas fa-paper-plane"></i> Submit
                </button>
            </div>
        </div>
    </div>
</div>

<script>
(function () {
    // Toggle the ply section based on hierarchyLevel. Values still exist in
    // the DOM when hidden, but buildPayload() gates ply-* on level = 2 so
    // stray inputs don't leak into a level-1 submission.
    const form = document.getElementById('threadSimulationForm');
    const hierarchyEl = form.querySelector('[name="hierarchyLevel"]');
    const plySection = document.getElementById('threadPlySection');
    function updatePlyVisibility() {
        plySection.style.display = (hierarchyEl.value === '2') ? '' : 'none';
    }
    hierarchyEl.addEventListener('change', updatePlyVisibility);
    updatePlyVisibility();

    // Hide any fields already known from artefact metadata.
    if (window.HestiaMetaPrefill) {
        window.HestiaMetaPrefill.applyThread(form);
    }

    // Material-preset auto-fill. Preset dictionaries and the "Other" sentinel
    // are injected server-side so the browser doesn't need its own copy.
    const THREAD_MATERIAL_DEFAULTS = ${JSON.stringify(THREAD_MATERIAL_DEFAULTS)};
    const CUSTOM_MATERIAL_TOKEN = ${JSON.stringify(CUSTOM_MATERIAL_TOKEN)};
    const materialSelect = document.getElementById('threadMaterialSelect');
    const materialCustom = document.getElementById('threadMaterialCustom');

    function setValueUnit(name, uv) {
        if (!uv) return;
        const v = form.querySelector('[name="' + name + '_value"]');
        const u = form.querySelector('[name="' + name + '_unit"]');
        if (v && uv.value != null) v.value = uv.value;
        if (u && uv.unit != null) u.value = uv.unit;
    }
    function setScalar(name, val) {
        const el = form.querySelector('[name="' + name + '"]');
        if (el && val != null) el.value = val;
    }
    function applyThreadMaterial(materialKey) {
        const defaults = THREAD_MATERIAL_DEFAULTS[materialKey];
        if (!defaults) return;
        for (const [key, val] of Object.entries(defaults)) {
            if (val && typeof val === 'object' && ('unit' in val || 'value' in val)) {
                setValueUnit(key, val);
            } else {
                setScalar(key, val);
            }
        }
    }
    materialSelect.addEventListener('change', () => {
        const val = materialSelect.value;
        if (val === CUSTOM_MATERIAL_TOKEN) {
            materialCustom.style.display = '';
            materialCustom.focus();
        } else {
            materialCustom.style.display = 'none';
            materialCustom.value = '';
            applyThreadMaterial(val);
        }
    });

    // Reset the form when the modal closes (X, Cancel, backdrop, Esc). Bootstrap
    // fires hidden.bs.modal after the closing animation finishes, so the user
    // never sees the reset flicker. form.reset() doesn't fire change events, so
    // we manually re-run the visibility handlers.
    const modalEl = document.getElementById('threadSimulationModal');
    modalEl.addEventListener('hidden.bs.modal', () => {
        form.reset();
        alertBox.innerHTML = '';
        materialCustom.value = '';
        materialCustom.style.display = 'none';
        updatePlyVisibility();
    });

    // ----- Submit handler -----
    const alertBox = document.getElementById('threadSimulationAlert');
    const submitBtn = document.getElementById('threadSimulationSubmitBtn');

    function num(v) {
        if (v === '' || v === null || v === undefined) return null;
        const n = Number(v);
        return Number.isFinite(n) ? n : null;
    }

    function int(v) {
        if (v === '' || v === null || v === undefined) return null;
        const n = parseInt(v, 10);
        return Number.isFinite(n) ? n : null;
    }

    function str(v) {
        return (v === '' || v === null || v === undefined) ? null : v;
    }

    function unitValue(fd, name) {
        const value = num(fd.get(name + '_value'));
        const unit = str(fd.get(name + '_unit'));
        if (value === null && unit === null) return null;
        return { unit: unit, value: value };
    }

    // Material dropdown returns a preset name, '', or the sentinel meaning
    // "Other". In the sentinel case the real name lives in the custom input.
    function resolveMaterial(fd) {
        const raw = str(fd.get('singleYarnMaterial'));
        if (raw === CUSTOM_MATERIAL_TOKEN) return str(fd.get('singleYarnMaterialCustom'));
        return raw;
    }

    function buildPayload() {
        const fd = new FormData(form);
        const level = int(fd.get('hierarchyLevel'));

        const structureInput = {
            hierarchyLevel:          level,
            threadTotalDiameter:     unitValue(fd, 'threadTotalDiameter'),
            threadTwistDirection:    str(fd.get('threadTwistDirection')),
            threadPitch:             unitValue(fd, 'threadPitch'),
            threadFoldCount:         int(fd.get('threadFoldCount')),
            singleYarnDiameter:      unitValue(fd, 'singleYarnDiameter'),
            singleYarnMaterial:      resolveMaterial(fd),
            singleYarnYoungsModulus: unitValue(fd, 'singleYarnYoungsModulus'),
            singleYarnPoissonRatio:  unitValue(fd, 'singleYarnPoissonRatio'),
        };
        if (level === 2) {
            structureInput.plyTotalDiameter  = unitValue(fd, 'plyTotalDiameter');
            structureInput.plyTwistDirection = str(fd.get('plyTwistDirection'));
            structureInput.plyPitch          = unitValue(fd, 'plyPitch');
            structureInput.plyFoldCount      = int(fd.get('plyFoldCount'));
        }

        const simulationInput = {
            friction:          unitValue(fd, 'friction'),
            adhesion:          unitValue(fd, 'adhesion'),
            discretization: {
                periodCount:         int(fd.get('discretizationPeriodCount')),
                nodesPerPeriodCount: int(fd.get('discretizationNodesPerPeriodCount')),
            },
            appliedElongation: unitValue(fd, 'appliedElongation'),
            structureInput:    structureInput,
        };

        // artefact_id is taken from the current URL so the backend can
        // scope the per-artefact experiment_id counter.
        const artMatch = window.location.pathname.match(/\\/artefacts\\/(\\d+)/);
        const artefactId = artMatch ? parseInt(artMatch[1], 10) : null;

        return {
            structureType: str(fd.get('structureType')) || 'Thread',
            artefact_id: artefactId,
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
            const res = await fetch('/archive/dynamo/thread-simulations', {
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
                    sessionStorage.setItem('thread_simulation_' + artefactId, simId);
                }
                showAlert('success',
                    'Submitted. Simulation ID: <code>' + (simId || '—') + '</code>. '
                    + 'Loading visualization placeholder…'
                );
                if (simId) {
                    const url = new URL(window.location.href);
                    url.searchParams.set('thread_simulation', simId);
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

function renderValueUnitPair(label, name, valueDefault, unitDefault) {
    return `
        <div class="col-md-6">
            <label class="form-label">${label}</label>
            <div class="input-group">
                <input type="number" step="any" class="form-control" name="${name}_value" value="${valueDefault}" placeholder="value">
                <input type="text" class="form-control" name="${name}_unit" value="${unitDefault}" placeholder="unit" style="max-width: 80px;">
            </div>
        </div>`;
}
