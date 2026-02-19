// Helper: Check whether user is authenticated by checking the refresh token cookie.
export const userIsAuthenticated = async (req, res, AuthenticationService) => {
    // Validation is occurred by refreshing the token.
    // See: https://github.com/directus/directus/discussions/10841
    const cookieName = process.env.REFRESH_TOKEN_COOKIE_NAME;
    if (req.cookies[cookieName]) {
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
                return true;
            }
        } catch { }  // refresh will throw an exception for invalid credentials or a suspended user, so it should fail silenty.
    }

    return false;
};
