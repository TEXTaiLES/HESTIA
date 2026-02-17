import { CSP_POLICY } from '../utils/constants.js';
import { userIsAuthenticated } from '../utils/auth.js';
import { renderLoginPage } from '../templates/login.js';

export default (router, { services }) => {
    const { AuthenticationService } = services;

    router.get('/user/login', async (req, res) => {
        try {
            // Set response headers.
            res.set('Content-Type', 'text/html');
            res.set('Content-Security-Policy', CSP_POLICY);

            // Get redirect_url from query params
            const redirectUrl = req.query.redirect_url;

            // If the user is not authenticated, show the login message.
            const isAuthenticated = await userIsAuthenticated(req, res, AuthenticationService);
            if (!isAuthenticated) {
                const html = renderLoginPage({
                    navbar: 'home',
                    title: 'User Login',
                    subtitle: 'Please login in order to view our collections.',
                    redirectUrl: redirectUrl
                });
                return res.send(html);
            }
            // Otherwise, redirect to the specified URL or homepage.
            if (redirectUrl) {
                res.redirect(redirectUrl);
            } else {
                res.redirect('/archive');
            }
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

                // Remove the cookie by making it expire.
                res.cookie('directus_refresh_token', req.cookies.directus_refresh_token, {
                    maxAge: 0,
                    httpOnly: true
                });
            }
            
            res.redirect('/archive');
        } catch (error) {
            console.error('User Logout error:', error);
            res.status(500).send('Error: ' + error.message);
        }
    });
};
