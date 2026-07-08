// EGI Check-In OIDC helpers.
//
// This module wraps the raw HTTP + crypto plumbing needed to run the
// authorization-code flow against EGI's Keycloak, so the route handlers
// stay small. We deliberately do NOT decode/verify id_tokens ourselves —
// instead we exchange the code for an access token, then call EGI's
// userinfo endpoint. If EGI accepts the token there, it's proof enough
// that the auth session is legitimate.

const crypto = require('crypto');
const https = require('https');
const { URL, URLSearchParams } = require('url');

// EGI configuration pulled from env. All three URLs are required at boot.
export const EGI = {
    authorizeUrl: process.env.EGI_AUTHORIZE_URL,
    tokenUrl:     process.env.EGI_TOKEN_URL,
    userinfoUrl:  process.env.EGI_USERINFO_URL,
    clientId:     process.env.EGI_CLIENT_ID,
    clientSecret: process.env.EGI_CLIENT_SECRET,
    // Callback URL Directus (and EGI) know about. Must match a redirect_uri
    // registered on the EGI service, byte for byte.
    redirectUri: `${process.env.PUBLIC_URL || 'http://localhost:8055'}/archive/user/egi-callback`,
};

// Generate a URL-safe random string (base64url, no padding).
export const randomToken = (bytes = 32) =>
    crypto.randomBytes(bytes).toString('base64url');

// PKCE (S256): produce a verifier the client keeps (in a cookie) and a challenge (hash) sent to EGI.
export const generatePkcePair = () => {
    const verifier = randomToken(32);
    const challenge = crypto.createHash('sha256').update(verifier).digest('base64url');
    return { verifier, challenge };
};

// Minimal Promise-wrapped https request. Parses JSON responses automatically.
// Returns { status, headers, body }. Rejects only on transport errors.
const httpsRequest = ({ url, method = 'GET', headers = {}, body = null }) =>
    new Promise((resolve, reject) => {
        const u = new URL(url);
        const req = https.request({
            hostname: u.hostname,
            port: u.port || 443,
            path: u.pathname + u.search,
            method,
            headers,
        }, (res) => {
            const chunks = [];
            res.on('data', (chunk) => chunks.push(chunk));
            res.on('end', () => {
                const raw = Buffer.concat(chunks).toString('utf8');
                const ctype = (res.headers['content-type'] || '').toLowerCase();
                let parsed = raw;
                if (ctype.includes('application/json')) {
                    try { parsed = JSON.parse(raw); } catch { /* keep raw */ }
                }
                resolve({ status: res.statusCode, headers: res.headers, body: parsed });
            });
        });
        req.on('error', reject);
        if (body) req.write(body);
        req.end();
    });

// Exchange the authorization code (send the verifier to EGI to hash it and compare it with the challenge) for tokens (access, id, refresh).
// Throws if EGI didn't return a valid access_token.
export const exchangeCodeForTokens = async (code, codeVerifier) => {
    const body = new URLSearchParams({
        grant_type: 'authorization_code',
        code,
        redirect_uri: EGI.redirectUri,
        client_id: EGI.clientId,
        client_secret: EGI.clientSecret,
        code_verifier: codeVerifier,
    }).toString();

    const { status, body: data } = await httpsRequest({
        url: EGI.tokenUrl,
        method: 'POST',
        headers: {
            'Content-Type': 'application/x-www-form-urlencoded',
            'Accept': 'application/json',
        },
        body,
    });

    if (status !== 200 || !data?.access_token) {
        throw new Error(`[EGI] Token exchange failed (${status}): ${JSON.stringify(data)}`);
    }
    return data;
};

// Call EGI's userinfo endpoint with the access token. This is the point at which
// EGI validates the token server-side; a 200 with an email claim is our proof
// that the authentication actually happened at EGI/ORCID.
export const fetchUserInfo = async (accessToken) => {
    const { status, body: data } = await httpsRequest({
        url: EGI.userinfoUrl,
        method: 'GET',
        headers: {
            'Authorization': `Bearer ${accessToken}`,
            'Accept': 'application/json',
        },
    });

    if (status !== 200) {
        throw new Error(`[EGI] Userinfo failed (${status}): ${JSON.stringify(data)}`);
    }
    if (!data?.email) {
        throw new Error(`[EGI] Userinfo returned no email claim: ${JSON.stringify(data)}`);
    }
    return data;
};
