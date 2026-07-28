// Custom EGI Check-In SSO handler.
//
// Two endpoints:
//   GET /archive/user/egi-login    → build EGI auth URL, set state cookies, redirect
//   GET /archive/user/egi-callback → validate state, exchange code, look up user,
//                                    mint Directus session, redirect back
//
// We bypass Directus 9.22's built-in openid/oauth2 drivers because both hit
// version-specific bugs against EGI's Keycloak (see docker-compose.yml comment).
// This handler talks OAuth directly using our own utils/egi.js helper.

import {
    EGI,
    randomToken,
    generatePkcePair,
    exchangeCodeForTokens,
    fetchUserInfo,
} from '../utils/egi.js';

const crypto = require('crypto');

// Short-lived cookies for the OAuth handshake (state + PKCE verifier + intended
// landing page). Distinct from Directus's session refresh_token cookie.
const STATE_COOKIE = 'egi_state';
const VERIFIER_COOKIE = 'egi_pkce_verifier';
const REDIRECT_COOKIE = 'egi_final_redirect';

const TEMP_COOKIE_OPTIONS = {
    httpOnly: true,
    sameSite: 'lax',
    path: '/archive/user/',
    maxAge: 10 * 60 * 1000, // 10 minutes — plenty for the round trip to EGI
    secure: (process.env.PUBLIC_URL || '').startsWith('https'),
};

// Allow same-origin paths, or absolute URLs whose hostname sits under the
// shared cookie domain (so cross-tool SSO redirects like
// https://nephele.textailes.athenarc.gr/... are permitted, but arbitrary
// external hosts are not — prevents open-redirect abuse of ?redirect_url=).
const sanitizeRedirect = (raw) => {
    if (!raw || typeof raw !== 'string') return '/archive';
    if (raw.startsWith('//')) return '/archive';
    if (raw.startsWith('/')) return raw;

    try {
        const url = new URL(raw);
        if (url.protocol !== 'https:' && url.protocol !== 'http:') return '/archive';
        const cookieDomain = (process.env.REFRESH_TOKEN_COOKIE_DOMAIN || '').replace(/^\./, '');
        if (!cookieDomain) return '/archive';
        const host = url.hostname.toLowerCase();
        if (host === cookieDomain || host.endsWith('.' + cookieDomain)) return raw;
        return '/archive';
    } catch {
        return '/archive';
    }
};

