/**
 * ATON API Integration Routes
 * 
 * These endpoints integrate with ATON server REST API v2 to:
 * - Create scenes with 3D models (POST /aton/scene/:artefactId)
 * - Retrieve existing scenes (GET /aton/scene/:artefactId)
 * - List all scenes (GET /aton/scenes)
 * - Get or create scene URL (GET /aton/scene/:artefactId/url)
 * 
 * ATON API Documentation: https://aton.ispc.cnr.it/apiv2-docs/
 */

import { createAtonScene, getAtonScene, listAtonScenes } from '../utils/helpers.js';
import { ATON_CONFIG } from '../utils/constants.js';

export default (router, { services }) => {
	const { ItemsService } = services;

	/**
	 * POST /aton/scene/:artefactId
	 * 
	 * Creates a new scene in ATON with the artefact's 3D model
	 * Calls ATON API: POST /api/v2/scenes/
	 */
	router.post('/aton/scene/:artefactId', async (req, res) => {
		try {
			const artefactsService = new ItemsService('artefacts', {
				schema: req.schema,
				accountability: null,
			});

			const artefacts = await artefactsService.readByQuery({
				fields: ['id', 'title', 'gltf_file', 'obj_file', 'obj_files.directus_files_id'],
				filter: { id: { _eq: req.params.artefactId } },
				limit: 1
			});

			if (!artefacts || artefacts.length === 0) {
				return res.status(404).json({ error: 'Artefact not found' });
			}

			const artefact = artefacts[0];
			const modelFile = artefact.gltf_file || artefact.obj_file;
			
			if (!modelFile) {
				return res.status(400).json({ error: 'No 3D model available for this artefact' });
			}

			// Construct the full URL to the model
			const baseUrl = `${req.protocol}://${req.get('host')}`;
			const relatedFileIds = artefact.obj_files?.map(f => f.directus_files_id).filter(Boolean) || [];
			let modelUrl = `${baseUrl}/archive/assets/${modelFile}`;
			if (artefact.obj_file && relatedFileIds.length > 0) {
				modelUrl += `?obj_files=${relatedFileIds.join(',')}`;
			}

			// Call ATON API v2 to create the scene
			const sceneData = await createAtonScene(
				artefact.id,
				artefact.title,
				modelUrl
			);

			res.json({
				...sceneData,
				artefactId: artefact.id,
				artefactTitle: artefact.title,
				modelUrl: modelUrl
			});

		} catch (error) {
			console.error('Create ATON scene error:', error);
			res.status(500).json({ 
				error: 'Failed to create ATON scene',
				message: error.message 
			});
		}
	});

	/**
	 * GET /aton/scene/:artefactId
	 * 
	 * Retrieves an existing scene from ATON
	 * Calls ATON API: GET /api/v2/scenes/{user}/{sceneId}
	 * Returns 404 if scene doesn't exist
	 */
	router.get('/aton/scene/:artefactId', async (req, res) => {
		try {
			// Build scene ID from artefact ID
			const sceneId = `artefact_${req.params.artefactId}`;
			const user = req.query.user || ATON_CONFIG.DEFAULT_USER;

			// Call ATON API v2 to get the scene
			const sceneData = await getAtonScene(user, sceneId);

			if (!sceneData) {
				return res.status(404).json({ 
					error: 'Scene not found',
					message: `Scene ${sceneId} not found for user ${user}. You may need to create it first.`,
					createEndpoint: `/archive/aton/scene/${req.params.artefactId}`
				});
			}

			res.json(sceneData);

		} catch (error) {
			console.error('Get ATON scene error:', error);
			res.status(500).json({ 
				error: 'Failed to get ATON scene',
				message: error.message 
			});
		}
	});

	/**
	 * GET /aton/scenes
	 * 
	 * Lists all public scenes from ATON server
	 * Calls ATON API: GET /api/v2/scenes/
	 */
	router.get('/aton/scenes', async (req, res) => {
		try {
			// Fetch all public scenes from ATON
			const scenesData = await listAtonScenes();

			res.json({
				...scenesData,
				baseUrl: ATON_CONFIG.BASE_URL,
				thothPath: ATON_CONFIG.THOTH_PATH
			});

		} catch (error) {
			console.error('List ATON scenes error:', error);
			res.status(500).json({ 
				error: 'Failed to list ATON scenes',
				message: error.message 
			});
		}
	});

	/**
	 * GET /aton/scene/:artefactId/url
	 * 
	 * Convenience endpoint that:
	 * 1. Tries to get existing scene from ATON
	 * 2. If not found, creates a new scene
	 * 3. Returns the scene URL for THOTH annotator
	 * 
	 * This is the main endpoint used by the "Annotate with THOTH" button
	 */
	router.get('/aton/scene/:artefactId/url', async (req, res) => {
		try {
			// Get artefact data from Directus database
			const artefactsService = new ItemsService('artefacts', {
				schema: req.schema,
				accountability: null,
			});

			const artefacts = await artefactsService.readByQuery({
				fields: ['id', 'title', 'gltf_file', 'obj_file', 'obj_files.directus_files_id'],
				filter: { id: { _eq: req.params.artefactId } },
				limit: 1
			});

			if (!artefacts || artefacts.length === 0) {
				return res.status(404).json({ error: 'Artefact not found' });
			}

			const artefact = artefacts[0];
			const sceneId = `artefact_${artefact.id}`;
			const user = req.query.user || ATON_CONFIG.DEFAULT_USER;

			// Step 1: Try to get existing scene from ATON
			let sceneData = await getAtonScene(user, sceneId);

			// Step 2: If scene doesn't exist in ATON, create it
			if (!sceneData) {
				const modelFile = artefact.gltf_file || artefact.obj_file;
				
				if (!modelFile) {
					return res.status(400).json({ error: 'No 3D model available' });
				}

				const baseUrl = `${req.protocol}://${req.get('host')}`;
				const relatedFileIds = artefact.obj_files?.map(f => f.directus_files_id).filter(Boolean) || [];
				let modelUrl = `${baseUrl}/archive/assets/${modelFile}`;
				if (artefact.obj_file && relatedFileIds.length > 0) {
					modelUrl += `?obj_files=${relatedFileIds.join(',')}`;
				}

				sceneData = await createAtonScene(artefact.id, artefact.title, modelUrl);
			}

			res.json({
				...sceneData,
				artefactId: artefact.id,
				artefactTitle: artefact.title
			});

		} catch (error) {
			console.error('Get/create scene URL error:', error);
			res.status(500).json({ 
				error: 'Failed to get scene URL',
				message: error.message 
			});
		}
	});
};
