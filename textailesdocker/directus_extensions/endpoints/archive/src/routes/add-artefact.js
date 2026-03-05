import { CSP_POLICY } from '../utils/constants.js';
import { userIsAuthenticated, hasPermission } from '../utils/auth.js';
import { renderLoginPage } from '../templates/login.js';
import { renderNavbar } from '../templates/navbar.js';
import { renderHtmlPage, renderFooter } from '../templates/layout.js';
import busboy from 'busboy'; // For handling file uploads

export default (router, { services }) => {
	const { AuthenticationService, ItemsService, FilesService } = services;

	// Route to display the "Add New Artefact" form
	router.get('/artefact/add', async (req, res) => {
		try {
			// Set response headers.
			res.set('Content-Type', 'text/html');
			res.set('Content-Security-Policy', CSP_POLICY);

			// If the user is not authenticated, load login page.
			const isAuthenticated = await userIsAuthenticated(req, res, AuthenticationService);
			if (!isAuthenticated) {
				const html = renderLoginPage({
					navbar: 'collections',
					title: 'Add New Artefact',
					subtitle: 'Add artefacts to the Digital TEXTaiLES Archive',
				});
				return res.send(html);
			}
			// Add artefact requires create permission
			const canCreate = await hasPermission(req, res, ItemsService, 'artefacts', 'create');
			if (!canCreate) return res.redirect('/archive/error/401');

			const content = `
${renderNavbar('collections', true)}

<div class="container mb-5">
    <div class="row mt-4 mt-lg-5 pt-lg-2">
        <div class="col-0 col-lg-2"></div>
        <div class="col-12 col-lg-8">
            <h2 class="mb-4 page-title">Add New Artefact</h2>
            
            <div id="alertContainer"></div>

            <form id="artefactForm" enctype="multipart/form-data" lang="en">
                
                <!-- Heritage Asset Section -->
                <div class="card mb-4">
                    <div class="card-header">
                        <h4 class="mb-0">Heritage Asset Information</h4>
                    </div>
                    <div class="card-body">
                        <div class="mb-3">
                            <label for="title" class="form-label">Title <span class="text-danger">*</span></label>
                            <input type="text" class="form-control" id="title" name="title" required>
                        </div>

                        <div class="mb-3">
                            <label for="description" class="form-label">Description</label>
                            <textarea class="form-control" id="description" name="description" rows="4"></textarea>
                        </div>

                        <div class="row">
                            <div class="col-md-6 mb-3">
                                <label for="date_timespan" class="form-label">Date - Timespan</label>
                                <input type="text" class="form-control" id="date_timespan" name="date_timespan">
                            </div>
                            <div class="col-md-6 mb-3">
                                <label for="dimensions" class="form-label">Dimensions</label>
                                <input type="text" class="form-control" id="dimensions" name="dimensions">
                            </div>
                        </div>

                        <div class="row">
                            <div class="col-md-6 mb-3">
                                <label for="owner" class="form-label">Heritage Asset Owner</label>
                                <input type="text" class="form-control" id="owner" name="owner">
                            </div>
                            <div class="col-md-6 mb-3">
                                <label for="textile_category" class="form-label">Category of Textile</label>
                                <input type="text" class="form-control" id="textile_category" name="textile_category">
                            </div>
                        </div>

                        <div class="row">
                            <div class="col-md-6 mb-3">
                                <label for="keywords" class="form-label">Keywords</label>
                                <input type="text" class="form-control" id="keywords" name="keywords" placeholder="Separate with commas">
                            </div>
                            <div class="col-md-6 mb-3">
                                <label for="inventory_number" class="form-label">Inventory Number</label>
                                <input type="text" class="form-control" id="inventory_number" name="inventory_number">
                            </div>
                        </div>

                        <div class="mb-3">
                            <label for="origin" class="form-label">Origin</label>
                            <input type="text" class="form-control" id="origin" name="origin">
                        </div>
                    </div>
                </div>

                <!-- Digital Asset Section -->
                <div class="card mb-4">
                    <div class="card-header">
                        <h4 class="mb-0">Digital Asset Information</h4>
                    </div>
                    <div class="card-body">
                        <div class="mb-3">
                            <div class="mb-3">
                                <label class="form-label" for="gltf_file_input">GLTF File</label>

                                <!-- hidden native input -->
                                <input type="file" id="gltf_file_input" name="gltf_file" accept=".gltf,.glb" class="visually-hidden" />

                                <!-- custom UI -->
                                <div class="input-group">
                                    <button type="button" class="btn btn-outline-secondary" id="gltf_browse_btn"> Browse… </button>
                                    <input type="text" class="form-control" id="gltf_filename" value="No file chosen" readonly>
                                </div>
                                <small class="form-text text-muted">Upload a GLTF or GLB file</small>
                            </div>

                            <div class="mb-3">
                            <label class="form-label" for="obj_file_input">OBJ File</label>

                            <!-- hidden native input -->
                            <input type="file" id="obj_file_input" name="obj_file" accept=".obj" class="visually-hidden" />

                                <!-- custom UI -->
                                <div class="input-group">
                                    <button type="button" class="btn btn-outline-secondary" id="obj_browse_btn"> Browse… </button>
                                    <input type="text" class="form-control" id="obj_filename" value="No file chosen" readonly>
                                </div>
                                <small class="form-text text-muted">Upload an OBJ file</small>
                            </div>

                            <div class="row">
                                <div class="col-md-4 mb-3">
                                    <label for="digitization_methods" class="form-label">Digitization Methods</label>
                                    <input type="text" class="form-control" id="digitization_methods" name="digitization_methods">
                                </div>
                                <div class="col-md-4 mb-3">
                                    <label for="digitization_actor" class="form-label">Digitization Actor</label>
                                    <input type="text" class="form-control" id="digitization_actor" name="digitization_actor">
                                </div>
                                <div class="col-md-4 mb-3">
                                    <label for="resolution" class="form-label">Resolution</label>
                                    <input type="text" class="form-control" id="resolution" name="resolution">
                                </div>
                            </div>
                        </div>
                    </div>
                </div>

                <!-- Identification Section -->
                <div class="card mb-4">
                    <div class="card-header">
                        <h4 class="mb-0">Identification</h4>
                    </div>
                    <div class="card-body">
                        <div class="row">
                            <div class="col-md-6 mb-3">
                                <label for="accession_number" class="form-label">Accession Number</label>
                                <input type="text" class="form-control" id="accession_number" name="accession_number">
                            </div>
                            <div class="col-md-6 mb-3">
                                <label for="reference_name_number" class="form-label">Reference Name/Number</label>
                                <input type="text" class="form-control" id="reference_name_number" name="reference_name_number">
                            </div>
                        </div>
                        <div class="mb-3">
                            <label for="material_analyzed" class="form-label">Material Analyzed</label>
                            <input type="text" class="form-control" id="material_analyzed" name="material_analyzed">
                        </div>
                    </div>
                </div>

                <!-- Condition Section -->
                <div class="card mb-4">
                    <div class="card-header">
                        <h4 class="mb-0">Condition</h4>
                    </div>
                    <div class="card-body">
                        <div class="row">
                            <div class="col-md-6 mb-3">
                                <label for="object_status" class="form-label">Object Status</label>
                                <input type="text" class="form-control" id="object_status" name="object_status">
                            </div>
                            <div class="col-md-6 mb-3">
                                <label for="condition_assessment" class="form-label">Condition Assessment</label>
                                <input type="text" class="form-control" id="condition_assessment" name="condition_assessment">
                            </div>
                        </div>
                        <div class="row">
                            <div class="col-md-6 mb-3">
                                <label for="state_of_preservation" class="form-label">State of Preservation</label>
                                <input type="text" class="form-control" id="state_of_preservation" name="state_of_preservation">
                            </div>
                            <div class="col-md-6 mb-3">
                                <label for="type_of_preservation" class="form-label">Type of Preservation</label>
                                <input type="text" class="form-control" id="type_of_preservation" name="type_of_preservation">
                            </div>
                        </div>
                    </div>
                </div>

                <!-- Preventive Conservation Section -->
                <div class="card mb-4">
                    <div class="card-header">
                        <h4 class="mb-0">Preventive Conservation</h4>
                    </div>
                    <div class="card-body">
                        <div class="row">
                            <div class="col-md-3 mb-3">
                                <label for="temperature" class="form-label">Temperature (°C)</label>
                                <input type="number" step="0.1" class="form-control" id="temperature" name="temperature">
                            </div>
                            <div class="col-md-3 mb-3">
                                <label for="humidity" class="form-label">Humidity (% RH)</label>
                                <input type="number" step="0.1" class="form-control" id="humidity" name="humidity">
                            </div>
                            <div class="col-md-3 mb-3">
                                <label for="type_of_container" class="form-label">Type of Container</label>
                                <input type="text" class="form-control" id="type_of_container" name="type_of_container">
                            </div>
                            <div class="col-md-3 mb-3">
                                <label for="mount" class="form-label">Mount</label>
                                <input type="text" class="form-control" id="mount" name="mount">
                            </div>
                        </div>
                        <div class="mb-3">
                            <label for="result" class="form-label">Result (URL)</label>
                            <input type="url" class="form-control" id="result" name="result">
                        </div>
                    </div>
                </div>

                <!-- Interventive Conservation Section -->
                <div class="card mb-4">
                    <div class="card-header">
                        <h4 class="mb-0">Interventive Conservation</h4>
                    </div>
                    <div class="card-body">
                        <div class="row">
                            <div class="col-md-6 mb-3">
                                <label class="form-label" for="conservation_date_input">Conservation Date</label>

                                <!-- hidden native input -->
                                <input type="date" id="conservation_date_input" name="conservation_date" class="visually-hidden" />

                                <!-- custom UI -->
                                <div class="input-group">
                                    <button type="button" class="btn btn-outline-secondary" id="conservation_date_btn">
                                        <i class="fas fa-calendar"></i>
                                    </button>
                                    <input type="text" class="form-control" id="conservation_date_display" placeholder="Select date" readonly>
                                </div>
                            </div>
                            <div class="col-md-6 mb-3">
                                <label for="cleaning" class="form-label">Cleaning</label>
                                <select class="form-control" id="cleaning" name="cleaning">
                                    <option value="">Select...</option>
                                    <option value="true">Yes</option>
                                    <option value="false">No</option>
                                </select>
                            </div>
                        </div>
                        <div class="row">
                            <div class="col-md-6 mb-3">
                                <label for="introduction_of_foreign_material" class="form-label">Foreign Material Introduced</label>
                                <input type="text" class="form-control" id="introduction_of_foreign_material" name="introduction_of_foreign_material">
                            </div>
                            <div class="col-md-6 mb-3">
                                <label for="specific_foreign_material_introduce" class="form-label">Specific Foreign Material</label>
                                <input type="text" class="form-control" id="specific_foreign_material_introduce" name="specific_foreign_material_introduce">
                            </div>
                        </div>
                    </div>
                </div>

                <!-- Additional Information Section -->
                <div class="card mb-4">
                    <div class="card-header">
                        <h4 class="mb-0">Additional Information</h4>
                    </div>
                    <div class="card-body">
                        <div class="row">
                            <div class="col-md-6 mb-3">
                                <label for="use_case" class="form-label">Use Case</label>
                                <input type="text" class="form-control" id="use_case" name="use_case">
                            </div>
                            <div class="col-md-6 mb-3">
                                <label for="collection" class="form-label">Collection</label>
                                <input type="text" class="form-control" id="collection" name="collection">
                            </div>
                        </div>
                        <div class="row">
                            <div class="col-md-4 mb-3">
                                <label for="time_period" class="form-label">Time Period</label>
                                <input type="text" class="form-control" id="time_period" name="time_period">
                            </div>
                            <div class="col-md-4 mb-3">
                                <label for="creator" class="form-label">Creator</label>
                                <input type="text" class="form-control" id="creator" name="creator">
                            </div>
                            <div class="col-md-4 mb-3">
                                <label for="sensor" class="form-label">Sensor</label>
                                <input type="text" class="form-control" id="sensor" name="sensor">
                            </div>
                        </div>
                        <div class="row">
                            <div class="col-md-6 mb-3">
                                <label for="location" class="form-label">Location</label>
                                <input type="text" class="form-control" id="location" name="location">
                            </div>
                            <div class="col-md-6 mb-3">
                                <label for="source" class="form-label">Source</label>
                                <input type="text" class="form-control" id="source" name="source">
                            </div>
                        </div>
                    </div>
                </div>

                <div class="d-flex justify-content-between mb-4">
                    <a href="/archive/collections" class="mt-2">
                        <i class="fas fa-arrow-left"></i> Cancel
                    </a>
                    <button type="submit" class="btn btn-save" id="submitBtn">
                        <i class="fas fa-save"></i> Save Artefact
                    </button>
                </div>
            </form>
        </div>
        <div class="col-0 col-lg-2"></div>
    </div>
</div>

<script>
    // Handle GLTF file input
    const gltfInput = document.getElementById('gltf_file_input');
    const gltfBtn = document.getElementById('gltf_browse_btn');
    const gltfNameField = document.getElementById('gltf_filename');

    gltfBtn.addEventListener('click', () => gltfInput.click());
    gltfInput.addEventListener('change', () => {
        gltfNameField.value = gltfInput.files && gltfInput.files.length ? gltfInput.files[0].name : 'No file chosen';
    });

    // Handle OBJ file input
    const objInput = document.getElementById('obj_file_input');
    const objBtn = document.getElementById('obj_browse_btn');
    const objNameField = document.getElementById('obj_filename');

    objBtn.addEventListener('click', () => objInput.click());
    objInput.addEventListener('change', () => {
        objNameField.value = objInput.files && objInput.files.length ? objInput.files[0].name : 'No file chosen';
    });

    // Handle conservation date input
    const dateInput = document.getElementById('conservation_date_input');
    const dateBtn = document.getElementById('conservation_date_btn');
    const dateDisplay = document.getElementById('conservation_date_display');

    dateBtn.addEventListener('click', () => dateInput.showPicker());
    dateInput.addEventListener('change', () => {
        if (dateInput.value) {
            // Format date as readable string (e.g., "January 17, 2026")
            const date = new Date(dateInput.value + 'T00:00:00');
            const options = { year: 'numeric', month: 'long', day: 'numeric' };
            dateDisplay.value = date.toLocaleDateString('en-US', options);
        } else {
            dateDisplay.value = '';
        }
    });

    // Form submission handler
    document.getElementById('artefactForm').addEventListener('submit', async (e) => {
        e.preventDefault();
        
        const submitBtn = document.getElementById('submitBtn');
        const originalText = submitBtn.innerHTML;
        submitBtn.disabled = true;
        submitBtn.innerHTML = '<i class="fas fa-spinner fa-spin"></i> Saving...';
        
        try {
            const formData = new FormData(e.target);
            
            const response = await fetch('/archive/artefact/create', {
                method: 'POST',
                body: formData
            });
            
            const result = await response.json();
            
            if (result.success) {
                showAlert('success', 'Artefact created successfully! Redirecting...');
                setTimeout(() => {
                    window.location.href = result.redirect || '/archive/collections';
                }, 1500);
            } else {
                showAlert('danger', 'Error: ' + (result.error || 'Failed to create artefact'));
                submitBtn.disabled = false;
                submitBtn.innerHTML = originalText;
            }
        } catch (error) {
            showAlert('danger', 'Error: ' + error.message);
            submitBtn.disabled = false;
            submitBtn.innerHTML = originalText;
        }
    });
    
    function showAlert(type, message) {
        const alertContainer = document.getElementById('alertContainer');
        alertContainer.innerHTML = \`
            <div class="alert alert-\${type} alert-dismissible fade show" role="alert">
                \${message}
                <button type="button" class="btn-close" data-bs-dismiss="alert" aria-label="Close"></button>
            </div>
        \`;
        alertContainer.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
    }
</script>

${renderFooter()}`;

			const html = renderHtmlPage({
				title: 'Add New Artefact - Digital Textailes Archive',
				content,
				includeModelViewer: false,
				bodyClass: 'id="addArtefact" tabindex="0"',
				cspPolicy: CSP_POLICY
			});
			res.send(html);
		} catch (error) {
			console.error('Add artefact form error:', error);
			res.status(500).send('Error: ' + error.message);
		}
	});

	// POST endpoint to create a new artefact
	// This endpoint receives multipart/form-data directly with files
	router.post('/artefact/create', async (req, res) => {
		try {
			// Check authentication and create permission on artefacts
			const isAuthenticated = await userIsAuthenticated(req, res, AuthenticationService);
			const isEditor = await hasPermission(req, res, ItemsService, 'artefacts', 'create');
			
			if (!isAuthenticated || !isEditor) {
				return res.status(401).json({ error: 'Unauthorized - Editor role required' });
			}

			const bb = busboy({ headers: req.headers }); // Content-Type: multipart/form-data
			const fields = {};
			const files = {};

            // Triggered when busboy encounters a file field
			bb.on('file', (name, file, info) => { // file: readable stream of the file data
				const { filename, encoding, mimeType } = info;
				const chunks = [];
				
				file.on('data', (data) => { // file in chunks
					chunks.push(data);
				});
				
				file.on('end', () => { // concatenate all chunks in files[name] with metadata
					files[name] = {
						buffer: Buffer.concat(chunks),
						filename,
						mimeType,
						size: Buffer.concat(chunks).length
					};
				});
			});

            // Triggered when busboy encounters a non-file field
			bb.on('field', (name, val) => {
				fields[name] = val;
			});

            // Triggered when busboy has finished parsing the whole form
			bb.on('finish', async () => {
				try {
                    // Initialize services
					const artefactsService = new ItemsService('artefacts', {
						schema: req.schema,
						accountability: null,
					});

					const filesService = new FilesService({
						schema: req.schema,
						accountability: null,
					});

					// Prepare artefact data
					const artefactData = { ...fields };

					// Upload files if present
					if (files.gltf_file && files.gltf_file.filename) {
						try {
							const { Readable } = await import('stream'); // uploadOne() expects a stream
							const stream = Readable.from(files.gltf_file.buffer); // create a stream from the buffer (binary data)
							
                            // Metadata for directus_files table
							const fileData = {
								storage: 'local',
								filename_download: files.gltf_file.filename,
								type: files.gltf_file.mimeType,
								filesize: files.gltf_file.size,
								title: files.gltf_file.filename.split('.')[0],
							};
							
							const uploadedFile = await filesService.uploadOne(stream, fileData); // creates record in directus_files and returns the UUID
							artefactData.gltf_file = uploadedFile;
						} catch (fileError) {
							console.error('GLTF upload error:', fileError);
						}
					}

					if (files.obj_file && files.obj_file.filename) {
						try {
							const { Readable } = await import('stream');
							const stream = Readable.from(files.obj_file.buffer);
							
							const fileData = {
								storage: 'local',
								filename_download: files.obj_file.filename,
								type: files.obj_file.mimeType,
								filesize: files.obj_file.size,
								title: files.obj_file.filename.split('.')[0],
							};
							
							const uploadedFile = await filesService.uploadOne(stream, fileData);
							artefactData.obj_file = uploadedFile;
						} catch (fileError) {
							console.error('OBJ upload error:', fileError);
						}
					}

					// Create the artefact
					const result = await artefactsService.createOne(artefactData); // returns the new artefact ID

                    // Respond with success and new artefact ID to redirect
					res.json({ 
						success: true, 
						id: result,
						message: 'Artefact created successfully',
						redirect: `/archive/artefacts/${result}`
					});
				} catch (error) {
					console.error('Create artefact error:', error);
					res.status(500).json({ 
						success: false,
						error: error.message 
					});
				}
			});

			bb.on('error', (error) => {
				console.error('Busboy error:', error);
				res.status(500).json({ 
					success: false,
					error: error.message 
				});
			});

            // Pipes the request to start parsing
			req.pipe(bb);

		} catch (error) {
			console.error('Route error:', error);
			res.status(500).json({ 
				success: false,
				error: error.message 
			});
		}
	});
};
