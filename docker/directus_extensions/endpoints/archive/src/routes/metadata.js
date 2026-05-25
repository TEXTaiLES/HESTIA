import { serveFile } from '../utils/helpers.js';

export default (router) => {
    router.get('/metadata', async (req, res) => {
        try {
            serveFile('/directus/extensions/endpoints/archive/static/metadata.html', res, 'text/html');
        } catch (error) {
            console.error('Metadata view error:', error);
            res.status(500).send('Error: ' + error.message);
        }
    });
}