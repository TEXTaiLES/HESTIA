// Internal helper: get user role from access token (JWT).
// Token is refreshed once per request; result is cached in res.locals.userRole.
const getUserRole = async (req, res, AuthenticationService) => {
    // Check if we already retrieved the role in this request (caching)
    if (res.locals?.userRole !== undefined) {
        return res.locals.userRole;
    }

    // Validation is occurred by refreshing the token.
    // See: https://github.com/directus/directus/discussions/10841
    const cookieName = process.env.REFRESH_TOKEN_COOKIE_NAME;
    if (!req.cookies[cookieName]) {
        res.locals = res.locals || {};
        res.locals.isAuthenticated = false;
        res.locals.userRole = null;
        return null;
    }

    const auth = new AuthenticationService({
        schema: req.schema,
        accountability: req.accountability,
    });

    try {
        const result = await auth.refresh(req.cookies[cookieName], { session: true });
        // Token refresh succeeded → user is authenticated regardless of role
        res.locals.isAuthenticated = true;
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
        console.error('[Auth] Refresh failed:', error.message);
        // Refresh failed - clear the cookie
        res.clearCookie(process.env.REFRESH_TOKEN_COOKIE_NAME, { domain: process.env.REFRESH_TOKEN_COOKIE_DOMAIN, path: '/' });
        res.locals.isAuthenticated = false;
    }

    // Cache null result
    res.locals = res.locals || {};
    res.locals.userRole = null;
    return null;
};

// Helper: Check whether user is authenticated.
// Returns true if the refresh token is valid, regardless of whether a role is assigned.
export const userIsAuthenticated = async (req, res, AuthenticationService) => {
    await getUserRole(req, res, AuthenticationService);
    return res.locals.isAuthenticated ?? false;
};

// Helper: Check if the authenticated user has a specific permission on a collection.
// Results are cached in res.locals.permissions to avoid duplicate DB queries per request.
// Usage: hasPermission(req, res, ItemsService, 'artefacts', 'create')
export const hasPermission = async (req, res, ItemsService, collection, action) => {
    const userRole = res.locals?.userRole;

    if (!userRole) {
        return false;
    }

    // Check cache
    const cacheKey = `${collection}:${action}`;
    if (res.locals.permissions?.[cacheKey] !== undefined) {
        return res.locals.permissions[cacheKey];
    }

    const setCache = (value) => {
        res.locals.permissions = res.locals.permissions || {};
        res.locals.permissions[cacheKey] = value;
        return value;
    };

    try {
        const rolesService = new ItemsService('directus_roles', { schema: req.schema });
        const role = await rolesService.readOne(userRole, { fields: ['admin_access'] });

        // Admins have full access
        if (role?.admin_access) {
            return setCache(true);
        }

        const permissionsService = new ItemsService('directus_permissions', { schema: req.schema });
        const perms = await permissionsService.readByQuery({
            filter: {
                role: { _eq: userRole },
                collection: { _eq: collection },
                action: { _eq: action }
            },
            limit: 1
        });

        return setCache(perms.length > 0);
    } catch (error) {
        console.error(`[Auth] Failed to check permission ${action} on ${collection}:`, error.message);
        return setCache(false);
    }
};