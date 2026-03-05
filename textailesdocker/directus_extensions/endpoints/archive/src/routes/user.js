import { CSP_POLICY } from '../utils/constants.js';
import { userIsAuthenticated } from '../utils/auth.js';
import { renderLoginPage } from '../templates/login.js';
import { render401Page } from '../templates/error.js';

export default (router, { services }) => {
    const { AuthenticationService, ItemsService } = services;

    router.get('/user/login', async (req, res) => {
        try {
            // Set response headers.
            res.set('Content-Type', 'text/html');
            res.set('Content-Security-Policy', CSP_POLICY);

            // If the user is not authenticated, show access denied page.
            const isAuthenticated = await userIsAuthenticated(req, res, AuthenticationService);
            if (!isAuthenticated) {
                // Not authenticated → 401 page
                if (res.locals?.roleError) return res.status(401).send(render401Page({ activePage: 'home', isAuthenticated: true }));
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
                // Also remove the cookie. Include the domain option to ensure the right cookie is handled.
                res.clearCookie(cookieName, { domain: process.env.REFRESH_TOKEN_COOKIE_DOMAIN, path: '/' });
            }

            res.redirect('/archive');
        } catch (error) {
            console.error('User Logout error:', error);
            res.status(500).send('Error: ' + error.message);
        }
    });
};