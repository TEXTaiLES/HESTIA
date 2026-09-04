/**
 * Patch Simulation Viewer Modal
 *
 * Renders a Bootstrap modal that displays one patch simulation's animations
 * (six per-experiment morph-target GLBs) plus the two polar stiffness plots
 * (Chart.js radar). Opened from the per-artefact list; the same modal
 * instance is reused for every row and reset between opens.
 *
 * Public API:
 *   window.openPatchViewer(simId, experimentId)
 */

export const renderPatchViewerModal = () => `
<div class="modal fade" id="patchViewerModal" tabindex="-1" aria-labelledby="patchViewerModalLabel" aria-hidden="true">
    <div class="modal-dialog modal-xl modal-dialog-scrollable">
        <div class="modal-content">
            <div class="modal-header">
                <div>
                    <h5 class="modal-title" id="patchViewerModalLabel">
                        Patch Simulation #<span id="patchViewerExperimentId">—</span>
                    </h5>
                    <div class="text-muted small mt-1">
                        Simulation ID: <code id="patchViewerSimId">—</code>
                        <span id="patchViewerStatus" class="ms-2"></span>
                    </div>
                </div>
                <div class="d-flex gap-2 align-items-start">
                    <a id="patchViewerDownloadBtn" href="#" class="btn btn-sm btn-outline-secondary" style="display:none;" download>
                        <i class="fas fa-download"></i> Download
                    </a>
                    <button type="button" class="btn-close" data-bs-dismiss="modal" aria-label="Close"></button>
                </div>
            </div>
            <div class="modal-body">
                <div class="d-flex align-items-center gap-2 mb-2">
                    <label for="patchViewerExperimentSelect" class="form-label mb-0 small">Experiment:</label>
                    <select id="patchViewerExperimentSelect" class="form-select form-select-sm" style="width:auto;">
                        <option value="inplane11">In-plane 11 (warp tension)</option>
                        <option value="inplane22">In-plane 22 (weft tension)</option>
                        <option value="inplane12">In-plane 12 (shear)</option>
                        <option value="bending11">Bending 11 (warp bend)</option>
                        <option value="bending22">Bending 22 (weft bend)</option>
                        <option value="bending12">Bending 12 (torsion)</option>
                    </select>
                </div>
                <div id="patchViewerAnimation" style="height: 400px;"></div>

                <div id="patchViewerStiffnessWrapper" class="mt-3" style="display:none;">
                    <h6 class="text-muted mb-2">Directional Stiffness (polar projection in the textile plane)</h6>
                    <div class="row g-3">
                        <div class="col-md-6">
                            <div class="text-center small text-muted mb-1">Effective Extensional Stiffness</div>
                            <div style="position:relative; height:320px;">
                                <canvas id="patchViewerExtChart"></canvas>
                            </div>
                        </div>
                        <div class="col-md-6">
                            <div class="text-center small text-muted mb-1">Effective Bending Stiffness</div>
                            <div style="position:relative; height:320px;">
                                <canvas id="patchViewerBendChart"></canvas>
                            </div>
                        </div>
                    </div>
                </div>
            </div>
        </div>
    </div>
</div>

<script>
(function () {
    const modalEl = document.getElementById('patchViewerModal');
    const expIdEl = document.getElementById('patchViewerExperimentId');
    const simIdEl = document.getElementById('patchViewerSimId');
    const statusEl = document.getElementById('patchViewerStatus');
    const dlBtn = document.getElementById('patchViewerDownloadBtn');
    const selectEl = document.getElementById('patchViewerExperimentSelect');
    const animationEl = document.getElementById('patchViewerAnimation');
    const stiffnessWrapper = document.getElementById('patchViewerStiffnessWrapper');
    const extCanvas = document.getElementById('patchViewerExtChart');
    const bendCanvas = document.getElementById('patchViewerBendChart');

    // Maps dropdown key → JSON field on simulationOutput.
    const expToField = {
        inplane11: 'visualizationFiles_inplane11',
        inplane22: 'visualizationFiles_inplane22',
        inplane12: 'visualizationFiles_inplane12',
        bending11: 'visualizationFiles_bending11',
        bending22: 'visualizationFiles_bending22',
        bending12: 'visualizationFiles_bending12',
    };
    let currentSimId = null;
    const experimentsReady = {};

    function setStatus(html) { statusEl.innerHTML = html; }
    function showError(msg) { setStatus('<span class="text-danger">' + msg + '</span>'); }

    function resetModal() {
        expIdEl.textContent = '—';
        simIdEl.textContent = '—';
        setStatus('');
        dlBtn.style.display = 'none';
        animationEl.innerHTML = '';
        stiffnessWrapper.style.display = 'none';
        for (const c of [extCanvas, bendCanvas]) {
            const existing = Chart.getChart(c);
            if (existing) existing.destroy();
        }
        for (const k of Object.keys(experimentsReady)) delete experimentsReady[k];
        currentSimId = null;
        selectEl.value = 'inplane11';
    }

    function renderAnimation(experiment) {
        if (!currentSimId) return;
        if (!experimentsReady[experiment]) {
            animationEl.innerHTML = '<div class="text-muted p-3">No frames available for ' + experiment + '.</div>';
            setStatus('<span class="text-warning">Empty: ' + experiment + '</span>');
            return;
        }
        const url = '/archive/assets/patch-simulation/' + encodeURIComponent(currentSimId)
            + '/visualization/' + encodeURIComponent(experiment) + '.glb';
        animationEl.innerHTML = ''
            + '<model-viewer src="' + url + '"'
            + ' camera-controls autoplay animation-name="deformation"'
            + ' environment-image="legacy" exposure="1.2" shadow-intensity="0.5"'
            + ' tone-mapping="commerce" style="width:100%; height:100%;"></model-viewer>';
        setStatus('<span class="text-success">Showing: ' + experiment + '</span>');
    }

    function renderPolar(canvas, angles, values, valueUnit, color) {
        if (!angles || !values || angles.length === 0 || angles.length !== values.length) return false;
        const existing = Chart.getChart(canvas);
        if (existing) existing.destroy();
        new Chart(canvas.getContext('2d'), {
            type: 'radar',
            data: {
                labels: angles.map(a => a.toFixed(0) + '°'),
                datasets: [{
                    label: 'Stiffness',
                    data: values,
                    borderColor: color,
                    backgroundColor: color.replace(')', ', 0.12)').replace('rgb', 'rgba'),
                    borderWidth: 2, pointRadius: 1, pointHoverRadius: 4,
                }]
            },
            options: {
                responsive: true, maintainAspectRatio: false,
                plugins: {
                    legend: { display: false },
                    tooltip: { callbacks: {
                        label: (c) => c.parsed.r.toExponential(3) + (valueUnit ? ' ' + valueUnit : '')
                    } }
                },
                scales: {
                    r: {
                        beginAtZero: true,
                        ticks: { display: false },
                        pointLabels: { font: { size: 9 } }
                    }
                }
            }
        });
        return true;
    }

    function renderStiffnessPlots(out) {
        const angles = (out.plotDataAngles && out.plotDataAngles.value) || null;
        const ext = (out.plotDataExtensionalStiffness && out.plotDataExtensionalStiffness.value) || null;
        const bend = (out.plotDataBendingStiffness && out.plotDataBendingStiffness.value) || null;
        const extUnit = (out.plotDataExtensionalStiffness && out.plotDataExtensionalStiffness.unit) || '';
        const bendUnit = (out.plotDataBendingStiffness && out.plotDataBendingStiffness.unit) || '';
        const drewExt = renderPolar(extCanvas, angles, ext, extUnit, 'rgb(184, 191, 26)');
        const drewBend = renderPolar(bendCanvas, angles, bend, bendUnit, 'rgb(23, 190, 207)');
        if (drewExt || drewBend) stiffnessWrapper.style.display = '';
    }

    selectEl.addEventListener('change', (e) => renderAnimation(e.target.value));

    window.openPatchViewer = function (simId, experimentId) {
        resetModal();
        currentSimId = simId;
        if (experimentId != null) expIdEl.textContent = String(experimentId);
        simIdEl.textContent = simId;
        dlBtn.href = '/archive/dynamo/patch-simulations/' + encodeURIComponent(simId) + '/download.zip';
        dlBtn.style.display = '';

        const modal = bootstrap.Modal.getOrCreateInstance(modalEl);
        modal.show();

        setStatus('<i class="fas fa-spinner fa-spin"></i> Loading…');
        // Use the artefact page's fetchWithAuthRetry to survive Directus's
        // single-use-refresh-token race.
        const fetcher = window.fetchWithAuthRetry || fetch;
        fetcher('/archive/dynamo/patch-simulations/' + encodeURIComponent(simId) + '?_=' + Date.now(),
            { cache: 'no-store' })
            .then(r => r.json().then(d => ({ ok: r.ok, data: d })))
            .then(({ ok, data }) => {
                if (!ok) {
                    showError('Could not load simulation: ' + (data.error || 'unknown error'));
                    return;
                }
                const out = data.simulationOutput;
                if (!out || !out.simulationCompleted) {
                    setStatus('<i class="fas fa-hourglass-half"></i> Simulation pending — reload when ready.');
                    return;
                }
                let totalFrames = 0;
                for (const [exp, field] of Object.entries(expToField)) {
                    const files = (out[field] || []);
                    experimentsReady[exp] = files.length > 0;
                    totalFrames += files.length;
                }
                if (totalFrames === 0) {
                    setStatus('<span class="text-warning">Completed but no animation frames.</span>');
                } else {
                    const firstReady = Object.keys(expToField).find(e => experimentsReady[e]);
                    if (firstReady) {
                        selectEl.value = firstReady;
                        renderAnimation(firstReady);
                    }
                }
                renderStiffnessPlots(out);
            })
            .catch(err => showError('Network error: ' + err.message));
    };

    modalEl.addEventListener('hidden.bs.modal', resetModal);
})();
</script>
`;
