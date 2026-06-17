/**
 * Yarn Simulation Modal
 *
 * Bootstrap modal with the form fields required by
 * POST /dynamo/yarn-simulations. Levels (inputLevelN) are
 * added/removed via + / × controls; yarnLevelCount is derived
 * from the number of rendered cards at submit time.
 */

export const renderYarnSimulationModal = () => `
<div class="modal fade" id="yarnSimulationModal" tabindex="-1" aria-labelledby="yarnSimulationModalLabel" aria-hidden="true">
    <div class="modal-dialog modal-lg modal-dialog-scrollable">
        <div class="modal-content">
            <div class="modal-header">
                <h5 class="modal-title" id="yarnSimulationModalLabel">Yarn Simulation</h5>
                <button type="button" class="btn-close" data-bs-dismiss="modal" aria-label="Close"></button>
            </div>
            <div class="modal-body">
                <form id="yarnSimulationForm">
                    <div id="yarnSimulationAlert"></div>

                    <h6 class="border-bottom pb-2 mb-3">General</h6>
                    <div class="row g-3 mb-3">
                        <div class="col-md-6">
                            <label class="form-label">Structure Type</label>
                            <input type="text" class="form-control" name="structureType" value="Yarn" required>
                        </div>
                    </div>

                    <div class="row g-3 mb-3">
                        ${renderValueUnitPair('Yarn Friction', 'yarnFriction', '', '1')}
                        ${renderValueUnitPair('Yarn Adhesion', 'yarnAdhesion', '', '1')}
                    </div>

                    <div class="row g-3 mb-3">
                        <div class="col-md-6">
                            <label class="form-label">Discretization Period Count</label>
                            <input type="number" class="form-control" name="discretizationPeriodCount" min="1">
                        </div>
                        <div class="col-md-6">
                            <label class="form-label">Discretization Nodes Per Period</label>
                            <input type="number" class="form-control" name="discretizationNodesPerPeriodCount" min="1">
                        </div>
                    </div>

                    <div class="row g-3 mb-4">
                        ${renderValueUnitPair('Applied Elongation', 'appliedElongation', '', '%')}
                    </div>

                    <div id="yarnLevelsContainer"></div>

                    <div class="d-flex justify-content-end mt-2">
                        <button type="button" id="yarnAddLevelBtn" class="btn btn-outline-secondary btn-sm">
                            <i class="fas fa-plus"></i> Add Level
                        </button>
                    </div>
                </form>
            </div>
            <div class="modal-footer">
                <button type="button" class="btn btn-secondary" data-bs-dismiss="modal">Cancel</button>
                <button type="submit" form="yarnSimulationForm" id="yarnSimulationSubmitBtn" class="btn btn-red">
                    <i class="fas fa-paper-plane"></i> Submit
                </button>
            </div>
        </div>
    </div>
</div>

<script>
(function () {
    const valueUnitPairHtml = (label, name, valueDefault, unitDefault) => \`
        <div class="col-md-6">
            <label class="form-label">\${label}</label>
            <div class="input-group">
                <input type="number" step="any" class="form-control" name="\${name}_value" value="\${valueDefault}" placeholder="value">
                <input type="text" class="form-control" name="\${name}_unit" value="\${unitDefault}" placeholder="unit" style="max-width: 80px;">
            </div>
        </div>\`;

    const levelCardHtml = (n) => \`
        <div class="card mb-3 yarn-level-card" data-level="\${n}">
            <div class="card-header py-2 d-flex justify-content-between align-items-center">
                <strong class="yarn-level-label">Level \${n}</strong>
                <button type="button" class="btn btn-sm btn-outline-danger yarn-level-remove" title="Remove level" \${n === 1 ? 'style="display:none;"' : ''}>
                    <i class="fas fa-times"></i>
                </button>
            </div>
            <div class="card-body">
                <div class="row g-3 mb-2">
                    <div class="col-md-6">
                        <label class="form-label">Substructure Count</label>
                        <input type="number" class="form-control" data-field="substructureCount" min="0">
                    </div>
                    <div class="col-md-6">
                        <label class="form-label">Outer Strand Count</label>
                        <input type="number" class="form-control" data-field="outerStrandCount" min="0">
                    </div>
                </div>

                <h6 class="mt-3 mb-2 text-muted">Core</h6>
                <div class="row g-3 mb-2">
                    <div class="col-md-6">
                        <label class="form-label">Twist Core</label>
                        <select class="form-select" data-field="twistCore">
                            <option value="">—</option>
                            <option value="S">S</option>
                            <option value="Z">Z</option>
                        </select>
                    </div>
                    <div class="col-md-6">
                        <label class="form-label">Material Core</label>
                        <input type="text" class="form-control" data-field="materialCore">
                    </div>
                </div>
                <div class="row g-3 mb-2">
                    \${valueUnitPairHtmlForLevel('Pitch Core', 'pitchCore', '', 'mm')}
                    \${valueUnitPairHtmlForLevel('Radius Core', 'radiusCore', '', 'mm')}
                </div>
                <div class="row g-3 mb-2">
                    \${valueUnitPairHtmlForLevel("Young's Modulus Core", 'youngsModulusCore', '', 'MPa')}
                    \${valueUnitPairHtmlForLevel('Poisson Ratio Core', 'poissonRatioCore', '', '1')}
                </div>

                <h6 class="mt-3 mb-2 text-muted">Outer</h6>
                <div class="row g-3 mb-2">
                    <div class="col-md-6">
                        <label class="form-label">Twist Outer</label>
                        <select class="form-select" data-field="twistOuter">
                            <option value="">—</option>
                            <option value="S">S</option>
                            <option value="Z">Z</option>
                        </select>
                    </div>
                    <div class="col-md-6">
                        <label class="form-label">Material Outer</label>
                        <input type="text" class="form-control" data-field="materialOuter">
                    </div>
                </div>
                <div class="row g-3 mb-2">
                    \${valueUnitPairHtmlForLevel('Pitch Outer', 'pitchOuter', '', 'mm')}
                    \${valueUnitPairHtmlForLevel('Radius Outer', 'radiusOuter', '', 'mm')}
                </div>
                <div class="row g-3">
                    \${valueUnitPairHtmlForLevel("Young's Modulus Outer", 'youngsModulusOuter', '', 'MPa')}
                    \${valueUnitPairHtmlForLevel('Poisson Ratio Outer', 'poissonRatioOuter', '', '1')}
                </div>
            </div>
        </div>\`;

    // Inside each level card, fields are addressed by data-field
    // rather than by global name — so the same DOM template can be
    // reused for every level without name collisions.
    function valueUnitPairHtmlForLevel(label, field, valueDefault, unitDefault) {
        return \`
        <div class="col-md-6">
            <label class="form-label">\${label}</label>
            <div class="input-group" data-field="\${field}">
                <input type="number" step="any" class="form-control" data-part="value" value="\${valueDefault}" placeholder="value">
                <input type="text" class="form-control" data-part="unit" value="\${unitDefault}" placeholder="unit" style="max-width: 80px;">
            </div>
        </div>\`;
    }

    const levelsContainer = document.getElementById('yarnLevelsContainer');
    const addLevelBtn = document.getElementById('yarnAddLevelBtn');

    function relabelLevels() {
        const cards = levelsContainer.querySelectorAll('.yarn-level-card');
        cards.forEach((card, idx) => {
            const n = idx + 1;
            card.dataset.level = String(n);
            const label = card.querySelector('.yarn-level-label');
            if (label) label.textContent = 'Level ' + n;
            const removeBtn = card.querySelector('.yarn-level-remove');
            if (removeBtn) removeBtn.style.display = (n === 1) ? 'none' : '';
        });
    }

    function addLevel() {
        const n = levelsContainer.querySelectorAll('.yarn-level-card').length + 1;
        levelsContainer.insertAdjacentHTML('beforeend', levelCardHtml(n));
        relabelLevels();
        // Hide any fields already known from artefact metadata on the new card.
        const newCard = levelsContainer.lastElementChild;
        if (window.HestiaMetaPrefill && newCard) {
            window.HestiaMetaPrefill.applyYarnLevelCard(newCard);
        }
    }

    levelsContainer.addEventListener('click', (e) => {
        const btn = e.target.closest('.yarn-level-remove');
        if (!btn) return;
        const card = btn.closest('.yarn-level-card');
        if (!card) return;
        const total = levelsContainer.querySelectorAll('.yarn-level-card').length;
        if (total <= 1) return; // must always have at least 1 level
        card.remove();
        relabelLevels();
    });

    addLevelBtn.addEventListener('click', addLevel);

    // Start with one level.
    addLevel();

    // Hide top-level fields already known from artefact metadata. (The
    // initial level card was already prefilled inside addLevel.)
    if (window.HestiaMetaPrefill) {
        window.HestiaMetaPrefill.applyYarn(document.getElementById('yarnSimulationForm'));
    }

    // ----- Submit handler -----
    const form = document.getElementById('yarnSimulationForm');
    const alertBox = document.getElementById('yarnSimulationAlert');
    const submitBtn = document.getElementById('yarnSimulationSubmitBtn');

    function num(v) {
        if (v === '' || v === null || v === undefined) return null;
        const n = Number(v);
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

    function readLevel(card) {
        const get = (field) => {
            const el = card.querySelector('[data-field="' + field + '"]');
            if (!el) return null;
            // input-group wrapper for value/unit pairs
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
        const getNum = (field) => {
            const el = card.querySelector('[data-field="' + field + '"]');
            return el ? num(el.value) : null;
        };

        return {
            substructureCount: getNum('substructureCount'),
            twistCore: get('twistCore'),
            pitchCore: get('pitchCore'),
            materialCore: get('materialCore'),
            youngsModulusCore: get('youngsModulusCore'),
            poissonRatioCore: get('poissonRatioCore'),
            radiusCore: get('radiusCore'),
            outerStrandCount: getNum('outerStrandCount'),
            twistOuter: get('twistOuter'),
            pitchOuter: get('pitchOuter'),
            materialOuter: get('materialOuter'),
            youngsModulusOuter: get('youngsModulusOuter'),
            poissonRatioOuter: get('poissonRatioOuter'),
            radiusOuter: get('radiusOuter'),
        };
    }

    function buildPayload() {
        const fd = new FormData(form);
        const cards = levelsContainer.querySelectorAll('.yarn-level-card');
        const levelCount = cards.length;

        const simulationInput = {
            yarnLevelCount: levelCount,
            yarnFriction: unitValue(fd, 'yarnFriction'),
            yarnAdhesion: unitValue(fd, 'yarnAdhesion'),
            discretization: {
                periodCount: num(fd.get('discretizationPeriodCount')),
                nodesPerPeriodCount: num(fd.get('discretizationNodesPerPeriodCount')),
            },
            appliedElongation: unitValue(fd, 'appliedElongation'),
        };

        cards.forEach((card, idx) => {
            simulationInput['inputLevel' + (idx + 1)] = readLevel(card);
        });

        return {
            structureType: str(fd.get('structureType')) || 'Yarn',
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
            const res = await fetch('/archive/dynamo/yarn-simulations', {
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
                // Match the artefact_id pattern used by the page-level visualization script.
                const m = window.location.pathname.match(/\\/artefacts\\/(\\d+)/);
                const artefactId = m ? m[1] : null;
                if (simId && artefactId) {
                    sessionStorage.setItem('yarn_simulation_' + artefactId, simId);
                }
                showAlert('success',
                    'Submitted. Simulation ID: <code>' + (simId || '—') + '</code>. '
                    + 'Loading visualization placeholder…'
                );
                // Bounce to the same page with ?yarn_simulation=<id> so the
                // visualization section on the artefact page activates.
                if (simId) {
                    const url = new URL(window.location.href);
                    url.searchParams.set('yarn_simulation', simId);
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