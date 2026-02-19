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

            // If the user is not authenticated, show the login message.
            const isAuthenticated = await userIsAuthenticated(req, res, AuthenticationService);
            if (!isAuthenticated) {
                const html = renderLoginPage({
                    navbar: 'home',
                    title: 'User Login',
                    subtitle: 'Please login in order to view our collections.',
                });
                return res.send(html);
            }
            // Otherwise, check redirect_url from query params
            // and redirect to the specified URL or homepage.
            const redirectUrl = req.query.redirect_url || '/archive';
            res.redirect(redirectUrl);
        } catch (error) {
            console.error('User Login page error:', error);
            res.status(500).send('Error: ' + error.message);
        }
    });

    router.get('/user/logout', async (req, res) => {
        try {
            const cookieName = process.env.REFRESH_TOKEN_COOKIE_NAME;
            if (req.cookies[cookieName]) {
                const auth = new AuthenticationService({
                    schema: req.schema,
                    accountability: req.accountability,
                });
                await auth.logout(req.cookies[cookieName]);

                // Remove the cookie by making it expire.
                res.cookie(cookieName, req.cookies[cookieName], {
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
