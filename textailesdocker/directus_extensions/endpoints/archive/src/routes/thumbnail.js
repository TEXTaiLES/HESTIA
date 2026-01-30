/*
 * Thumbnail generation route
 * Handles thumbnail generation from GLB files using a Python script
 */
import { PATHS } from '../utils/constants.js';

const path = require('path');
const fs = require('fs');
const { spawn } = require('child_process'); // For spawning Python process

// Use absolute path - the extension is mounted at /directus/extensions/endpoints/archive
const PYTHON_SCRIPT_THUMBNAIL = '/directus/extensions/endpoints/archive/scripts/generate_thumbnail.py';
const PYTHON_CMD = process.env.PYTHON_CMD || (process.platform === 'win32' ? 'python' : 'python3');

/**
 * Execute Python thumbnail generation script
 * @param {string} glbPath - Path to the GLB file
 * @param {string} outputPath - Path where thumbnail should be saved
 * @param {number} viewIndex - Camera view index (0-23)
 * @returns {Promise<void>}
 */
function generateThumbnailPython(glbPath, outputPath, viewIndex = 7) {
	return new Promise((resolve, reject) => { // allows async/await usage
		const args = [PYTHON_SCRIPT_THUMBNAIL, glbPath, outputPath, viewIndex.toString()];
		
		console.log(`Executing: ${PYTHON_CMD} ${args.join(' ')}`);
		
		const process = spawn(PYTHON_CMD, args);
		
		// Buffers to capture output
		let stdout = '';
		let stderr = '';
		
		// Capture standard output
		process.stdout.on('data', (data) => {
			stdout += data.toString();
		});
		
		// Capture error output
		process.stderr.on('data', (data) => {
			stderr += data.toString();
		});
		
		// Handle process exit (0 = success)
		process.on('close', (code) => {
			if (code === 0) {
				console.log(stdout);
				resolve();
			} else {
				console.error('Python script error:', stderr);
				reject(new Error(`Python script exited with code ${code}: ${stderr}`));
			}
		});
		
		// Handle process start errors (e.g., command not found)
		process.on('error', (err) => {
			reject(new Error(`Failed to start Python script: ${err.message}`));
		});
	});
}

export default (router, { services }) => {
	const { ItemsService, FilesService } = services;

	/*
	 * POST /thumbnail/generate
	 * 
	 * Generate a thumbnail from a GLB file and update the artefact
	 * 
	 * Body:
	 * - artefact_id: The artefact ID to update
	 * - glb_file_id: The Directus file ID of the GLB file
	 * - view_index: (optional) Which view to use (0-23), default 7
	 */
	router.post('/thumbnail/generate', async (req, res) => {
		try {
			const { artefact_id, glb_file_id, view_index = 7 } = req.body; // extract from body

			// Validate input
			if (!artefact_id || !glb_file_id) {
				return res.status(400).json({
					success: false,
					error: 'Missing required fields: artefact_id and glb_file_id'
				});
			}

			// Initialize services
			const filesService = new FilesService({
				schema: req.schema,
				accountability: null,
			});

			const artefactsService = new ItemsService('artefacts', {
				schema: req.schema,
				accountability: null,
			});

			// Get the GLB file info from Directus
			const glbFile = await filesService.readOne(glb_file_id);
			if (!glbFile) {
				return res.status(404).json({
					success: false,
					error: 'GLB file not found'
				});
			}

			// Path to the GLB file
			const glbPath = path.join(PATHS.UPLOADS_ROOT, glbFile.filename_disk);
			if (!fs.existsSync(glbPath)) {
				return res.status(404).json({
					success: false,
					error: 'GLB file not found on disk'
				});
			}

			console.log(`Generating thumbnail for artefact ${artefact_id} from GLB ${glbFile.filename_download}`);

			// Create temporary path for thumbnail
			const tempThumbnailPath = path.join(
				PATHS.UPLOADS_ROOT,
				`temp_thumbnail_${Date.now()}_${artefact_id}.png`
			);

			try {
				// Generate thumbnail using Python script
				await generateThumbnailPython(glbPath, tempThumbnailPath, view_index);

				// Read the generated thumbnail
				const thumbnailBuffer = fs.readFileSync(tempThumbnailPath); // load into memory as buffer

				// Upload thumbnail to Directus (convert buffer to stream for uploadOne)
				const { Readable } = await import('stream');
				const thumbnailStream = Readable.from(thumbnailBuffer);

				const thumbnailFileData = {
					storage: 'local',
					filename_download: `${artefact_id}_thumbnail.png`,
					type: 'image/png',
					filesize: thumbnailBuffer.length,
					title: `Thumbnail for ${artefact_id}`,
				};

				const uploadedThumbnail = await filesService.uploadOne(thumbnailStream, thumbnailFileData);

				// Update the artefact with the thumbnail
				await artefactsService.updateOne(artefact_id, {
					thumbnail: uploadedThumbnail
				});

				// Clean up temp file
				if (fs.existsSync(tempThumbnailPath)) {
					fs.unlinkSync(tempThumbnailPath);
				}

				console.log(`Thumbnail generated and saved for artefact ${artefact_id}`);

				// Respond with success and thumbnail ID so the interface can update the field
				return res.json({
					success: true,
					thumbnail_id: uploadedThumbnail,
					artefact_id: artefact_id
				});

			} catch (error) {
				// Clean up temp file on error
				if (fs.existsSync(tempThumbnailPath)) {
					fs.unlinkSync(tempThumbnailPath);
				}
				throw error;
			}

		} catch (error) {
			console.error('Thumbnail generation error:', error);
			return res.status(500).json({
				success: false,
				error: error.message
			});
		}
	});
};