import { CSP_POLICY } from '../utils/constants.js';
import { userIsAuthenticated, userHasPermission, userIsAdmin } from '../utils/auth.js';
import { renderLoginPage } from '../templates/login.js';
import { render401Page } from '../templates/error.js';
import { renderNavbar } from '../templates/navbar.js';
import { renderHtmlPage, renderFooter } from '../templates/layout.js';

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
					// 1. Conservation — Identification
					'accession_number', 'reference_name_number', 'material_analyzed',
					// 1. Conservation — Condition
					'object_status', 'condition_assessment', 'state_of_preservation', 'type_of_preservation',
					// 1. Conservation — Preventive conservation
					'temperature', 'humidity', 'type_of_container', 'mount', 'result',
					// 1. Conservation — Interventive conservation
					'yes_no', 'conservation_date', 'cleaning', 'introduction_of_foreign_material', 'specific_foreign_material_introduce',
					// 2. Analysis — Identification
					'material_analyzed_analysis',
					// 2. Analysis — Non-destructive analysis
					'method_of_analysis', 'type_of_instrument', 'aim', 'result_analysis',
					// 2. Analysis — Destructive analysis
					'sample_material', 'area_of_sample_on_object', 'size_of_samples', 'sample_weight', 'method_of_destructive_analysis', 'type_of_destructive_instrument', 'aim_destructive', 'result_destructive',
					// 3. Documentation — Identification
					'current_location_doc', 'external_link_doc', 'origin_doc','location_doc', 'provenance_doc',
					'belongs_to_a_group_doc', 'belongs_to_a_subgroup', 'documented_by', 'date_of_upload_doc',
					'type_of_object_doc', 'specific_type_of_object', 'maximum_dimensions_doc', 'date_period_doc', 'publication_doc',
					// 3. Documentation — Technological analysis — Primary structure
					'material_pr_structure', 'type_of_weave_pr_structure', 'type_of_weave_specification_pr_structure', 'weave_count_pr_structure',
					'thread_diameter_pr_structure', 'thread_ply_pr_structure', 'thread_twist_pr_structure', 'twist_angle_pr_structure', 'twist_method_pr_structure',
					'thread_diameter_pr_structure_b', 'thread_ply_pr_structure_b', 'thread_twist_pr_structure_b', 'twist_angle_pr_structure_b', 'twist_method_pr_structure_b',
					// 3. Documentation — Technological analysis — Decoration
					'type_of_decoration', 'decorations_specifics', 'decoration_material', 'decoration_thread_diameter', 'decoration_thread_specific',
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
                    <button onclick="window.open('https://thoth.textailes.athenarc.gr/a/thoth/?id=speveri', '_blank')" class="btn btn-red">
                        <i class="fas fa-edit"></i> Annotate with THOTH
                    </button>
                    <!-- <button onclick="location.href='#'" class="btn btn-red">
                        <i class="fas fa-magnifying-glass"></i> Button 2
                    </button>
                    <button onclick="location.href='#'" class="btn btn-red">
                        <i class="fas fa-layer-group"></i> Button 3
                    </button> -->
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

                <!-- =========================================================
                     CONSERVATION
                     ========================================================= -->
                <div class="col-12 mb-4">
                    <h2 class="border-bottom pb-2 mt-2 fw-bold text-uppercase">CONSERVATION</h2>

                    <!-- Identification -->
                    <h5 class="mt-4">Identification</h5>
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

                    <!-- Condition -->
                    <h5 class="mt-4">Condition</h5>
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

                    <!-- Preventive Conservation -->
                    <h5 class="mt-4">Preventive Conservation</h5>
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

                    <!-- Interventive Conservation -->
                    <h5 class="mt-4">Interventive Conservation</h5>
                    <div class="row">
                        <div class="col-md-6">
                            <dl>
                                <dt class="samewidth">Interventive Conservation:</dt>
                                <dd>${artefact.yes_no || 'N/A'}</dd>

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

                <!-- =========================================================
                     ANALYSIS
                     ========================================================= -->
                <div class="col-12 mb-4">
                    <h2 class="border-bottom pb-2 mt-2 fw-bold text-uppercase">ANALYSIS</h2>

                    <!-- Identification -->
                    <h5 class="mt-4">Identification</h5>
                    <div class="row">
                        <div class="col-md-6">
                            <dl>
                                <dt class="samewidth">Accession Number:</dt>
                                <dd>${artefact.accession_number || 'N/A'}</dd>

                                <dt class="samewidth">Reference Name/Number:</dt>
                                <dd>${artefact.reference_name_number || 'N/A'}</dd>
                            </dl>
                        </div>
                        <div class="col-md-6">
                            <dl>
                                <dt class="samewidth">Material Analyzed:</dt>
                                <dd>${artefact.material_analyzed_analysis || 'N/A'}</dd>
                            </dl>
                        </div>
                    </div>

                    <!-- Non-destructive Analysis -->
                    <h5 class="mt-4">Non-destructive Analysis</h5>
                    <div class="row">
                        <div class="col-md-6">
                            <dl>
                                <dt class="samewidth">Method of Analysis:</dt>
                                <dd>${artefact.method_of_analysis || 'N/A'}</dd>

                                <dt class="samewidth">Type of Instrument:</dt>
                                <dd>${artefact.type_of_instrument || 'N/A'}</dd>
                            </dl>
                        </div>
                        <div class="col-md-6">
                            <dl>
                                <dt class="samewidth">Aim:</dt>
                                <dd>${artefact.aim || 'N/A'}</dd>

                                <dt class="samewidth">Result:</dt>
                                <dd>${artefact.result_analysis ? `<a href="${artefact.result_analysis}" target="_blank">View</a>` : 'N/A'}</dd>
                            </dl>
                        </div>
                    </div>

                    <!-- Destructive Analysis -->
                    <h5 class="mt-4">Destructive Analysis</h5>
                    <div class="row">
                        <div class="col-md-6">
                            <dl>
                                <dt class="samewidth">Sample Material:</dt>
                                <dd>${artefact.sample_material || 'N/A'}</dd>

                                <dt class="samewidth">Area of Sample on Object:</dt>
                                <dd>${artefact.area_of_sample_on_object || 'N/A'}</dd>

                                <dt class="samewidth">Size of Samples:</dt>
                                <dd>${artefact.size_of_samples || 'N/A'}</dd>

                                <dt class="samewidth">Sample Weight:</dt>
                                <dd>${artefact.sample_weight || 'N/A'}</dd>
                            </dl>
                        </div>
                        <div class="col-md-6">
                            <dl>
                                <dt class="samewidth">Method of Analysis:</dt>
                                <dd>${artefact.method_of_destructive_analysis || 'N/A'}</dd>

                                <dt class="samewidth">Type of Instrument:</dt>
                                <dd>${artefact.type_of_destructive_instrument || 'N/A'}</dd>

                                <dt class="samewidth">Aim:</dt>
                                <dd>${artefact.aim_destructive || 'N/A'}</dd>

                                <dt class="samewidth">Result:</dt>
                                <dd>${artefact.result_destructive ? `<a href="${artefact.result_destructive}" target="_blank">View</a>` : 'N/A'}</dd>
                            </dl>
                        </div>
                    </div>
                </div>

                <!-- =========================================================
                     DOCUMENTATION
                     ========================================================= -->
                <div class="col-12 mb-4">
                    <h2 class="border-bottom pb-2 mt-2 fw-bold text-uppercase">DOCUMENTATION</h2>

                    <!-- Identification -->
                    <h5 class="mt-4">Identification</h5>
                    <div class="row">
                        <div class="col-md-6">
                            <dl>
                                <dt class="samewidth">Accession Number:</dt>
                                <dd>${artefact.accession_number || 'N/A'}</dd>

                                <dt class="samewidth">Reference Name/Number:</dt>
                                <dd>${artefact.reference_name_number || 'N/A'}</dd>

                                <dt class="samewidth">Current Location:</dt>
                                <dd>${artefact.current_location_doc || 'N/A'}</dd>

                                <dt class="samewidth">External Link:</dt>
                                <dd>${artefact.external_link_doc ? `<a href="${artefact.external_link_doc}" target="_blank">${artefact.external_link_doc}</a>` : 'N/A'}</dd>

                                <dt class="samewidth">Origin:</dt>
                                <dd>${artefact.origin_doc || 'N/A'}</dd>

                                <dt class="samewidth">Location:</dt>
                                <dd>${artefact.location_doc || 'N/A'}</dd>

                                <dt class="samewidth">Provenance:</dt>
                                <dd>${artefact.provenance_doc || 'N/A'}</dd>

                                <dt class="samewidth">Group:</dt>
                                <dd>${artefact.belongs_to_a_group_doc || 'N/A'}</dd>
                            </dl>
                        </div>
                        <div class="col-md-6">
                            <dl>
                                <dt class="samewidth">Subgroup:</dt>
                                <dd>${artefact.belongs_to_a_subgroup || 'N/A'}</dd>

                                <dt class="samewidth">Documented By:</dt>
                                <dd>${artefact.documented_by || 'N/A'}</dd>

                                <dt class="samewidth">Date of Upload:</dt>
                                <dd>${artefact.date_of_upload_doc || 'N/A'}</dd>

                                <dt class="samewidth">Type of Object:</dt>
                                <dd>${artefact.type_of_object_doc || 'N/A'}</dd>

                                <dt class="samewidth">Specific Type of Object:</dt>
                                <dd>${artefact.specific_type_of_object || 'N/A'}</dd>

                                <dt class="samewidth">Maximum Dimensions:</dt>
                                <dd>${artefact.maximum_dimensions_doc || 'N/A'}</dd>

                                <dt class="samewidth">Date - Period:</dt>
                                <dd>${artefact.date_period_doc || 'N/A'}</dd>
                            </dl>
                        </div>
                    </div>
                    <div class="row">
                        <div class="col-12">
                            <dl>
                                <dt class="samewidth">Publication:</dt>
                                <dd style="white-space: pre-wrap;">${artefact.publication_doc || 'N/A'}</dd>
                            </dl>
                        </div>
                    </div>

                    <!-- Technological Analysis -->
                    <h5 class="mt-4">Technological Analysis</h5>

                    <!-- Primary Structure -->
                    <h6 class="mt-3 ms-3"><em>Primary Structure</em></h6>
                    <div class="row ms-1">
                        <div class="col-md-6">
                            <dl>
                                <dt class="samewidth">Material:</dt>
                                <dd>${artefact.material_pr_structure || 'N/A'}</dd>

                                <dt class="samewidth">Type of Weave:</dt>
                                <dd>${artefact.type_of_weave_pr_structure || 'N/A'}</dd>

                                <dt class="samewidth">Type of Weave Specification:</dt>
                                <dd>${artefact.type_of_weave_specification_pr_structure || 'N/A'}</dd>

                                <dt class="samewidth">Weave Count:</dt>
                                <dd>${artefact.weave_count_pr_structure || 'N/A'}</dd>
                            </dl>
                            <p class="mb-1"><strong>Thread A</strong></p>
                            <dl>
                                <dt class="samewidth">Diameter:</dt>
                                <dd>${artefact.thread_diameter_pr_structure || 'N/A'}</dd>

                                <dt class="samewidth">Ply:</dt>
                                <dd>${artefact.thread_ply_pr_structure || 'N/A'}</dd>

                                <dt class="samewidth">Twist:</dt>
                                <dd>${artefact.thread_twist_pr_structure || 'N/A'}</dd>

                                <dt class="samewidth">Twist Angle:</dt>
                                <dd>${artefact.twist_angle_pr_structure || 'N/A'}</dd>

                                <dt class="samewidth">Twist Method:</dt>
                                <dd>${artefact.twist_method_pr_structure || 'N/A'}</dd>
                            </dl>
                        </div>
                        <div class="col-md-6">
                            <p class="mb-1"><strong>Thread B</strong></p>
                            <dl>
                                <dt class="samewidth">Diameter:</dt>
                                <dd>${artefact.thread_diameter_pr_structure_b || 'N/A'}</dd>

                                <dt class="samewidth">Ply:</dt>
                                <dd>${artefact.thread_ply_pr_structure_b || 'N/A'}</dd>

                                <dt class="samewidth">Twist:</dt>
                                <dd>${artefact.thread_twist_pr_structure_b || 'N/A'}</dd>

                                <dt class="samewidth">Twist Angle:</dt>
                                <dd>${artefact.twist_angle_pr_structure_b || 'N/A'}</dd>

                                <dt class="samewidth">Twist Method:</dt>
                                <dd>${artefact.twist_method_pr_structure_b || 'N/A'}</dd>
                            </dl>
                        </div>
                    </div>

                    <!-- Decoration -->
                    <h6 class="mt-3 ms-3"><em>Decoration</em></h6>
                    <div class="row ms-1">
                        <div class="col-md-6">
                            <dl>
                                <dt class="samewidth">Type of Decoration:</dt>
                                <dd>${artefact.type_of_decoration || 'N/A'}</dd>

                                <dt class="samewidth">Decoration Specifics:</dt>
                                <dd>${artefact.decorations_specifics || 'N/A'}</dd>

                                <dt class="samewidth">Decoration Material:</dt>
                                <dd>${artefact.decoration_material || 'N/A'}</dd>
                            </dl>
                        </div>
                        <div class="col-md-6">
                            <dl>
                                <dt class="samewidth">Decoration Thread Diameter:</dt>
                                <dd>${artefact.decoration_thread_diameter || 'N/A'}</dd>

                                <dt class="samewidth">Decoration Thread Specific:</dt>
                                <dd>${artefact.decoration_thread_specific || 'N/A'}</dd>
                            </dl>
                        </div>
                    </div>
                </div>

                <!-- Legacy Additional Information -->
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