export default (router, { services }) => {
    const { ItemsService } = services;

    // ------------------------------------------------------------------
    // /user/egi-login  →  redirect to EGI's authorization endpoint
    // ------------------------------------------------------------------
    router.get('/user/egi-login', async (req, res) => {
        try {
            // HttpOnly cookies (not accessible via JavaScript, so we cannot store the final redirect in a JS variable. Instead, we store it in a cookie and read it back in the callback)            const state = randomToken(32);
            const state = randomToken(32);
            const { verifier, challenge } = generatePkcePair(); // CSRF check , PKCE pair for OAuth handshake
            const finalRedirect = sanitizeRedirect(req.query.redirect_url || req.query.redirect);

            res.cookie(STATE_COOKIE, state, TEMP_COOKIE_OPTIONS);
            res.cookie(VERIFIER_COOKIE, verifier, TEMP_COOKIE_OPTIONS);
            res.cookie(REDIRECT_COOKIE, finalRedirect, TEMP_COOKIE_OPTIONS);

            // Build the EGI authorization URL with query parameters.
            const authUrl = new URL(EGI.authorizeUrl);
            authUrl.searchParams.set('response_type', 'code');
            authUrl.searchParams.set('client_id', EGI.clientId);
            authUrl.searchParams.set('redirect_uri', EGI.redirectUri);
            authUrl.searchParams.set('scope', 'openid email profile');
            authUrl.searchParams.set('state', state);
            authUrl.searchParams.set('code_challenge', challenge);
            authUrl.searchParams.set('code_challenge_method', 'S256');

            console.info('[EGI Login] Redirecting to EGI authorize endpoint');
            return res.redirect(authUrl.toString()); // Sends a 302 redirect to the EGI authorization endpoint.
        } catch (err) {
            console.error('[EGI Login] Failed to initiate:', err);
            return res.redirect('/archive?reason=EGI_LOGIN_INIT_FAILED');
        }
    });

    // ------------------------------------------------------------------
    // /user/egi-callback  →  handle EGI's redirect back after auth
    // ------------------------------------------------------------------
    router.get('/user/egi-callback', async (req, res) => {
        const finalRedirect = sanitizeRedirect(req.cookies[REDIRECT_COOKIE]);

        const clearTemps = () => {
            res.clearCookie(STATE_COOKIE, { path: '/archive/user/' });
            res.clearCookie(VERIFIER_COOKIE, { path: '/archive/user/' });
            res.clearCookie(REDIRECT_COOKIE, { path: '/archive/user/' });
        };

        try {
            // If EGI passed back an error (user denied consent, IdP failure, etc.)
            if (req.query.error) {
                console.warn('[EGI Callback] EGI returned error:', req.query.error);
                clearTemps();
                const code = String(req.query.error).toUpperCase().replace(/[^A-Z0-9_]/g, '');
                return res.redirect(`${finalRedirect}?reason=EGI_${code}`);
            }

            const code = req.query.code;
            const state = req.query.state;
            const cookieState = req.cookies[STATE_COOKIE];
            const codeVerifier = req.cookies[VERIFIER_COOKIE];

            if (!code || !state || !cookieState || !codeVerifier) {
                console.warn('[EGI Callback] Missing params or cookies');
                clearTemps();
                return res.redirect(`${finalRedirect}?reason=EGI_MISSING_PARAMS`);
            }

            // CSRF guard: the state we handed to EGI must match the one it sent back.
            if (state !== cookieState) {
                console.warn('[EGI Callback] State mismatch — possible CSRF');
                clearTemps();
                return res.redirect(`${finalRedirect}?reason=EGI_STATE_MISMATCH`);
            }

            // ------------------------------------------------------------
            // Exchange code + PKCE verifier for tokens, then get userinfo.
            // ------------------------------------------------------------
            const tokens = await exchangeCodeForTokens(code, codeVerifier);
            const userinfo = await fetchUserInfo(tokens.access_token);
            const email = userinfo.email;
            console.info(`[EGI Callback] Authenticated at EGI: email=${email}`);

            // ------------------------------------------------------------
            // Match the returning identity to a Directus user by email.
            // System-level ItemsService (accountability: null) bypasses row-level
            // permissions — this is a trusted authentication path.
            // ------------------------------------------------------------
            const usersService = new ItemsService('directus_users', {
                schema: req.schema,
                accountability: null,
            });
            const matches = await usersService.readByQuery({
                filter: { email: { _eq: email } },
                fields: ['id', 'email', 'status'],
                limit: 1,
            });

            if (matches.length === 0) {
                console.warn(`[EGI Callback] No Directus user for email=${email}`);
                clearTemps();
                return res.redirect(`${finalRedirect}?reason=EGI_USER_NOT_REGISTERED`);
            }
            if (matches[0].status !== 'active') {
                console.warn(`[EGI Callback] Directus user inactive: email=${email}`);
                clearTemps();
                return res.redirect(`${finalRedirect}?reason=EGI_USER_INACTIVE`);
            }
            const userId = matches[0].id;

            // ------------------------------------------------------------
            // Mint a Directus session for this user by inserting a session row
            // and setting the refresh_token cookie. On the next request, the
            // archive's userIsAuthenticated() calls AuthenticationService.refresh()
            // with this cookie, which mints an access token and rotates the
            // refresh token — same path any normal Directus login uses.
            // ------------------------------------------------------------
            const refreshToken = crypto.randomBytes(32).toString('base64url');
            const refreshTokenTTL = 7 * 24 * 60 * 60 * 1000; // 7 days
            const expires = new Date(Date.now() + refreshTokenTTL);

            const sessionsService = new ItemsService('directus_sessions', {
                schema: req.schema,
                accountability: null,
            });
            await sessionsService.createOne({
                token: refreshToken,
                user: userId,
                expires,
                ip: req.ip,
                user_agent: req.headers['user-agent'] || null,
                origin: req.headers.origin || req.headers.referer || null,
            });

            const cookieName = process.env.REFRESH_TOKEN_COOKIE_NAME;
            res.cookie(cookieName, refreshToken, {
                maxAge: refreshTokenTTL,
                httpOnly: true,
                domain: process.env.REFRESH_TOKEN_COOKIE_DOMAIN,
                path: '/',
                secure: process.env.REFRESH_TOKEN_COOKIE_SECURE === 'true' ||
                        (process.env.PUBLIC_URL || '').startsWith('https'),
                sameSite: process.env.REFRESH_TOKEN_COOKIE_SAME_SITE || 'lax',
            });

            console.info(`[EGI Callback] Session issued for user id=${userId}, redirecting to ${finalRedirect}`);
            clearTemps();
            return res.redirect(finalRedirect);
        } catch (err) {
            console.error('[EGI Callback] Failed:', err);
            clearTemps();
            return res.redirect(`${finalRedirect}?reason=EGI_CALLBACK_ERROR`);
        }
    });
};
