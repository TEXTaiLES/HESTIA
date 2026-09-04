/**
 * Thread Simulation Viewer Modal
 *
 * Renders a Bootstrap modal that displays one thread simulation's animation
 * (morph-target GLB via model-viewer) and its Force-Elongation curve
 * (Chart.js). Opened from the per-artefact list; the same modal instance is
 * reused for every row and reset between opens.
 *
 * Public API (attached to window so the artefact-page IIFE can call it):
 *   window.openThreadViewer(simId, experimentId)
 */

export const renderThreadViewerModal = () => `
<div class="modal fade" id="threadViewerModal" tabindex="-1" aria-labelledby="threadViewerModalLabel" aria-hidden="true">
    <div class="modal-dialog modal-xl modal-dialog-scrollable">
        <div class="modal-content">
            <div class="modal-header">
                <div>
                    <h5 class="modal-title" id="threadViewerModalLabel">
                        Thread Simulation #<span id="threadViewerExperimentId">—</span>
                    </h5>
                    <div class="text-muted small mt-1">
                        Simulation ID: <code id="threadViewerSimId">—</code>
                        <span id="threadViewerStatus" class="ms-2"></span>
                    </div>
                </div>
                <div class="d-flex gap-2 align-items-start">
                    <a id="threadViewerDownloadBtn" href="#" class="btn btn-sm btn-outline-secondary" style="display:none;" download>
                        <i class="fas fa-download"></i> Download
                    </a>
                    <button type="button" class="btn-close" data-bs-dismiss="modal" aria-label="Close"></button>
                </div>
            </div>
            <div class="modal-body">
                <div id="threadViewerAnimation" style="height: 400px;"></div>
                <div id="threadViewerChartWrapper" class="mt-3" style="display:none;">
                    <h6 class="text-muted mb-2">Force-Elongation Diagram</h6>
                    <div style="position:relative; height:320px;">
                        <canvas id="threadViewerChart"></canvas>
                    </div>
                </div>
            </div>
        </div>
    </div>
</div>

<script>
(function () {
    const modalEl = document.getElementById('threadViewerModal');
    const expIdEl = document.getElementById('threadViewerExperimentId');
    const simIdEl = document.getElementById('threadViewerSimId');
    const statusEl = document.getElementById('threadViewerStatus');
    const dlBtn = document.getElementById('threadViewerDownloadBtn');
    const animationEl = document.getElementById('threadViewerAnimation');
    const chartWrapper = document.getElementById('threadViewerChartWrapper');
    const chartCanvas = document.getElementById('threadViewerChart');

    function setStatus(html) { statusEl.innerHTML = html; }
    function showError(msg) { setStatus('<span class="text-danger">' + msg + '</span>'); }

    function resetModal() {
        expIdEl.textContent = '—';
        simIdEl.textContent = '—';
        setStatus('');
        dlBtn.style.display = 'none';
        animationEl.innerHTML = '';
        chartWrapper.style.display = 'none';
        const existingChart = Chart.getChart(chartCanvas);
        if (existingChart) existingChart.destroy();
    }

    function renderAnimation(simId) {
        const url = '/archive/assets/thread-simulation/' + encodeURIComponent(simId) + '/visualization.glb';
        animationEl.innerHTML = ''
            + '<model-viewer src="' + url + '"'
            + ' camera-controls autoplay animation-name="deformation"'
            + ' environment-image="legacy" exposure="1.2" shadow-intensity="0.5"'
            + ' tone-mapping="commerce" style="width:100%; height:100%;"></model-viewer>';
    }

    function renderChart(out) {
        const elongations = (out.elongations && out.elongations.value) || [];
        const forces = (out.forces && out.forces.value) || [];
        if (!elongations.length || !forces.length || elongations.length !== forces.length) return;
        if (typeof Chart === 'undefined') return;
        const elongationUnit = (out.elongations && out.elongations.unit) || '%';
        const forceUnit = (out.forces && out.forces.unit) || 'N';
        chartWrapper.style.display = '';
        new Chart(chartCanvas.getContext('2d'), {
            type: 'line',
            data: { datasets: [{
                label: 'Force',
                data: elongations.map((e, i) => ({ x: e, y: forces[i] })),
                borderColor: '#d62728',
                backgroundColor: 'rgba(214, 39, 40, 0.1)',
                borderWidth: 2, pointRadius: 3, pointHoverRadius: 5, tension: 0.2, fill: false,
            }] },
            options: {
                responsive: true, maintainAspectRatio: false,
                plugins: {
                    legend: { display: false },
                    tooltip: { callbacks: {
                        label: (c) => 'Force: ' + c.parsed.y.toFixed(3) + ' ' + forceUnit
                            + ' at elongation ' + c.parsed.x.toFixed(3) + ' ' + elongationUnit
                    } }
                },
                scales: {
                    x: { type: 'linear', title: { display: true, text: 'Elongation (' + elongationUnit + ')' } },
                    y: { title: { display: true, text: 'Force (' + forceUnit + ')' } }
                }
            }
        });
    }

    window.openThreadViewer = function (simId, experimentId) {
        resetModal();
        if (experimentId != null) expIdEl.textContent = String(experimentId);
        simIdEl.textContent = simId;
        dlBtn.href = '/archive/dynamo/thread-simulations/' + encodeURIComponent(simId) + '/download.zip';
        dlBtn.style.display = '';

        const modal = bootstrap.Modal.getOrCreateInstance(modalEl);
        modal.show();

        setStatus('<i class="fas fa-spinner fa-spin"></i> Loading…');
        // Cache-bust so a stale 304 from before the simulator PATCHed the output
        // doesn't make us think it's still pending. Use the artefact page's
        // fetchWithAuthRetry to survive Directus's single-use-refresh-token race.
        const fetcher = window.fetchWithAuthRetry || fetch;
        fetcher('/archive/dynamo/thread-simulations/' + encodeURIComponent(simId) + '?_=' + Date.now(),
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
                const files = out.visualizationFiles || [];
                if (files.length > 0) {
                    renderAnimation(simId);
                    setStatus('<span class="text-success">Ready</span>');
                } else {
                    setStatus('<span class="text-warning">Completed but no animation frames.</span>');
                }
                renderChart(out);
            })
            .catch(err => showError('Network error: ' + err.message));
    };

    // Reset on close so reopening a different sim doesn't briefly show the old one.
    modalEl.addEventListener('hidden.bs.modal', resetModal);
})();
</script>
`;
