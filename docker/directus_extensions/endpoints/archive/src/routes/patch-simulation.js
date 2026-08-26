/**
 * Patch Simulation Routes
 *
 * - POST /dynamo/patch-simulations                                  → JSON proxy (form submission)
 * - GET  /dynamo/patch-simulations/:id                              → JSON proxy (status + output)
 * - GET  /assets/patch-simulation/:id/visualization/:experiment.glb → streams the per-experiment morph GLB
 * - GET  /dynamo/patch-simulations/:id/download.zip                 → streams the simulation ZIP
 *
 * Patch produces 6 distinct OBJ animations (inplane11, inplane22, inplane12,
 * bending11, bending22, bending12); the page picks one at a time via a selector.
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

	router.post('/dynamo/patch-simulations', async (req, res) => {
		try {
			const isAuthenticated = await userIsAuthenticated(req, res, AuthenticationService);
			if (!isAuthenticated) return res.status(401).json({ error: 'Not authenticated' });

			const body = await readBody(req);
			const result = await apiRequestBuffered('POST', '/dynamo/patch-simulations', { body });
			res.status(result.statusCode || 502);
			res.set('Content-Type', result.headers['content-type'] || 'application/json');
			res.send(result.body);
		} catch (err) {
			console.error('[patch-simulation] POST error:', err);
			res.status(502).json({ error: 'API unreachable', message: err.message, code: err.code });
		}
	});

	router.get('/dynamo/patch-simulations/:simulation_id', async (req, res) => {
		try {
			const isAuthenticated = await userIsAuthenticated(req, res, AuthenticationService);
			if (!isAuthenticated) return res.status(401).json({ error: 'Not authenticated' });
			const { simulation_id } = req.params;
			const result = await apiRequestBuffered('GET', `/dynamo/patch-simulations/${encodeURIComponent(simulation_id)}`);
			res.status(result.statusCode || 502);
			res.set('Content-Type', result.headers['content-type'] || 'application/json');
			res.send(result.body);
		} catch (err) {
			console.error('[patch-simulation] GET error:', err);
			res.status(502).json({ error: 'API unreachable', message: err.message, code: err.code });
		}
	});

	// Stream a per-experiment morph-target GLB. Mirror of the yarn endpoint:
	// keeps response streaming so the first chunk hits the browser as soon as
	// the API has it; build+cache happens on the API side.
	router.get('/assets/patch-simulation/:simulation_id/visualization/:experiment.glb', (req, res) => {
		const { simulation_id, experiment } = req.params;
		const upstreamPath = `/dynamo/patch-simulations/${encodeURIComponent(simulation_id)}/visualization/${encodeURIComponent(experiment)}.glb`;
		const upstream = http.request(
			`http://${API_INTERNAL}${upstreamPath}`,
			{ method: 'GET', headers: { Authorization: `Bearer ${API_SECRET_KEY}` } },
			(apiRes) => {
				res.status(apiRes.statusCode || 502);
				res.set('Content-Type', apiRes.headers['content-type'] || 'model/gltf-binary');
				if (apiRes.headers['cache-control']) res.set('Cache-Control', apiRes.headers['cache-control']);
				if (apiRes.headers['content-length']) res.set('Content-Length', apiRes.headers['content-length']);
				if (apiRes.headers['x-cache']) res.set('X-Cache', apiRes.headers['x-cache']);
				apiRes.pipe(res);
			}
		);
		upstream.on('error', (err) => {
			console.error('[patch-simulation] GLB proxy error:', err);
			if (!res.headersSent) {
				res.status(502).json({ error: 'API unreachable', message: err.message, code: err.code });
			} else {
				res.end();
			}
		});
		upstream.end();
	});

	// Download ZIP (simulation.json only for now; polar-plot PNGs land here once
	// the new DynaMo schema is in place). Streamed and the Content-Disposition
	// is forwarded so the browser triggers a save dialog.
	router.get('/dynamo/patch-simulations/:simulation_id/download.zip', (req, res) => {
		const { simulation_id } = req.params;
		const upstreamPath = `/dynamo/patch-simulations/${encodeURIComponent(simulation_id)}/download.zip`;
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
			console.error('[patch-simulation] download.zip proxy error:', err);
			if (!res.headersSent) {
				res.status(502).json({ error: 'API unreachable', message: err.message, code: err.code });
			} else {
				res.end();
			}
		});
		upstream.end();
	});
};
