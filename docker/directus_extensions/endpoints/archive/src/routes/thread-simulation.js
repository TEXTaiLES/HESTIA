/**
 * Thread Simulation Routes
 *
 * - POST /dynamo/thread-simulations           → proxy to Flask API (form submission)
 * - GET  /dynamo/thread-simulations/:id       → proxy to Flask API (status + output)
 * - GET  /assets/thread-simulation/:id/visualization.glb
 *        Thin proxy to GET /dynamo/thread-simulations/:id/visualization.glb on
 *        the Flask API, which builds (and caches in MinIO) a single GLB with
 *        morph-target animation from every OBJ in visualizationFiles.
 * - GET  /dynamo/thread-simulations/:id/download.zip
 *        Thin proxy to GET /dynamo/thread-simulations/:id/download.zip on
 *        the Flask API, which generates a ZIP containing simulation.json
 *        and force_elongation.png.
 */

import { userIsAuthenticated } from '../utils/auth.js';

const http = require('http');

const API_INTERNAL = process.env.API_INTERNAL_ENDPOINT || 'api:5000';
const API_SECRET_KEY = process.env.API_SECRET_KEY || '';

// ---------- helpers ----------

function readBody(req) {
	return new Promise((resolve) => {
		if (req.body && Object.keys(req.body).length > 0) {
			return resolve(JSON.stringify(req.body));
		}
		let raw = '';
		req.on('data', (chunk) => { raw += chunk; });
		req.on('end', () => resolve(raw || '{}'));
		req.on('error', () => resolve('{}'));
	});
}

function apiRequestBuffered(method, pathname, { body = null } = {}) {
	return new Promise((resolve, reject) => {
		const url = `http://${API_INTERNAL}${pathname}`;
		const headers = { Authorization: `Bearer ${API_SECRET_KEY}` };
		if (body !== null) {
			headers['Content-Type'] = 'application/json';
			headers['Content-Length'] = Buffer.byteLength(body);
		}
		const req = http.request(url, { method, headers }, (res) => {
			const chunks = [];
			res.on('data', (c) => chunks.push(c));
			res.on('end', () => resolve({ statusCode: res.statusCode, headers: res.headers, body: Buffer.concat(chunks) }));
		});
		req.on('error', reject);
		if (body !== null) req.write(body);
		req.end();
	});
}

// ---------- routes ----------

export default (router, { services }) => {
	const { AuthenticationService } = services;

	router.post('/dynamo/thread-simulations', async (req, res) => {
		try {
			const isAuthenticated = await userIsAuthenticated(req, res, AuthenticationService);
			if (!isAuthenticated) return res.status(401).json({ error: 'Not authenticated' });

			const body = await readBody(req);
			const result = await apiRequestBuffered('POST', '/dynamo/thread-simulations', { body });
			res.status(result.statusCode || 502);
			res.set('Content-Type', result.headers['content-type'] || 'application/json');
			res.send(result.body);
		} catch (err) {
			console.error('[thread-simulation] POST error:', err);
			res.status(502).json({ error: 'API unreachable', message: err.message, code: err.code });
		}
	});

	router.get('/dynamo/thread-simulations/:simulation_id', async (req, res) => {
		try {
			const isAuthenticated = await userIsAuthenticated(req, res, AuthenticationService);
			if (!isAuthenticated) return res.status(401).json({ error: 'Not authenticated' });
			const { simulation_id } = req.params;
			const result = await apiRequestBuffered('GET', `/dynamo/thread-simulations/${encodeURIComponent(simulation_id)}`);
			res.status(result.statusCode || 502);
			res.set('Content-Type', result.headers['content-type'] || 'application/json');
			res.send(result.body);
		} catch (err) {
			console.error('[thread-simulation] GET error:', err);
			res.status(502).json({ error: 'API unreachable', message: err.message, code: err.code });
		}
	});

	// Stream the morph-target GLB. May be large (tens of MB) — stream rather
	// than buffer so the first chunk hits the browser as soon as it leaves
	// the API. Build/cache lives on the API side.
	router.get('/assets/thread-simulation/:simulation_id/visualization.glb', (req, res) => {
		const { simulation_id } = req.params;
		const upstreamPath = `/dynamo/thread-simulations/${encodeURIComponent(simulation_id)}/visualization.glb`;
		const upstream = http.request(
			`http://${API_INTERNAL}${upstreamPath}`,
			{ method: 'GET', headers: { Authorization: `Bearer ${API_SECRET_KEY}` } },
			(apiRes) => {
				res.status(apiRes.statusCode || 502);
				// Forward content-type and cache headers; default to GLB mimetype.
				res.set('Content-Type', apiRes.headers['content-type'] || 'model/gltf-binary');
				if (apiRes.headers['cache-control']) res.set('Cache-Control', apiRes.headers['cache-control']);
				if (apiRes.headers['content-length']) res.set('Content-Length', apiRes.headers['content-length']);
				if (apiRes.headers['x-cache']) res.set('X-Cache', apiRes.headers['x-cache']);
				apiRes.pipe(res);
			}
		);
		upstream.on('error', (err) => {
			console.error('[thread-simulation] visualization.glb proxy error:', err);
			if (!res.headersSent) {
				res.status(502).json({ error: 'API unreachable', message: err.message, code: err.code });
			} else {
				res.end();
			}
		});
		upstream.end();
	});

	// Download ZIP (simulation.json + force_elongation.png). Streamed and the
	// Content-Disposition is forwarded so the browser triggers a save dialog.
	router.get('/dynamo/thread-simulations/:simulation_id/download.zip', (req, res) => {
		const { simulation_id } = req.params;
		const upstreamPath = `/dynamo/thread-simulations/${encodeURIComponent(simulation_id)}/download.zip`;
		const upstream = http.request(
			`http://${API_INTERNAL}${upstreamPath}`,
			{ method: 'GET', headers: { Authorization: `Bearer ${API_SECRET_KEY}` } },
			(apiRes) => {
				res.status(apiRes.statusCode || 502);
				res.set('Content-Type', apiRes.headers['content-type'] || 'application/zip');
				if (apiRes.headers['content-disposition']) res.set('Content-Disposition', apiRes.headers['content-disposition']);
				if (apiRes.headers['content-length']) res.set('Content-Length', apiRes.headers['content-length']);
				res.set('Cache-Control', 'no-store');
				apiRes.pipe(res);
			}
		);
		upstream.on('error', (err) => {
			console.error('[thread-simulation] download.zip proxy error:', err);
			if (!res.headersSent) {
				res.status(502).json({ error: 'API unreachable', message: err.message, code: err.code });
			} else {
				res.end();
			}
		});
		upstream.end();
	});
};