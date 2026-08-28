import { CSP_POLICY } from '../utils/constants.js';
import { userIsAuthenticated, userHasPermission, userIsAdmin } from '../utils/auth.js';
import { renderLoginPage } from '../templates/login.js';
import { render401Page } from '../templates/error.js';
import { renderNavbar } from '../templates/navbar.js';
import { renderHtmlPage, renderFooter } from '../templates/layout.js';
import { renderThreadSimulationModal } from '../templates/thread-simulation-modal.js';
import { renderPatchSimulationModal } from '../templates/patch-simulation-modal.js';
import { renderSimulationMetadataPrefillScript } from '../templates/simulation-metadata-prefill.js';

/**
 * Pull physics-relevant fields off the artefact record so the simulation
 * forms can pre-fill (and hide) inputs whose values are already known.
 *
 * Convention: each key in the returned dict matches the form field's
 * `name=` (top-level) or `data-field=` (per-card) attribute exactly.
 * Value/unit pairs use `{ unit, value }`. Scalars are returned as-is.
 *
 * Today this returns `{}` — there are no physics fields on the artefacts
 * collection yet. As fields are added through the Directus admin UI
 * (Flat columns on `artefacts`), extend this function to map
 * `artefact.<column>` → metadata key. The whole client-side framework
 * is otherwise unchanged.
 *
 * Example future addition:
 *   if (artefact.poisson_ratio_core_value != null) {
 *       meta.poissonRatioCore = {
 *           unit:  artefact.poisson_ratio_core_unit  || '1',
 *           value: artefact.poisson_ratio_core_value,
 *       };
 *   }
 */
function extractPhysicsMetadata(artefact) {
    const meta = {};
    // Intentionally empty for now — extend here when physics fields are added
    // to the `artefacts` collection in Directus.
    /* Example future addition:
    if (artefact.poisson_ratio_core_value != null) {
        meta.poissonRatioCore = {
            unit:  artefact.poisson_ratio_core_unit  || '1',
            value: artefact.poisson_ratio_core_value,
        };
    }
    */
    return meta;
}

