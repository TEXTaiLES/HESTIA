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
                res.cookie('directus_refresh_token', result.refreshToken, {
                    maxAge: result.expires,
                    httpOnly: true
                });
                return true;
            }
        } catch { }  // refresh will throw an exception for invalid credentials or a suspended user, so it should fail silenty.
    }

    return false;
};
