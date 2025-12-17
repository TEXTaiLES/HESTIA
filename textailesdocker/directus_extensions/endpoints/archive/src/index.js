// Main entry point - imports and registers all routes
import assetsRoutes from './routes/assets.js';
import collectionsRoutes from './routes/collections.js';
import artefactRoutes from './routes/artefact.js';
import toolboxRoutes from './routes/toolbox.js';
import homeRoutes from './routes/home.js';
import atonRoutes from './routes/aton.js';

export default (router, context) => {
	// Registers all route modules when Directus loads the extension
	// Each route file then registers its endpoints
	assetsRoutes(router, context);
	collectionsRoutes(router, context);
	artefactRoutes(router, context);
	toolboxRoutes(router, context);
	homeRoutes(router, context);
	atonRoutes(router, context);
};
