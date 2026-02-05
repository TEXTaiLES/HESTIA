// Helper: Check whether user is authenticated by checking the refresh token cookie.
// Helper: Get user role from refresh token (internal function)
export const getUserRole = async (req, res, AuthenticationService) => {
    // Check if we already retrieved the role in this request (caching)
    if (res.locals?.userRole !== undefined) {
        return res.locals.userRole;
    }

    // Validation is occurred by refreshing the token.
    // See: https://github.com/directus/directus/discussions/10841
    const cookieName = process.env.REFRESH_TOKEN_COOKIE_NAME;
    if (!req.cookies[cookieName]) {
        res.locals = res.locals || {};
        res.locals.userRole = null;
        return null;
    }

    const auth = new AuthenticationService({
        schema: req.schema,
        accountability: req.accountability,
    });

    try {
        const result = await auth.refresh(req.cookies[cookieName], { session: true });
        if (result.refreshToken) {
            // Also refresh the cookie in the response.
            const cookieOptions = {
                maxAge: result.expires,                           // Cookie expiration time in ms
                httpOnly: true,                                   // Prevents JavaScript access (security)
                domain: process.env.REFRESH_TOKEN_COOKIE_DOMAIN,  // If set, cookie is also valid for subdomains
                path: '/'                                         // Cookie valid for all paths on the domain
            };
            res.cookie(cookieName, result.refreshToken, cookieOptions);

            // Decode the JWT access token to get user role
            // JWT tokens have 3 parts separated by dots: header.payload.signature (Base64 encoded)
            if (result.accessToken) {
                try {
                    // Split JWT and decode the payload (middle section)
                    const payload = result.accessToken.split('.')[1];
                    const decoded = JSON.parse(Buffer.from(payload, 'base64').toString('utf8'));

                    // Cache the role in res.locals for this request
                    res.locals = res.locals || {};
                    res.locals.userRole = decoded.role;
                    return decoded.role;
                } catch (decodeError) {
                    console.error('[Auth] Failed to decode JWT:', decodeError.message);
                }
            }
        }
    } catch (error) {
        // Refresh failed - clear the cookie
        res.clearCookie(process.env.REFRESH_TOKEN_COOKIE_NAME, { domain: process.env.REFRESH_TOKEN_COOKIE_DOMAIN, path: '/' });
    }

    // Cache null result
    res.locals = res.locals || {};
    res.locals.userRole = null;
    return null;
};

// Helper: Check whether user is authenticated (Editor or Member role)
export const userIsAuthenticated = async (req, res, AuthenticationService) => {
    const userRole = await getUserRole(req, res, AuthenticationService);

    if (!userRole) {
        return false;
    }

    const EDITOR_ROLE_ID = process.env.ARCHIVE_EDITOR_ROLE_ID;
    const MEMBER_ROLE_ID = process.env.ARCHIVE_MEMBER_ROLE_ID;

    if (!EDITOR_ROLE_ID || !MEMBER_ROLE_ID) {
        console.error('[Auth] ARCHIVE_EDITOR_ROLE_ID or ARCHIVE_MEMBER_ROLE_ID environment variable is not set!');
        return false;
    }

    if (userRole === EDITOR_ROLE_ID || userRole === MEMBER_ROLE_ID) {
        return true;  // Grant access to Editor and Member users
    } else {
        // User authenticated but doesn't have required role
        res.clearCookie(process.env.REFRESH_TOKEN_COOKIE_NAME, { domain: process.env.REFRESH_TOKEN_COOKIE_DOMAIN, path: '/' });
        res.locals = res.locals || {};
        res.locals.roleError = true;
        return false;
    }
};

// Helper: Check if the authenticated user has the Editor role (for edit/create operations)
export const userIsEditor = async (req, res, AuthenticationService) => {
    const userRole = await getUserRole(req, res, AuthenticationService);

    if (!userRole) {
        return false;
    }

    const EDITOR_ROLE_ID = process.env.ARCHIVE_EDITOR_ROLE_ID;
    return userRole === EDITOR_ROLE_ID;
};