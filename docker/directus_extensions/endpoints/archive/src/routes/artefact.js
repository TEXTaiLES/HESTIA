import { CSP_POLICY, ATON_CONFIG } from '../utils/constants.js';
import { userIsAuthenticated, userHasPermission } from '../utils/auth.js';
import { renderLoginPage } from '../templates/login.js';
import { render401Page } from '../templates/error.js';
import { renderNavbar } from '../templates/navbar.js';
import { renderHtmlPage, renderFooter } from '../templates/layout.js';
import { getAtonScene } from '../utils/helpers.js';

export default (router, { services }) => {
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

            // Build the Artefact page.
			const artefactsService = new ItemsService('artefacts', {
				schema: req.schema,
				accountability: null,
			});

            // Fetches artefact from database by ID
			const artefacts = await artefactsService.readByQuery({
				fields: [
					'id', 'title', 'gltf_file', 'obj_file', // e.g., "ben-uuid"
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
					'creator', 'sensor', 'location', 'source', 'time_period', 'collection', 'use_case'
				],
				filter: { id: { _eq: req.params.id } },
				limit: 1
			});

			if (!artefacts || artefacts.length === 0) {
				return res.status(404).send('Artefact not found');
			}

			const artefact = artefacts[0];

			// Build asset URL with obj_files parameter if available
			let modelUrl = '';
			if (artefact.gltf_file) {
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

			// Build ATON scene URL for this artefact
			// The URL will be used by the "Annotate with THOTH" button
			const sceneId = `artefact_${artefact.id}`;
			let atonSceneUrl = `${ATON_CONFIG.BASE_URL}${ATON_CONFIG.THOTH_PATH}?s=${sceneId}`;
			
			// Try to check if scene exists in ATON (optional - will use URL anyway)
			try {
				const sceneData = await getAtonScene(ATON_CONFIG.DEFAULT_USER, sceneId);
				if (sceneData) {
					atonSceneUrl = sceneData.sceneUrl;
				}
			} catch (err) {
				// Scene might not exist yet - that's ok, URL will still work or user can create it
				console.log(`Scene ${sceneId} not found in ATON, but URL will still be provided`);
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
            <div class="mt-3 mb-4">
                <div id="artefact-model" style="height: 555px;">
                   <model-viewer
                     src="${modelUrl}"
                     camera-controls
                     auto-rotate
                     environment-image="neutral"
                     exposure="0.7"
                     shadow-intensity="3"
                     tone-mapping="neutral"
                     style="width:100%; height:100%;">
                   </model-viewer>
                </div>
            </div>

            <div class="row mt-4">
                <div class="col-12 text-end mb-3">
                    <div class="feature-card" style="display: inline-block;">
                        <button onclick="annotateWithThoth(${artefact.id})" class="btn btn-primary">
                            <i class="fas fa-edit"></i> Annotate with THOTH
                        </button>
                    </div>
                </div>
            </div>

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
