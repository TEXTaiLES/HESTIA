import { PATCH_SIDE_MATERIAL_DEFAULTS, MATERIAL_REFERENCES, MATERIAL_NAMES, CUSTOM_MATERIAL_TOKEN } from '../utils/material-defaults.js';

/**
 * Patch Simulation Modal
 *
 * Bootstrap modal with the form fields required by
 * POST /dynamo/patch-simulations. Warp and weft are fixed sides
 * (rendered as two non-removable cards). Only structureType is
 * pre-filled ("Patch"); every other field is empty so the submitter
 * fills it in — or picks a material preset per side to auto-fill.
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

                    <h6 class="border-bottom pb-2 mb-3">Parameter Sweep <span class="text-muted small">(optional)</span></h6>
                    <div class="form-check mb-2">
                        <input class="form-check-input" type="checkbox" id="patchSweepToggle">
                        <label class="form-check-label" for="patchSweepToggle">Enable parameter sweep</label>
                        <div class="form-text mt-0">Run several simulations that share one experiment number, varying one parameter across a range.</div>
                    </div>
                    <div id="patchSweepConfig" class="border rounded p-3 mb-3 bg-light" style="display:none;">
                        <div class="row g-2 align-items-end">
                            <div class="col-md-5">
                                <label class="form-label small mb-1">Parameter</label>
                                <select class="form-select form-select-sm" id="patchSweepParameter">
                                    <optgroup label="Warp">
                                        <option value="warpYarnDiameter">Thread Diameter</option>
                                        <option value="warpYoungsModulus">Young's Modulus</option>
                                        <option value="warpYarnCountPerDistance">Thread Count Per Distance</option>
                                    </optgroup>
                                    <optgroup label="Weft">
                                        <option value="weftYarnDiameter">Thread Diameter</option>
                                        <option value="weftYoungsModulus">Young's Modulus</option>
                                        <option value="weftYarnCountPerDistance">Thread Count Per Distance</option>
                                    </optgroup>
                                </select>
                            </div>
                            <div class="col-md-3">
                                <label class="form-label small mb-1">Reference value</label>
                                <input type="number" step="any" class="form-control form-control-sm" id="patchSweepReference" placeholder="from form or type">
                            </div>
                            <div class="col-md-2">
                                <label class="form-label small mb-1">Variation (%)</label>
                                <input type="number" step="any" min="0" value="10" class="form-control form-control-sm" id="patchSweepPercent">
                            </div>
                            <div class="col-md-2">
                                <label class="form-label small mb-1">Steps</label>
                                <input type="number" min="2" max="20" step="1" value="5" class="form-control form-control-sm" id="patchSweepSteps">
                            </div>
                        </div>
                        <div class="form-text mt-2 mb-0">
                            The reference value is auto-filled from the corresponding side's form input when a value is present there; otherwise please type it here. The sweep runs from <em>Reference &minus; variation%</em> to <em>Reference + variation%</em> in <em>Steps</em> linear points. Defaults: &plusmn;10%, 5 steps.
                            <span id="patchSweepPreview" class="d-block mt-1 fw-semibold"></span>
                        </div>
                    </div>
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
    // Material presets injected server-side. Each side's card carries its
    // own <select> + custom text input; picking a preset fills only that
    // side's fields (both sides can be different materials).
    const MATERIAL_NAMES = ${JSON.stringify(MATERIAL_NAMES)};
    const CUSTOM_MATERIAL_TOKEN = ${JSON.stringify(CUSTOM_MATERIAL_TOKEN)};
    const PATCH_SIDE_MATERIAL_DEFAULTS = ${JSON.stringify(PATCH_SIDE_MATERIAL_DEFAULTS)};

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

    const materialOptionsHtml = '<option value="" selected>&mdash; Select material &mdash;</option>'
        + MATERIAL_NAMES.map(m => '<option value="' + m + '">' + m + '</option>').join('')
        + '<option value="' + CUSTOM_MATERIAL_TOKEN + '">Other (specify)&hellip;</option>';

    const sideCardHtml = (sideKey, sideLabel) => \`
        <div class="card mb-3 patch-side-card" data-side="\${sideKey}">
            <div class="card-header py-2"><strong>\${sideLabel}</strong></div>
            <div class="card-body">
                <div class="row g-3 mb-2">
                    <div class="col-md-6">
                        <label class="form-label mb-1">
                            Material
                            <button type="button" class="btn btn-link btn-sm p-0 ms-1 align-baseline" data-field="materialInfoBtn" style="display:none;" tabindex="0" aria-label="Material info">
                                <i class="fas fa-info-circle text-muted"></i>
                            </button>
                        </label>
                        <select class="form-select" data-field="material">\${materialOptionsHtml}</select>
                        <input type="text" class="form-control mt-2" data-field="materialCustom" placeholder="Custom material name" style="display:none;">
                        <div class="form-text">Picking a preset fills this side's Young's modulus. Click the info icon for description, tensile strength, and source.</div>
                    </div>
                </div>
                <div class="row g-3 mb-2">
                    \${valueUnitPairHtmlForSide("Young's Modulus", 'youngsModulus', '', '')}
                    \${valueUnitPairHtmlForSide('Poisson Ratio', 'poissonRatio', '', '')}
                </div>
                <div class="row g-3 mb-2">
                    \${valueUnitPairHtmlForSide('Thread Diameter', 'yarnDiameter', '', '')}
                    \${valueUnitPairHtmlForSide('Thread Diameter Ratio', 'yarnDiameterRatio', '', '')}
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

    // Wire up per-side material auto-fill. Cards were just inserted, so
    // we can bind directly instead of using event delegation.
    function setCardValueUnit(card, field, uv) {
        if (!uv) return;
        const wrapper = card.querySelector('[data-field="' + field + '"]');
        if (!wrapper) return;
        const v = wrapper.querySelector('[data-part="value"]');
        const u = wrapper.querySelector('[data-part="unit"]');
        if (v && uv.value != null) v.value = uv.value;
        if (u && uv.unit != null) u.value = uv.unit;
    }
    function applySideMaterial(card, materialKey) {
        const defaults = PATCH_SIDE_MATERIAL_DEFAULTS[materialKey];
        if (!defaults) return;
        for (const [field, uv] of Object.entries(defaults)) {
            setCardValueUnit(card, field, uv);
        }
    }
    // Popovers live inside each card so warp and weft can carry different
    // material info independently. One Popover instance per side, tracked in
    // a WeakMap keyed by the card element so we don't leak on rerender.
    const PATCH_MATERIAL_REFERENCES = ${JSON.stringify(MATERIAL_REFERENCES)};
    const sideMaterialPopovers = new WeakMap();
    function escapeHtml(str) {
        return String(str)
            .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
    }
    function renderMaterialInfoHtml(ref) {
        const parts = [];
        if (ref.description) {
            parts.push('<div class="mb-2">' + escapeHtml(ref.description) + '</div>');
        }
        const ts = ref.tensileStrength;
        if (ts && ts.value != null) {
            const mean = ts.value.toFixed(1);
            const sd = ts.standardDeviation != null ? ' &plusmn; ' + ts.standardDeviation.toFixed(1) : '';
            parts.push('<div class="small mb-2"><strong>Tensile strength:</strong> ' + mean + sd + ' ' + escapeHtml(ts.unit || '') + '</div>');
        }
        if (ref.source) {
            parts.push('<div class="small text-muted"><strong>Source:</strong> ' + escapeHtml(ref.source) + '</div>');
        }
        return parts.join('');
    }
    function updateSideMaterialInfo(card, materialKey) {
        const btn = card.querySelector('[data-field="materialInfoBtn"]');
        if (!btn) return;
        const existing = sideMaterialPopovers.get(card);
        if (existing) {
            existing.dispose();
            sideMaterialPopovers.delete(card);
        }
        const ref = PATCH_MATERIAL_REFERENCES[materialKey];
        if (!ref) {
            btn.style.display = 'none';
            return;
        }
        btn.style.display = '';
        const popover = new bootstrap.Popover(btn, {
            html: true,
            trigger: 'focus',
            placement: 'right',
            title: materialKey,
            content: renderMaterialInfoHtml(ref),
            customClass: 'material-info-popover',
        });
        sideMaterialPopovers.set(card, popover);
    }
    sidesContainer.querySelectorAll('.patch-side-card').forEach(card => {
        const sel = card.querySelector('[data-field="material"]');
        const custom = card.querySelector('[data-field="materialCustom"]');
        if (!sel || !custom) return;
        sel.addEventListener('change', () => {
            if (sel.value === CUSTOM_MATERIAL_TOKEN) {
                custom.style.display = '';
                custom.focus();
                updateSideMaterialInfo(card, null);
            } else {
                custom.style.display = 'none';
                custom.value = '';
                applySideMaterial(card, sel.value);
                updateSideMaterialInfo(card, sel.value);
                // If a sweep is active on the side we just re-filled (e.g.
                // Warp Young's Modulus), refresh the reference from the
                // updated form field. pullReferenceFromForm() reads only
                // from the swept param's designated selector, so a weft
                // material change won't touch a warp sweep and vice versa.
                if (sweepToggle.checked) {
                    pullReferenceFromForm();
                    updateSweepPreview();
                }
            }
        });
    });

    // Grab the form early so updateSweptFieldsState (called synchronously
    // below) can query for inputs inside it. The submit handler further
    // down still uses this same const.
    const form = document.getElementById('patchSimulationForm');
    const alertBox = document.getElementById('patchSimulationAlert');
    const submitBtn = document.getElementById('patchSimulationSubmitBtn');

    // Parameter sweep toggle: show/hide the sweep config block. Payload
    // building reads the toggle state directly. Also disables the regular
    // input(s) for the currently-selected parameter so the user can't type
    // a value that would be silently overridden by the sweep. yarnDiameter
    // targets both warp and weft cards because they're swept in lock-step.
    const sweepToggle = document.getElementById('patchSweepToggle');
    const sweepConfig = document.getElementById('patchSweepConfig');
    const sweepParamEl = document.getElementById('patchSweepParameter');
    const sweepReferenceEl = document.getElementById('patchSweepReference');
    const sweepPercentEl = document.getElementById('patchSweepPercent');
    const sweepStepsEl = document.getElementById('patchSweepSteps');
    const sweepPreviewEl = document.getElementById('patchSweepPreview');
    // Scoped by data-side= so warp and weft can be swept independently.
    const SWEEP_TARGET_SELECTORS = {
        warpYarnDiameter:         ['.patch-side-card[data-side="warp"] [data-field="yarnDiameter"] input'],
        weftYarnDiameter:         ['.patch-side-card[data-side="weft"] [data-field="yarnDiameter"] input'],
        warpYoungsModulus:        ['.patch-side-card[data-side="warp"] [data-field="youngsModulus"] input'],
        weftYoungsModulus:        ['.patch-side-card[data-side="weft"] [data-field="youngsModulus"] input'],
        warpYarnCountPerDistance: ['.patch-side-card[data-side="warp"] [data-field="yarnCountPerDistance"] input'],
        weftYarnCountPerDistance: ['.patch-side-card[data-side="weft"] [data-field="yarnCountPerDistance"] input'],
    };
    // Where to read the reference value from when a sweep is enabled — the
    // <input data-part="value"> inside the corresponding side's card.
    const SWEEP_REFERENCE_SELECTORS = {
        warpYarnDiameter:         '.patch-side-card[data-side="warp"] [data-field="yarnDiameter"] [data-part="value"]',
        weftYarnDiameter:         '.patch-side-card[data-side="weft"] [data-field="yarnDiameter"] [data-part="value"]',
        warpYoungsModulus:        '.patch-side-card[data-side="warp"] [data-field="youngsModulus"] [data-part="value"]',
        weftYoungsModulus:        '.patch-side-card[data-side="weft"] [data-field="youngsModulus"] [data-part="value"]',
        warpYarnCountPerDistance: '.patch-side-card[data-side="warp"] [data-field="yarnCountPerDistance"] [data-part="value"]',
        weftYarnCountPerDistance: '.patch-side-card[data-side="weft"] [data-field="yarnCountPerDistance"] [data-part="value"]',
    };
    function updateSweepVisibility() {
        sweepConfig.style.display = sweepToggle.checked ? '' : 'none';
    }
    function updateSweptFieldsState() {
        for (const selectors of Object.values(SWEEP_TARGET_SELECTORS)) {
            for (const sel of selectors) {
                form.querySelectorAll(sel).forEach(el => { el.disabled = false; });
            }
        }
        if (!sweepToggle.checked) return;
        const selected = sweepParamEl.value;
        for (const sel of (SWEEP_TARGET_SELECTORS[selected] || [])) {
            form.querySelectorAll(sel).forEach(el => { el.disabled = true; });
        }
    }
    function pullReferenceFromForm() {
        const sel = SWEEP_REFERENCE_SELECTORS[sweepParamEl.value];
        if (!sel) return;
        const el = form.querySelector(sel);
        if (!el) return;
        const raw = el.value;
        if (raw !== '' && raw != null) sweepReferenceEl.value = raw;
    }
    function updateSweepPreview() {
        const ref = parseFloat(sweepReferenceEl.value);
        const pct = parseFloat(sweepPercentEl.value);
        const steps = parseInt(sweepStepsEl.value, 10);
        if (!Number.isFinite(ref) || !Number.isFinite(pct) || !Number.isFinite(steps) || steps < 2) {
            sweepPreviewEl.textContent = '';
            return;
        }
        const from = ref * (1 - pct / 100);
        const to = ref * (1 + pct / 100);
        sweepPreviewEl.textContent = 'Range: ' + from.toPrecision(4) + ' → ' + to.toPrecision(4) + ' in ' + steps + ' steps.';
    }
    sweepToggle.addEventListener('change', () => {
        updateSweepVisibility();
        updateSweptFieldsState();
        if (sweepToggle.checked) pullReferenceFromForm();
        updateSweepPreview();
    });
    sweepParamEl.addEventListener('change', () => {
        updateSweptFieldsState();
        sweepReferenceEl.value = '';
        pullReferenceFromForm();
        updateSweepPreview();
    });
    sweepReferenceEl.addEventListener('input', updateSweepPreview);
    sweepPercentEl.addEventListener('input', updateSweepPreview);
    sweepStepsEl.addEventListener('input', updateSweepPreview);
    updateSweepVisibility();
    updateSweptFieldsState();
    updateSweepPreview();

    // Reset the form when the modal closes (X, Cancel, backdrop, Esc). Bootstrap
    // fires hidden.bs.modal after the closing animation finishes. form.reset()
    // clears the standard inputs but doesn't touch our per-side custom-name
    // inputs, hide them again, or reset the sweep controls (which live outside
    // the <form>), so we do that manually.
    const modalEl = document.getElementById('patchSimulationModal');
    modalEl.addEventListener('hidden.bs.modal', () => {
        form.reset();
        alertBox.innerHTML = '';
        sidesContainer.querySelectorAll('[data-field="materialCustom"]').forEach(c => {
            c.value = '';
            c.style.display = 'none';
        });
        sidesContainer.querySelectorAll('.patch-side-card').forEach(card => updateSideMaterialInfo(card, null));
        sweepToggle.checked = false;
        sweepReferenceEl.value = '';
        sweepPercentEl.value = '10';
        sweepStepsEl.value = '5';
        sweepParamEl.selectedIndex = 0;
        updateSweepVisibility();
        updateSweptFieldsState();
        updateSweepPreview();
    });

    // ----- Submit handler -----
    // form/alertBox/submitBtn already declared above so the sweep helpers
    // (which run during setup) can reach the form.

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
        // Material dropdown: sentinel "Other" means read the custom name field.
        const rawMaterial = get('material');
        const material = rawMaterial === CUSTOM_MATERIAL_TOKEN ? get('materialCustom') : rawMaterial;
        return {
            material: material,
            youngsModulus: get('youngsModulus'),
            poissonRatio: get('poissonRatio'),
            yarnDiameter: get('yarnDiameter'),
            yarnDiameterRatio: get('yarnDiameterRatio'),
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

        // artefact_id is taken from the current URL so the backend can
        // scope the per-artefact experiment_id counter.
        const artMatch = window.location.pathname.match(/\\/artefacts\\/(\\d+)/);
        const artefactId = artMatch ? parseInt(artMatch[1], 10) : null;

        const payload = {
            structureType: str(fd.get('structureType')) || 'Patch',
            artefact_id: artefactId,
            simulationInput: simulationInput,
        };

        // Attach sweep spec if the toggle is on and inputs parse cleanly.
        // Reference + variation% get converted to from/to here so the backend
        // keeps its existing (from, to, steps) contract.
        if (sweepToggle.checked) {
            const parameter = sweepParamEl.value;
            const reference = parseFloat(sweepReferenceEl.value);
            const percent = parseFloat(sweepPercentEl.value);
            const steps = parseInt(sweepStepsEl.value, 10);
            if (Number.isFinite(reference) && Number.isFinite(percent) && Number.isFinite(steps)
                    && steps >= 2 && steps <= 20) {
                const from = reference * (1 - percent / 100);
                const to = reference * (1 + percent / 100);
                payload.sweep = { parameter: parameter, from: from, to: to, steps: steps };
            }
        }

        return payload;
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
