import { PATHS } from '../utils/constants.js';
import { serveFile, mapGltfUris } from '../utils/helpers.js';

const path = require('path');
const fs = require('fs');
const http = require('http');
const obj2gltf = require('obj2gltf');
const crypto = require('crypto');

const MINIO_INTERNAL = process.env.MINIO_INTERNAL_ENDPOINT || 'minio:9000';

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

	// Route: Proxy a robot image from MinIO by scan_id and filename
	// Works because robot images are public in MinIO and local, production have the same network.
	router.get('/assets/robot-image/:scan_id/:filename', (req, res) => {
		const { scan_id } = req.params;
		const filename = decodeURIComponent(req.params.filename);
		const encodedFilename = encodeURIComponent(filename);
		const minioUrl = `http://${MINIO_INTERNAL}/robot-images/${scan_id}/${encodedFilename}`;

		http.get(minioUrl, (minioRes) => {
			if (minioRes.statusCode !== 200) {
				return res.status(minioRes.statusCode || 404).send('Image not found in storage');
			}
			const contentType = minioRes.headers['content-type'] || 'image/jpeg';
			res.set('Content-Type', contentType);
			res.set('Cache-Control', 'public, max-age=3600');
			if (minioRes.headers['content-length']) {
				res.set('Content-Length', minioRes.headers['content-length']);
			}
			minioRes.pipe(res);
		}).on('error', (err) => {
			console.error('MinIO robot image proxy error:', err.message);
			res.status(502).send('Error fetching image from storage');
		});
	});

	// Route: Proxy a reconstruction GLB from MinIO by object_id.
	// Also patches all materials to doubleSided=true so model-viewer renders inner faces (uses both faces of the triangle).
	// Works because reconstructions are public in MinIO and local, production have the same network.
	router.get('/assets/reconstruction/:object_id/model', (req, res) => {
		const { object_id } = req.params;
		const minioUrl = `http://${MINIO_INTERNAL}/reconstructions/${object_id}/model.glb`;

		http.get(minioUrl, (minioRes) => {
			if (minioRes.statusCode !== 200) {
				return res.status(minioRes.statusCode || 404).send('Model not found in storage');
			}

			// Stream the GLB from MinIO, parse it, modify materials to be double-sided, and re-serve it.
			const chunks = [];
			minioRes.on('data', chunk => chunks.push(chunk));
			
			// Process the GLB once fully received - patch logic.
			minioRes.on('end', () => {
				try {
					const glb = Buffer.concat(chunks);

					// GLB binary layout: 12-byte header, then chunks (0 : JSON, 1 : BIN mesh).
					// Chunk 0: 4-byte length + 4-byte type (JSON) + JSON bytes
					const jsonChunkLength = glb.readUInt32LE(12);
					const jsonStart = 20;
					const jsonEnd = jsonStart + jsonChunkLength;

					const gltf = JSON.parse(glb.toString('utf8', jsonStart, jsonEnd));

					// Force every material to render both sides
					if (Array.isArray(gltf.materials)) {
						gltf.materials.forEach(mat => { mat.doubleSided = true; });
					}

					// Re-encode JSON, pad to 4-byte boundary with spaces (spec requirement)
					let newJsonStr = JSON.stringify(gltf);
					const pad = (4 - (newJsonStr.length % 4)) % 4;
					newJsonStr += ' '.repeat(pad);
					const newJsonBuf = Buffer.from(newJsonStr, 'utf8');

					// Everything after the original JSON chunk (binary chunk) stays untouched
					const binChunk = glb.subarray(jsonEnd);
					const newTotalLength = 12 + 8 + newJsonBuf.length + binChunk.length;

					// Update GLB header and JSON chunk header with new lengths
					const newGlb = Buffer.alloc(newTotalLength);
					glb.copy(newGlb, 0, 0, 8);                    // magic + version
					newGlb.writeUInt32LE(newTotalLength, 8);        // updated total length
					newGlb.writeUInt32LE(newJsonBuf.length, 12);    // updated JSON chunk length
					glb.copy(newGlb, 16, 16, 20);                  // JSON chunk type unchanged
					newJsonBuf.copy(newGlb, 20);                    // new JSON
					binChunk.copy(newGlb, 20 + newJsonBuf.length);  // binary chunk unchanged

					res.set('Content-Type', 'model/gltf-binary');
					res.set('Cache-Control', 'public, max-age=3600');
					res.set('Content-Length', newTotalLength.toString());
					res.send(newGlb);
				} catch (err) {
					console.error('GLB patch error:', err.message);
					res.status(500).send('Error processing model');
				}
			});
			minioRes.on('error', err => {
				console.error('MinIO stream error:', err.message);
				res.status(502).send('Error reading model from storage');
			});
		}).on('error', (err) => {
			console.error('MinIO proxy error:', err.message);
			res.status(502).send('Error fetching model from storage');
		});
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
