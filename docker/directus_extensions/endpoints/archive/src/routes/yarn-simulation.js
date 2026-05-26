/**
 * Yarn Simulation Proxy Route
 *
 * Forwards portal form submissions to the internal Flask API
 * (POST /dynamo/yarn-simulations) so the browser never needs the
 * API_SECRET_KEY directly.
 */

import { userIsAuthenticated } from '../utils/auth.js';

const http = require('http');

const API_INTERNAL = process.env.API_INTERNAL_ENDPOINT || 'api:5000';
const API_SECRET_KEY = process.env.API_SECRET_KEY || '';

// Pull body from the request. Directus may or may not have parsed it as JSON
// already — fall back to reading the raw stream so we don't silently send {}.
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

export default (router, { services }) => {
	const { AuthenticationService } = services;

	router.post('/dynamo/yarn-simulations', async (req, res) => {
		try {
			const isAuthenticated = await userIsAuthenticated(req, res, AuthenticationService);
			if (!isAuthenticated) {
				return res.status(401).json({ error: 'Not authenticated' });
			}

			const body = await readBody(req);
			const proxyUrl = `http://${API_INTERNAL}/dynamo/yarn-simulations`;
			console.log(`[yarn-simulation] proxying POST → ${proxyUrl} (body bytes: ${Buffer.byteLength(body)})`);

			const proxyReq = http.request(
				proxyUrl,
				{
					method: 'POST',
					headers: {
						'Content-Type': 'application/json',
						'Content-Length': Buffer.byteLength(body),
						Authorization: `Bearer ${API_SECRET_KEY}`
					}
				},
				(apiRes) => {
					let data = '';
					apiRes.on('data', (chunk) => { data += chunk; });
					apiRes.on('end', () => {
						res.status(apiRes.statusCode || 502);
						res.set('Content-Type', apiRes.headers['content-type'] || 'application/json');
						res.send(data);
					});
				}
			);

			proxyReq.on('error', (err) => {
				console.error('[yarn-simulation] proxy error:', err);
				res.status(502).json({
					error: 'API unreachable',
					message: err.message,
					code: err.code,
					target: proxyUrl
				});
			});

			proxyReq.write(body);
			proxyReq.end();
		} catch (error) {
			console.error('[yarn-simulation] route error:', error);
			res.status(500).json({ error: error.message });
		}
	});
};