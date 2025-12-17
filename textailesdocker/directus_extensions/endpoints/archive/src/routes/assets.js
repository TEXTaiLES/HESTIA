import { PATHS } from '../utils/constants.js';
import { serveFile, mapGltfUris } from '../utils/helpers.js';

const path = require('path');
const fs = require('fs');
const obj2gltf = require('obj2gltf');
const crypto = require('crypto');

export default (router, { services }) => {
	const { ItemsService } = services;
	
	// Cache directory for converted GLB files
	const CACHE_DIR = path.join(PATHS.UPLOADS_ROOT, '.glb_cache');
	if (!fs.existsSync(CACHE_DIR)) {
		fs.mkdirSync(CACHE_DIR, { recursive: true });
	}

	// Route: Serve static files
	router.get('/static/*', (req, res) => {
		const requestedPath = req.params[0];
		const staticPath = path.join(PATHS.STATIC_ROOT, requestedPath);
		serveFile(staticPath, res);
	});

	// Route: Serve favicon
	router.get('/favicon.ico', (req, res) => {
		const faviconPath = path.join(PATHS.STATIC_ROOT, PATHS.FAVICON);
		serveFile(faviconPath, res, 'image/png');
	});

	// Route: Proxy endpoint to serve assets publicly
	router.get('/assets/:fileId', async (req, res) => {
		try {
			const filesService = new ItemsService('directus_files', {
				schema: req.schema,
				accountability: null,
			});
			const file = await filesService.readOne(req.params.fileId);
			if (!file) {
				return res.status(404).send('File not found');
			}
			const uploadsPath = path.join(PATHS.UPLOADS_ROOT, file.filename_disk);
			if (!fs.existsSync(uploadsPath)) {
				return res.status(404).send('File not found on disk');
			}
			res.set('Content-Type', file.type || 'application/octet-stream');
			res.set('Access-Control-Allow-Origin', '*');
			
			// Get related files if obj_files parameter is provided (comma-separated file IDs)
			let relatedFiles = [];
			if (req.query.obj_files) { // req.query.obj_files = "ben-uuid,ben_ks-uuid" (MTL + textures)
				const fileIds = req.query.obj_files.split(',').filter(id => id && id !== req.params.fileId); // fileIds = ["ben-uuid", "ben_ks-uuid"]
				if (fileIds.length > 0) {
					relatedFiles = await filesService.readByQuery({
						fields: ['id', 'filename_disk', 'filename_download', 'type'],
						filter: { id: { _in: fileIds } }, // Query ONLY these 2 files
						limit: -1
					});
				}
			} 
			
			// Handle OBJ files - convert to GLB (binary GLTF) with materials and textures
			if (file.type === 'model/obj' || file.filename_download.endsWith('.obj')) {
				try {
					// Generate cache key based on file ID and related files
					const relatedFileIds = relatedFiles.map(f => f.id).sort().join(',');
                    // Generate unique cache key from: OBJ ID + related file IDs + modification date
					const cacheKey = crypto.createHash('md5')
						.update(`${req.params.fileId}:${relatedFileIds}:${file.modified_on || file.uploaded_on}`) // req.params.fileId = "abc123-uuid" (the OBJ file)
						.digest('hex');
					const cachedGlbPath = path.join(CACHE_DIR, `${cacheKey}.glb`);
					
					// Check if cached GLB exists
					if (fs.existsSync(cachedGlbPath)) {
						const cachedGlb = fs.readFileSync(cachedGlbPath);
						res.set('Content-Type', 'model/gltf-binary');
						res.set('X-Cache', 'HIT'); // Cache HIT → Return cached GLB instantly
						return res.send(cachedGlb);
					}
					const conversionStart = Date.now();
					
					// Read OBJ file
					let objContent = fs.readFileSync(uploadsPath, 'utf8');
					let mtlContent = null;
					let tempObjPath = null;
					let tempMtlPath = null;
					
			// Use provided related files if available, otherwise search all files
			let allFiles = relatedFiles.length > 0 ? relatedFiles : null;
			if (!allFiles) {
                // No obj_files parameter → search ALL uploaded files
				allFiles = await filesService.readByQuery({
					fields: ['id', 'filename_disk', 'filename_download', 'type'],
					limit: -1
				});
			}		// Check if OBJ references an MTL file
					const mtllibMatch = objContent.match(/^mtllib\s+(.+)$/m);
					if (mtllibMatch && mtllibMatch[1]) {
						const originalMtlPath = mtllibMatch[1].trim();
						const originalMtlFilename = path.basename(originalMtlPath);
						
						// Find the MTL file - search by similar filename or just get the only MTL
						let mtlFile = allFiles.find(f => 
							(f.type === 'model/mtl' || f.filename_download.endsWith('.mtl')) &&
							f.filename_download.toLowerCase().includes(originalMtlFilename.toLowerCase().replace('.mtl', ''))
						);
						
						// If not found by name, just get any MTL file (assuming one MTL per OBJ)
						if (!mtlFile) {
							mtlFile = allFiles.find(f => f.type === 'model/mtl' || f.filename_download.endsWith('.mtl'));
						}
						
						if (mtlFile) {
							// Read MTL content
							const mtlDiskPath = path.join(PATHS.UPLOADS_ROOT, mtlFile.filename_disk);
							mtlContent = fs.readFileSync(mtlDiskPath, 'utf8');
							
							// Fix texture paths in MTL to use UUIDs
							const textureReferences = ['map_Kd', 'map_Ka', 'map_Ks', 'map_Ns', 'map_d', 'map_bump', 'bump'];
							textureReferences.forEach(texType => {
								const regex = new RegExp(`^${texType}\\s+(.+)$`, 'gm');
								mtlContent = mtlContent.replace(regex, (match, texPath) => {
									const texFilename = path.basename(texPath.trim());
									
									// Find texture file in uploads
									const texFile = allFiles.find(f => 
										(f.type && f.type.startsWith('image/')) &&
										(f.filename_download.toLowerCase() === texFilename.toLowerCase() ||
										 f.filename_download.toLowerCase().includes(texFilename.toLowerCase().replace(/\.[^.]+$/, '')))
									);
									
									if (texFile) {
										return `${texType} ${texFile.filename_disk}`;
									}
									return match;
								});
							});
							
							// Create temp MTL file with fixed paths
							tempMtlPath = path.join(PATHS.UPLOADS_ROOT, `temp_${Date.now()}_material.mtl`);
							fs.writeFileSync(tempMtlPath, mtlContent);
							
							// Update OBJ to reference temp MTL
							objContent = objContent.replace(/^mtllib\s+.+$/m, `mtllib ${path.basename(tempMtlPath)}`);
						}
					}
					
					// Create temp OBJ file
					tempObjPath = path.join(PATHS.UPLOADS_ROOT, `temp_${Date.now()}_model.obj`);
					fs.writeFileSync(tempObjPath, objContent);
					
					try {
						// Convert to GLB
						const glb = await obj2gltf(tempObjPath, {
							binary: true,
							packOcclusion: true,
							metallicRoughness: true,
							specularGlossiness: false,
							unlit: false
						});
						
						// Save to cache
						fs.writeFileSync(cachedGlbPath, Buffer.from(glb));
						
						res.set('Content-Type', 'model/gltf-binary');
						res.set('X-Cache', 'MISS');
						return res.send(Buffer.from(glb));
					} finally {
						// Clean up temp files
						if (tempObjPath && fs.existsSync(tempObjPath)) {
							fs.unlinkSync(tempObjPath);
						}
						if (tempMtlPath && fs.existsSync(tempMtlPath)) {
							fs.unlinkSync(tempMtlPath);
						}
					}
				} catch (error) {
					console.error('OBJ to GLB conversion error:', error);
					return res.status(500).send('Error converting OBJ to GLB: ' + error.message);
				}
			}
			
			// Handle GLTF files - rewrite URIs to use proxy
			if (file.type === 'model/gltf+json' || file.filename_download.endsWith('.gltf')) {
				const gltfContent = fs.readFileSync(uploadsPath, 'utf8');
				const gltf = JSON.parse(gltfContent);
				await mapGltfUris(gltf, filesService);
				res.set('Content-Type', 'model/gltf+json');
				return res.send(JSON.stringify(gltf));
			}
			
			// Handle MTL files (material files for OBJ)
			if (file.type === 'model/mtl' || file.filename_download.endsWith('.mtl')) {
				res.set('Content-Type', 'model/mtl');
			}
			
			// Stream other files (bin, jpg, obj, mtl, etc)
			fs.createReadStream(uploadsPath).pipe(res);
		} catch (error) {
			console.error('Asset error:', error);
			res.status(500).send('Error: ' + error.message);
		}
	});
};
