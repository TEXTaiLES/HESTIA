import { CONTENT_TYPES, ATON_CONFIG } from './constants.js';

const fs = require('fs');
const path = require('path');

// Helper: Serve a static file with proper content type header
export const serveFile = (filePath, res, contentTypeOverride = null) => {
	if (!fs.existsSync(filePath)) {
		return res.status(404).send('File not found');
	}
	const ext = path.extname(filePath).toLowerCase();
	const contentType = contentTypeOverride || CONTENT_TYPES[ext];
	if (contentType) {
		res.set('Content-Type', contentType);
	}
	const fileContent = fs.readFileSync(filePath);
	res.send(fileContent);
};

// Helper: Map GLTF URIs to Directus file IDs
export const mapGltfUris = async (gltf, filesService) => {
	const allFiles = await filesService.readByQuery({
		fields: ['id', 'filename_download'],
		limit: -1
	});

	const fileMap = new Map(allFiles.map(f => [f.filename_download, f.id]));

	const updateUri = (item) => {
		if (item.uri && !item.uri.startsWith('data:')) {
			const filename = path.basename(item.uri);
			const fileId = fileMap.get(filename);
			if (fileId) {
				item.uri = fileId;
			}
		}
	};

	gltf.buffers?.forEach(updateUri);
	gltf.images?.forEach(updateUri);

	return gltf;
};

// Helper: Match artefacts by use case
export const matchesByUseCase = (artefact, usecase, useCaseNumber) => {
	// Direct number match (artefact.use_case is "1", "2", "3", etc.)
	const matchByNumber = useCaseNumber && artefact.use_case && artefact.use_case.toString() === useCaseNumber.toString();
	// Title contains use case number (e.g., "1. Greek Ancient Textiles")
	const matchByTitle = useCaseNumber && artefact.title && artefact.title.includes(useCaseNumber + '.');
	return matchByNumber || matchByTitle;
};

// Helper: Create ATON scene using REST API v2
export const createAtonScene = async (artefactId, artefactTitle, modelUrl) => {
	// Generate unique scene ID for this artefact
	const sceneId = `artefact_${artefactId}`;
	
	// Build request body according to ATON REST API v2 specification
	const requestBody = {
		data: {
			title: artefactTitle || `Artefact ${artefactId}`,
			description: `3D model for artefact ${artefactId}`,
			// You can add more metadata here
		},
		vis: 1,  // 1 = public, 0 = private
		fromItem: modelUrl  // Create scene from this 3D model URL
	};

	try {
		// POST to ATON API v2 to create scene
		const createUrl = `${ATON_CONFIG.BASE_URL}${ATON_CONFIG.API_BASE}/scenes/`;
		
		// Send request to ATON server using fetch()
		const response = await fetch(createUrl, {
			method: 'POST',
			headers: {
				'Content-Type': 'application/json'
			},
			body: JSON.stringify(requestBody)
		});
		
		if (!response.ok) {
			const errorData = await response.json().catch(() => ({}));
			throw new Error(`ATON API error: ${errorData.message || response.statusText}`);
		}
		
		const responseData = await response.json();
		
		// Return scene info including URL for THOTH annotator
		return {
			success: true,
			sceneId: sceneId,
			sceneUrl: `${ATON_CONFIG.BASE_URL}${ATON_CONFIG.THOTH_PATH}?s=${sceneId}`,
			sceneData: responseData
		};
		
	} catch (error) {
		console.error('Error creating ATON scene:', error.message);
		throw new Error(`Failed to create ATON scene: ${error.message}`);
	}
};

// Helper: Get ATON scene JSON from API
export const getAtonScene = async (user, sceneId) => {
	try {
		// Build URL to get specific scene: /api/v2/scenes/{user}/{sceneId}
		const sceneUrl = `${ATON_CONFIG.BASE_URL}${ATON_CONFIG.API_BASE}/scenes/${user}/${sceneId}`;
		
		// Fetch scene JSON from ATON using fetch()
		const response = await fetch(sceneUrl);
		
		if (!response.ok) {
			return null; // Scene not found
		}
		
		const responseData = await response.json();
		
		return {
			success: true,
			sceneId: sceneId,
			sceneUrl: `${ATON_CONFIG.BASE_URL}${ATON_CONFIG.THOTH_PATH}?s=${sceneId}`,
			sceneJson: responseData
		};
	} catch (error) {
		console.error('Error fetching ATON scene:', error.message);
		return null;
	}
};

// Helper: List all ATON scenes from API
export const listAtonScenes = async () => {
	try {
		// Get list of all public scenes from ATON
		const scenesUrl = `${ATON_CONFIG.BASE_URL}${ATON_CONFIG.API_BASE}/scenes/`;
		const response = await fetch(scenesUrl);
		
		if (!response.ok) {
			const errorData = await response.json().catch(() => ({}));
			throw new Error(`ATON API error: ${errorData.message || response.statusText}`);
		}
		
		const responseData = await response.json();
		
		return {
			success: true,
			scenes: responseData
		};
	} catch (error) {
		console.error('Error listing ATON scenes:', error.message);
		throw new Error(`Failed to list ATON scenes: ${error.message}`);
	}
};

