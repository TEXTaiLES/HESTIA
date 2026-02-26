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
        console.error('[Auth] Refresh failed:', error.message);
        // Refresh failed - clear the cookie
        res.clearCookie(process.env.REFRESH_TOKEN_COOKIE_NAME, { domain: process.env.REFRESH_TOKEN_COOKIE_DOMAIN, path: '/' });
    }

    // Cache null result
    res.locals = res.locals || {};
    res.locals.userRole = null;
    return null;
};

// Helper: Check whether user is authenticated — checks if the role has read or create permission on artefacts
export const userIsAuthenticated = async (req, res, AuthenticationService, ItemsService) => {
    const userRole = await getUserRole(req, res, AuthenticationService);

    if (!userRole) {
        return false;
    }

    try {
        const rolesService = new ItemsService('directus_roles', { schema: req.schema });
        const role = await rolesService.readOne(userRole, { fields: ['admin_access'] });

        // Admins have full access — no need to check directus_permissions
        if (role?.admin_access) {
            return true;
        }

        // Query directus_permissions to check if the role has read or create access on artefacts.
        const permissionsService = new ItemsService('directus_permissions', { schema: req.schema });
        const perms = await permissionsService.readByQuery({
            filter: {
                role: { _eq: userRole },
                collection: { _eq: 'artefacts' },
                action: { _in: ['read', 'create'] }
            },
            limit: 1
        });

        if (perms.length === 0) {
            // Role exists but has no permission on artefacts — clear cookie and flag role error
            res.clearCookie(process.env.REFRESH_TOKEN_COOKIE_NAME, { domain: process.env.REFRESH_TOKEN_COOKIE_DOMAIN, path: '/' });
            res.locals = res.locals || {};
            res.locals.roleError = true;
            return false;
        }

        return true;
    } catch (error) {
        console.error('[Auth] Failed to query directus_permissions:', error.message);
        return false;
    }
};

// Helper: Check if the authenticated user can create artefacts (for showing the add-artefact button)
export const userIsEditor = async (req, res, AuthenticationService, ItemsService) => {
    const userRole = await getUserRole(req, res, AuthenticationService);

    if (!userRole) {
        return false;
    }

    try {
        const rolesService = new ItemsService('directus_roles', { schema: req.schema });
        const role = await rolesService.readOne(userRole, { fields: ['admin_access'] });

        // Admins have full access — treat as editor
        if (role?.admin_access) {
            return true;
        }

        // Query directus_permissions to check if the role has create permission on artefacts.
        const permissionsService = new ItemsService('directus_permissions', { schema: req.schema });
        const perms = await permissionsService.readByQuery({
            filter: {
                role: { _eq: userRole },
                collection: { _eq: 'artefacts' },
                action: { _eq: 'create' }
            },
            limit: 1
        });

        return perms.length > 0;
    } catch (error) {
        console.error('[Auth] Failed to query directus_permissions:', error.message);
        return false;
    }
};