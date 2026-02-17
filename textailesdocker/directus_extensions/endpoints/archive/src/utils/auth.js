// Helper: Check whether user is authenticated by checking the (default) 'directus_refresh_token' cookie.
export const userIsAuthenticated = async (req, res, AuthenticationService) => {
    // Validation is occured by refreshing the token.
    // See: https://github.com/directus/directus/discussions/10841
    if (req.cookies.directus_refresh_token) {
        const auth = new AuthenticationService({
            schema: req.schema,
            accountability: req.accountability,
        });
        try {
            const result = await auth.refresh(req.cookies.directus_refresh_token, { session: true });
            if (result.refreshToken) {
                // Also refresh the cookie in the response.
                const cookieOptions = {
                    maxAge: result.expires,      // Cookie expiration time in ms
                    httpOnly: true,              // Prevents JavaScript access (security)
                    path: '/'                    // Cookie valid for all paths on the domain
                };

                // Add domain only in production (not localhost)
                const host = req.get('host') || '';
                if (host.includes(process.env.HOST_DOMAIN)) {
                    cookieOptions.domain = process.env.COOKIE_DOMAIN; // Works for all subdomains
                }

                res.cookie('directus_refresh_token', result.refreshToken, cookieOptions); 
                return true;
            }
        } catch { }  // refresh will throw an exception for invalid credentials or a suspended user, so it should fail silenty.
    }

    return false;
};
