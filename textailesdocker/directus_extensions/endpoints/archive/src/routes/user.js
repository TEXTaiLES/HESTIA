import { CSP_POLICY } from '../utils/constants.js';
import { renderLoginPage } from '../templates/login.js';

export default (router, { services }) => {
    const { AuthenticationService } = services;

    router.get('/user/login', async (req, res) => {
        try {
            // Set response headers.
            res.set('Content-Type', 'text/html');
            res.set('Content-Security-Policy', CSP_POLICY);

            // Check if user is authenticated by checking the (default) 'directus_refresh_token' cookie.
            // Validation is occured by refreshing the token.
            // See: https://github.com/directus/directus/discussions/10841
            let isAuthenticated = false;
            if (req.cookies.directus_refresh_token) {
                const auth = new AuthenticationService({
                    schema: req.schema,
                    accountability: req.accountability,
                });
                try {
                    const result = await auth.refresh(req.cookies.directus_refresh_token, { session: true });
                    if (result.refreshToken) {
                        isAuthenticated = true;
                        res.cookie('directus_refresh_token', result.refreshToken, {
                            maxAge: result.expires,
                            httpOnly: true
                        });
                    }
                } catch { }  // refresh will throw an exception for invalid credentials or a suspended user, so it should fail silenty.
            }

            // If not authenticated, show login message.
            if (!isAuthenticated) {
                const html = renderLoginPage({
                    navbar: 'home',
                    title: 'User Login',
                    subtitle: 'Please login in order to view our collections.'
                });
                return res.send(html);
            }
            // Otherwise, redirect to the homepage.
            res.redirect('/archive');
        } catch (error) {
            console.error('User Login page error:', error);
            res.status(500).send('Error: ' + error.message);
        }
    });

    router.get('/user/logout', async (req, res) => {
        try {
            if (req.cookies.directus_refresh_token) {
                const auth = new AuthenticationService({
                    schema: req.schema,
                    accountability: req.accountability,
                });
                await auth.logout(req.cookies.directus_refresh_token);
            }
            res.redirect('/archive');
        } catch (error) {
            console.error('User Logout error:', error);
            res.status(500).send('Error: ' + error.message);
        }
    });
};