export default (router, { services, database }) => {
	const { AuthenticationService, ItemsService } = services;

	router.get('/artefacts/:id', async (req, res) => {
		try {
            // Set response headers.
            res.set('Content-Type', 'text/html');
            res.set('Content-Security-Policy', CSP_POLICY);

            // If the user is not authenticated, show the login message.
            const isAuthenticated = await userIsAuthenticated(req, res, AuthenticationService);
            if (!isAuthenticated) {
                const html = renderLoginPage({
                    navbar: 'collections',
                    title: 'Collections',
                    subtitle: 'Explore Our Cultural Heritage Archive',
                });
                return res.send(html);
            }

			// Artefacts require read permission; redirect to 401 if not allowed.
			const canRead = await userHasPermission(req, res, ItemsService, 'artefacts', 'read');
			if (!canRead) {
				return res.status(401).send(render401Page({ activePage: 'collections' }));
			}

			const isAdmin = await userIsAdmin(req, res, ItemsService);

            // Build the Artefact page.
			const artefactsService = new ItemsService('artefacts', {
				schema: req.schema,
				accountability: null,
			});

            // Fetches artefact from database by ID
			const artefacts = await artefactsService.readByQuery({
				fields: [
					'id', 'title', 'published', 'gltf_file', 'obj_file', 'ThreeD_model_id', 'robot_scan_id',
					'obj_files.directus_files_id', // e.g., ["ben-uuid", "ben_ks-uuid"]
					// Heritage Asset
					'description', 'date_timespan', 'dimensions', 'owner', 'textile_category',
					'keywords', 'inventory_number', 'origin',
					// Digital Asset
					'digitization_methods', 'digitization_actor', 'resolution',
					// Identification
					'accession_number', 'reference_name_number', 'material_analyzed',
					// Condition
					'object_status', 'condition_assessment', 'state_of_preservation', 'type_of_preservation',
					// Preventive conservation
					'temperature', 'humidity', 'type_of_container', 'mount', 'result',
					// Interventive conservation
					'conservation_date', 'cleaning', 'introduction_of_foreign_material', 'specific_foreign_material_introduce',
					// Legacy fields
					'creator', 'sensor', 'location', 'source', 'time_period', 'collection', 'use_case',
                    // Physics metadata (for pre-filling simulation forms)
                    //'poisson_ratio_core_value', 'poisson_ratio_core_unit'
				],
				filter: { id: { _eq: req.params.id } },
				limit: 1
			});

			if (!artefacts || artefacts.length === 0) {
				return res.status(404).send('Artefact not found');
			}

			const artefact = artefacts[0];

			// Non-admins cannot access unpublished artefacts.
			if (!isAdmin && !artefact.published) {
				return res.status(404).send('Artefact not found');
			}

			// Fetch all robot images from the same scan as the linked image_id.
			// Step 1: get scan_id from image_id. Step 2: get all images in that scan.
			let robotImages = [];
			let robotImagesError = null;
			if (artefact.robot_scan_id && database) {
				try {
					const ref = await database('robot_images')
						.where('image_id', artefact.robot_scan_id)
						.select('scan_id')
						.first();
					if (ref) {
						robotImages = await database('robot_images')
							.where('scan_id', ref.scan_id)
							.orderBy('timestamp', 'asc');
					}
				} catch (err) {
					console.error('Failed to fetch robot images:', err.message);
					robotImagesError = err.message;
				}
			}

			// Build asset URL — prefer a linked reconstruction GLB, then fall back to uploaded files
			let modelUrl = '';
			if (artefact.ThreeD_model_id) {
				modelUrl = `/archive/assets/reconstruction/${artefact.ThreeD_model_id}/model`;
			} else if (artefact.gltf_file) {
				modelUrl = `/archive/assets/${artefact.gltf_file}`;
			} else if (artefact.obj_file) {
				// Extract file IDs from obj_files relational field
				const relatedFileIds = artefact.obj_files?.map(f => f.directus_files_id).filter(Boolean) || []; // e.g., ["ben-uuid","ben_ks-uuid"]
				if (relatedFileIds.length > 0) {
					modelUrl = `/archive/assets/${artefact.obj_file}?obj_files=${relatedFileIds.join(',')}`; // Result: "/assets/ben-uuid?obj_files=ben-uuid,ben_ks-uuid"
				} else {
					modelUrl = `/archive/assets/${artefact.obj_file}`;
				}
			}

			const content = `
${renderNavbar('collections', true)}

<div class="container mb-5">
    <div class="row mt-3">
        <div class="col-0 col-lg-2"></div>
        <div class="col-12 col-lg-10">
            <nav aria-label="breadcrumb">
                <ol class="breadcrumb">
                    <li class="breadcrumb-item"><a href="https://textailes-eccch.eu/">Home</a></li>
                    <li class="breadcrumb-item"><a href="/archive/collections">Collections</a></li>
                    <li class="breadcrumb-item active" aria-current="page">${artefact.title || 'Artefact'}</li>
                </ol>
            </nav>
        </div>
    </div>

    <div class="row mt-3">
        <div class="col-0 col-lg-2"></div>
        <div class="col-12 col-lg-8">
            ${modelUrl ? `
            <div class="mt-3 mb-4">
                <div id="artefact-model" style="height: 555px;">
                   <model-viewer
                     src="${modelUrl}"
                     camera-controls
                     auto-rotate
                     environment-image="legacy"
                     exposure="1.2"
                     shadow-intensity="0.5"
                     tone-mapping="commerce"
                     style="width:100%; height:100%;">
                   </model-viewer>
                </div>
            </div>

            <div class="row mt-4">
                <div class="col-12 mb-3 d-flex justify-content-end gap-2">
                    <button onclick="annotateWithThoth(${artefact.id})" class="btn btn-red">
                        <i class="fas fa-edit"></i> Annotate with THOTH
                    </button>
                    <button type="button" class="btn btn-red" data-bs-toggle="modal" data-bs-target="#threadSimulationModal">
                        <i class="fas fa-layer-group"></i> Thread Simulation
                    </button>
                    <button type="button" class="btn btn-red" data-bs-toggle="modal" data-bs-target="#patchSimulationModal">
                        <i class="fas fa-th"></i> Patch Simulation
                    </button>
                </div>
            </div>

            <!-- Physics metadata + prefill helper. Must appear BEFORE the
                 modal HTML so HestiaMetaPrefill is defined when each modal's
                 inline IIFE runs. Metadata is empty today; extend
                 extractPhysicsMetadata() in the server route to populate. -->
            <script>window.HESTIA_PHYSICS_METADATA = ${JSON.stringify(extractPhysicsMetadata(artefact))};</script>
            ${renderSimulationMetadataPrefillScript()}

            ${renderThreadSimulationModal()}
            ${renderPatchSimulationModal()}

            <!-- Thread Simulation Visualization (renders when ?thread_simulation=<id> is set or a recent submission exists in sessionStorage) -->
            <div id="threadVisualizationSection" class="mt-4" style="display:none;">
                <div class="d-flex justify-content-between align-items-center border-bottom pb-2">
                    <h4 class="mb-0">Thread Simulation Result</h4>
                    <a id="threadDownloadBtn" href="#" class="btn btn-sm btn-outline-secondary" style="display:none;" download>
                        <i class="fas fa-download"></i> Download
                    </a>
                </div>
                <div class="text-muted small mt-2 mb-2">
                    Simulation ID: <code id="threadVisualizationSimId">—</code>
                    <span id="threadVisualizationStatus" class="ms-2"></span>
                </div>
                <div id="threadVisualizationViewer" style="height: 500px;"></div>
                <div id="threadForceElongationWrapper" class="mt-3" style="display:none;">
                    <h6 class="text-muted mb-2">Force-Elongation Diagram</h6>
                    <div style="position:relative; height:320px;">
                        <canvas id="threadForceElongationChart"></canvas>
                    </div>
                </div>
            </div>

            <script>
                (function () {
                    const ARTEFACT_ID = ${artefact.id};
                    const params = new URLSearchParams(window.location.search);
                    const simId = params.get('thread_simulation')
                        || sessionStorage.getItem('thread_simulation_' + ARTEFACT_ID);
                    if (!simId) return;

                    const section = document.getElementById('threadVisualizationSection');
                    const idEl = document.getElementById('threadVisualizationSimId');
                    const statusEl = document.getElementById('threadVisualizationStatus');
                    const viewerEl = document.getElementById('threadVisualizationViewer');
                    const dlBtn = document.getElementById('threadDownloadBtn');
                    section.style.display = '';
                    idEl.textContent = simId;
                    dlBtn.href = '/archive/dynamo/thread-simulations/' + encodeURIComponent(simId) + '/download.zip';
                    dlBtn.style.display = '';

                    function setStatus(html) { statusEl.innerHTML = html; }
                    function showSpinner(msg) {
                        setStatus('<i class="fas fa-spinner fa-spin"></i> ' + msg);
                        viewerEl.innerHTML = '';
                    }
                    function showError(msg) {
                        setStatus('<span class="text-danger">' + msg + '</span>');
                    }
                    function renderViewer() {
                        const url = '/archive/assets/thread-simulation/' + encodeURIComponent(simId) + '/visualization.glb';
                        viewerEl.innerHTML = ''
                            + '<model-viewer src="' + url + '"'
                            + ' camera-controls autoplay animation-name="deformation"'
                            + ' environment-image="legacy" exposure="1.2" shadow-intensity="0.5"'
                            + ' tone-mapping="commerce" style="width:100%; height:100%;"></model-viewer>';
                        setStatus('<span class="text-success">Ready</span>');
                    }

                    function renderForceElongationChart(out) {
                        const elongations = (out.elongations && out.elongations.value) || [];
                        const forces = (out.forces && out.forces.value) || [];
                        if (!elongations.length || !forces.length || elongations.length !== forces.length) return;
                        if (typeof Chart === 'undefined') {
                            console.warn('Chart.js not loaded; skipping Force-Elongation chart.');
                            return;
                        }
                        const elongationUnit = (out.elongations && out.elongations.unit) || '%';
                        const forceUnit = (out.forces && out.forces.unit) || 'N';
                        const points = elongations.map((e, i) => ({ x: e, y: forces[i] }));

                        document.getElementById('threadForceElongationWrapper').style.display = '';
                        const ctx = document.getElementById('threadForceElongationChart').getContext('2d');
                        new Chart(ctx, {
                            type: 'line',
                            data: {
                                datasets: [{
                                    label: 'Force',
                                    data: points,
                                    borderColor: '#d62728',
                                    backgroundColor: 'rgba(214, 39, 40, 0.1)',
                                    borderWidth: 2,
                                    pointRadius: 3,
                                    pointHoverRadius: 5,
                                    tension: 0.2,
                                    fill: false,
                                }]
                            },
                            options: {
                                responsive: true,
                                maintainAspectRatio: false,
                                plugins: {
                                    legend: { display: false },
                                    tooltip: {
                                        callbacks: {
                                            label: (c) => 'Force: ' + c.parsed.y.toFixed(3) + ' ' + forceUnit
                                                + ' at elongation ' + c.parsed.x.toFixed(3) + ' ' + elongationUnit
                                        }
                                    }
                                },
                                scales: {
                                    x: { type: 'linear', title: { display: true, text: 'Elongation (' + elongationUnit + ')' } },
                                    y: { title: { display: true, text: 'Force (' + forceUnit + ')' } }
                                }
                            }
                        });
                    }

                    showSpinner('Checking simulation status...');
                    // cache-bust so a stale 304 from before the simulator
                    // patched the output doesn't make us think it's still pending.
                    fetch('/archive/dynamo/thread-simulations/' + encodeURIComponent(simId) + '?_=' + Date.now(),
                        { cache: 'no-store' })
                        .then(r => r.json().then(d => ({ ok: r.ok, data: d })))
                        .then(({ ok, data }) => {
                            if (!ok) {
                                showError('Could not load simulation: ' + (data.error || 'unknown error'));
                                return;
                            }
                            const out = data.simulationOutput;
                            const files = (out && out.visualizationFiles) || [];
                            if (out && out.simulationCompleted && files.length > 0) {
                                renderViewer();
                                renderForceElongationChart(out);
                            } else if (out && out.simulationCompleted) {
                                showError('Simulation completed but produced no visualization files.');
                                renderForceElongationChart(out);
                            } else {
                                setStatus('<i class="fas fa-hourglass-half"></i> Simulation pending — reload when ready.');
                            }
                        })
                        .catch(err => showError('Network error: ' + err.message));
                })();
            </script>

            <!-- Patch Simulation Visualization (renders when ?patch_simulation=<id> is set or a recent submission exists in sessionStorage) -->
            <div id="patchVisualizationSection" class="mt-4" style="display:none;">
                <div class="d-flex justify-content-between align-items-center border-bottom pb-2">
                    <h4 class="mb-0">Patch Simulation Result</h4>
                    <a id="patchDownloadBtn" href="#" class="btn btn-sm btn-outline-secondary" style="display:none;" download>
                        <i class="fas fa-download"></i> Download
                    </a>
                </div>
                <div class="text-muted small mt-2 mb-2">
                    Simulation ID: <code id="patchVisualizationSimId">—</code>
                    <span id="patchVisualizationStatus" class="ms-2"></span>
                </div>
                <div class="d-flex align-items-center gap-2 mb-2">
                    <label for="patchExperimentSelect" class="form-label mb-0 small">Experiment:</label>
                    <select id="patchExperimentSelect" class="form-select form-select-sm" style="width:auto;">
                        <option value="inplane11">In-plane 11 (warp tension)</option>
                        <option value="inplane22">In-plane 22 (weft tension)</option>
                        <option value="inplane12">In-plane 12 (shear)</option>
                        <option value="bending11">Bending 11 (warp bend)</option>
                        <option value="bending22">Bending 22 (weft bend)</option>
                        <option value="bending12">Bending 12 (torsion)</option>
                    </select>
                </div>
                <div id="patchVisualizationViewer" style="height: 500px;"></div>
            </div>

            <script>
                (function () {
                    const ARTEFACT_ID = ${artefact.id};
                    const params = new URLSearchParams(window.location.search);
                    const simId = params.get('patch_simulation')
                        || sessionStorage.getItem('patch_simulation_' + ARTEFACT_ID);
                    if (!simId) return;

                    const section = document.getElementById('patchVisualizationSection');
                    const idEl = document.getElementById('patchVisualizationSimId');
                    const statusEl = document.getElementById('patchVisualizationStatus');
                    const viewerEl = document.getElementById('patchVisualizationViewer');
                    const selectEl = document.getElementById('patchExperimentSelect');
                    const dlBtn = document.getElementById('patchDownloadBtn');
                    section.style.display = '';
                    idEl.textContent = simId;
                    dlBtn.href = '/archive/dynamo/patch-simulations/' + encodeURIComponent(simId) + '/download.zip';
                    dlBtn.style.display = '';

                    function setStatus(html) { statusEl.innerHTML = html; }
                    function showError(msg) { setStatus('<span class="text-danger">' + msg + '</span>'); }

                    // Maps dropdown key → JSON field on simulationOutput.
                    const expToField = {
                        inplane11: 'visualizationFiles_inplane11',
                        inplane22: 'visualizationFiles_inplane22',
                        inplane12: 'visualizationFiles_inplane12',
                        bending11: 'visualizationFiles_bending11',
                        bending22: 'visualizationFiles_bending22',
                        bending12: 'visualizationFiles_bending12',
                    };
                    const experimentsReady = {};

                    function renderViewer(experiment) {
                        if (!experimentsReady[experiment]) {
                            viewerEl.innerHTML = '<div class="text-muted">No frames available for ' + experiment + '.</div>';
                            setStatus('<span class="text-warning">Empty: ' + experiment + '</span>');
                            return;
                        }
                        const url = '/archive/assets/patch-simulation/' + encodeURIComponent(simId)
                            + '/visualization/' + encodeURIComponent(experiment) + '.glb';
                        viewerEl.innerHTML = ''
                            + '<model-viewer src="' + url + '"'
                            + ' camera-controls autoplay animation-name="deformation"'
                            + ' environment-image="legacy" exposure="1.2" shadow-intensity="0.5"'
                            + ' tone-mapping="commerce" style="width:100%; height:100%;"></model-viewer>';
                        setStatus('<span class="text-success">Showing: ' + experiment + '</span>');
                    }

                    selectEl.addEventListener('change', (e) => renderViewer(e.target.value));

                    setStatus('<i class="fas fa-spinner fa-spin"></i> Checking simulation status…');
                    fetch('/archive/dynamo/patch-simulations/' + encodeURIComponent(simId) + '?_=' + Date.now(),
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
                                showError('Simulation completed but produced no visualization files.');
                                return;
                            }
                            const firstReady = Object.keys(expToField).find(e => experimentsReady[e]);
                            if (firstReady) {
                                selectEl.value = firstReady;
                                renderViewer(firstReady);
                            }
                        })
                        .catch(err => showError('Network error: ' + err.message));
                })();
            </script>

            <script>
                /**
                 * Annotate with THOTH - Main function
                 *
                 * This function:
                 * 1. Shows loading spinner on button
                 * 2. Calls API endpoint to get or create ATON scene
                 * 3. Opens THOTH annotator in new tab with the scene
                 */
                async function annotateWithThoth(artefactId) {
                    const btn = event.target.closest('button');
                    const originalText = btn.innerHTML;
                    btn.disabled = true;
                    btn.innerHTML = '<i class="fas fa-spinner fa-spin"></i> Preparing scene...';

                    try {
                        // Call endpoint that gets existing scene or creates new one
                        const response = await fetch('/archive/aton/scene/' + artefactId + '/url');
                        const data = await response.json();

                        if (data.success) {
                            console.log('Scene data:', data);
                            // Open THOTH with the scene
                            window.open(data.sceneUrl, '_blank');
                        } else {
                            alert('Error: ' + (data.message || 'Failed to prepare scene'));
                        }
                    } catch (error) {
                        alert('Error: ' + error.message);
                    } finally {
                        btn.disabled = false;
                        btn.innerHTML = originalText;
                    }
                }
            </script>
            ` : robotImages.length > 0 ? `
            <div class="mt-3 mb-4">
                <!-- <h5 class="mb-3">Robot Scan Images</h5> -->
                ${robotImagesError ? `<p class="text-danger small mt-2">DB error: ${robotImagesError}</p>` : ''}
                <div class="row g-2">
                    ${robotImages.map(img => {
                        const encodedFilename = encodeURIComponent(img.filename);
                        const url = `/archive/assets/robot-image/${img.scan_id}/${encodedFilename}`;
                        return `
                    <div class="col-6 col-md-3 col-lg-2">
                        <a href="${url}" target="_blank">
                            <img
                                src="${url}"
                                alt="${img.filename}"
                                class="img-fluid rounded"
                                style="width:100%; height:120px; object-fit:cover;"
                                loading="lazy">
                        </a>
                    </div>`;
                    }).join('\n')}
                </div>
            </div>
            ` : ''}

            <div class="row mt-4">
                <!-- Heritage Asset Section -->
                <div class="col-12 mb-4">
                    <h4 class="border-bottom pb-2">Heritage Asset</h4>
                    <div class="row">
                        <div class="col-md-6">
                            <dl>
                                <dt class="samewidth">Title:</dt>
                                <dd>${artefact.title || 'N/A'}</dd>
                                
                                <dt class="samewidth">Description:</dt>
                                <dd>${artefact.description || 'N/A'}</dd>
                                
                                <dt class="samewidth">Date - Timespan:</dt>
                                <dd>${artefact.date_timespan || 'N/A'}</dd>

                                <dt class="samewidth">Dimensions:</dt>
                                <dd>${artefact.dimensions || 'N/A'}</dd>

                                <dt class="samewidth">Heritage Asset Owner:</dt>
                                <dd>${artefact.owner || 'N/A'}</dd>

                                <dt class="samewidth">Category of Textile:</dt>
                                <dd>${artefact.textile_category || 'N/A'}</dd>

                                <dt class="samewidth">Keywords:</dt>
                                <dd>${artefact.keywords || 'N/A'}</dd>

                                <dt class="samewidth">Inventory Number:</dt>
                                <dd>${artefact.inventory_number || 'N/A'}</dd>

                                <dt class="samewidth">Origin:</dt>
                                <dd>${artefact.origin || 'N/A'}</dd>
                            </dl>
                        </div>
                    </div>
                </div>

                <!-- Digital Asset Section -->
                <div class="col-12 mb-4">
                    <h4 class="border-bottom pb-2">Digital Asset</h4>
                    <div class="row">
                        <div class="col-md-6">
                            <dl>
                                <dt class="samewidth">Digitization methods:</dt>
                                <dd>${artefact.digitization_methods || 'N/A'}</dd>
                                
                                <dt class="samewidth">Digitization Actor:</dt>
                                <dd>${artefact.digitization_actor || 'N/A'}</dd>
                                
                                <dt class="samewidth">Resolution:</dt>
                                <dd>${artefact.resolution || 'N/A'}</dd>
                            </dl>
                        </div>
                    </div>
                </div>

                <!-- Additional Information (toggle only) -->
                <div class="col-12 mb-4">
                    <div class="d-flex align-items-center justify-content-end border-bottom pb-2"
                        role="button"
                        data-bs-toggle="collapse"
                        data-bs-target="#additionalInfoCollapse"
                        aria-expanded="false"
                        aria-controls="additionalInfoCollapse">
                    <div class="d-flex align-items-center">
                        <h4 class="mb-0 fs-6"><em>Additional Information</em></h4>
                        <i class="fas fa-chevron-down ms-1 rotate-on-open"></i>
                    </div>
                </div>

                <!-- Collapsible content (everything hidden until click) -->
                <div class="collapse mt-3" id="additionalInfoCollapse">

                <!-- Identification Section -->
                <div class="col-12 mb-4">
                    <h4 class="border-bottom pb-2">Identification</h4>
                    <div class="row">
                        <div class="col-md-6">
                            <dl>
                                <dt class="samewidth">Title:</dt>
                                <dd>${artefact.title || 'N/A'}</dd>
                                
                                <dt class="samewidth">ID:</dt>
                                <dd>${artefact.id || 'N/A'}</dd>
                                
                                <dt class="samewidth">Accession Number:</dt>
                                <dd>${artefact.accession_number || 'N/A'}</dd>
                            </dl>
                        </div>
                        <div class="col-md-6">
                            <dl>
                                <dt class="samewidth">Reference Name/Number:</dt>
                                <dd>${artefact.reference_name_number || 'N/A'}</dd>
                                
                                <dt class="samewidth">Material Analyzed:</dt>
                                <dd>${artefact.material_analyzed || 'N/A'}</dd>
                            </dl>
                        </div>
                    </div>
                </div>

                <!-- Condition Section -->
                <div class="col-12 mb-4">
                    <h4 class="border-bottom pb-2">Condition</h4>
                    <div class="row">
                        <div class="col-md-6">
                            <dl>
                                <dt class="samewidth">Object Status:</dt>
                                <dd>${artefact.object_status || 'N/A'}</dd>
                                
                                <dt class="samewidth">Condition Assessment:</dt>
                                <dd>${artefact.condition_assessment || 'N/A'}</dd>
                            </dl>
                        </div>
                        <div class="col-md-6">
                            <dl>
                                <dt class="samewidth">State of Preservation:</dt>
                                <dd>${artefact.state_of_preservation || 'N/A'}</dd>
                                
                                <dt class="samewidth">Type of Preservation:</dt>
                                <dd>${artefact.type_of_preservation || 'N/A'}</dd>
                            </dl>
                        </div>
                    </div>
                </div>

                <!-- Preventive Conservation Section -->
                <div class="col-12 mb-4">
                    <h4 class="border-bottom pb-2">Preventive Conservation</h4>
                    <div class="row">
                        <div class="col-md-6">
                            <dl>
                                <dt class="samewidth">Temperature:</dt>
                                <dd>${artefact.temperature ? artefact.temperature + '°C' : 'N/A'}</dd>
                                
                                <dt class="samewidth">Humidity:</dt>
                                <dd>${artefact.humidity ? artefact.humidity + '% RH' : 'N/A'}</dd>
                                
                                <dt class="samewidth">Type of Container:</dt>
                                <dd>${artefact.type_of_container || 'N/A'}</dd>
                            </dl>
                        </div>
                        <div class="col-md-6">
                            <dl>
                                <dt class="samewidth">Mount:</dt>
                                <dd>${artefact.mount || 'N/A'}</dd>
                                
                                <dt class="samewidth">Result:</dt>
                                <dd>${artefact.result ? `<a href="${artefact.result}" target="_blank">View</a>` : 'N/A'}</dd>
                            </dl>
                        </div>
                    </div>
                </div>

                <!-- Interventive Conservation Section -->
                <div class="col-12 mb-4">
                    <h4 class="border-bottom pb-2">Interventive Conservation</h4>
                    <div class="row">
                        <div class="col-md-6">
                            <dl>
                                <dt class="samewidth">Conservation Date:</dt>
                                <dd>${artefact.conservation_date || 'N/A'}</dd>
                                
                                <dt class="samewidth">Cleaning:</dt>
                                <dd>${artefact.cleaning === true ? 'Yes' : artefact.cleaning === false ? 'No' : 'N/A'}</dd>
                            </dl>
                        </div>
                        <div class="col-md-6">
                            <dl>
                                <dt class="samewidth">Foreign Material Introduced:</dt>
                                <dd>${artefact.introduction_of_foreign_material || 'N/A'}</dd>
                                
                                <dt class="samewidth">Specific Foreign Material:</dt>
                                <dd>${artefact.specific_foreign_material_introduce || 'N/A'}</dd>
                            </dl>
                        </div>
                    </div>
                </div>

                <!-- Additional Information Section -->
                <div class="col-12 mb-4">
                    <h4 class="border-bottom pb-2">Additional Information</h4>
                    <div class="row">
                        <div class="col-md-6">
                            <dl>
                                <dt class="samewidth">Use Case:</dt>
                                <dd>${artefact.use_case || 'N/A'}</dd>
                                
                                <dt class="samewidth">Collection:</dt>
                                <dd>${artefact.collection || 'N/A'}</dd>
                                
                                <dt class="samewidth">Time Period:</dt>
                                <dd>${artefact.time_period || 'N/A'}</dd>
                            </dl>
                        </div>
                        <div class="col-md-6">
                            <dl>
                                <dt class="samewidth">Creator:</dt>
                                <dd>${artefact.creator || 'N/A'}</dd>
                                
                                <dt class="samewidth">Sensor:</dt>
                                <dd>${artefact.sensor || 'N/A'}</dd>
                                
                                <dt class="samewidth">Location:</dt>
                                <dd>${artefact.location || 'N/A'}</dd>
                            </dl>
                        </div>
                    </div>
                </div>
            </div>
            
            <p class="mt-3"><a href="/archive/collections">← Back to Collections</a></p>
        </div>
        <div class="col-0 col-lg-2"></div>
    </div>
</div>

${renderFooter()}`;

			const html = renderHtmlPage({
				title: `${artefact.title || 'Artefact'} - Digital TEXTaiLES Archive`,
				content,
				includeModelViewer: true,
				includeChartJs: true,
				bodyClass: 'id="artefact" tabindex="0"',
				cspPolicy: CSP_POLICY
			});
			res.send(html);
		} catch (error) {
			console.error('Artefact view error:', error);
			res.status(500).send('Error: ' + error.message);
		}
	});
};
