// Main entry point - imports and registers all routes
import assetsRoutes from './routes/assets.js';
import userRoutes from './routes/user.js';
import collectionsRoutes from './routes/collections.js';
import artefactRoutes from './routes/artefact.js';
import addArtefactRoutes from './routes/add-artefact.js';
import toolboxRoutes from './routes/toolbox.js';
import homeRoutes from './routes/home.js';
import atonRoutes from './routes/aton.js';
import metadataRoutes from './routes/metadata.js';
import yarnSimulationRoutes from './routes/yarn-simulation.js';

export default (router, context) => {
	// Registers all route modules when Directus loads the extension
	// Each route file then registers its endpoints
	assetsRoutes(router, context);
	userRoutes(router, context);
	collectionsRoutes(router, context);
	artefactRoutes(router, context);
	addArtefactRoutes(router, context);
	toolboxRoutes(router, context);
	homeRoutes(router, context);
	atonRoutes(router, context);
	metadataRoutes(router, context);
	yarnSimulationRoutes(router, context);
};
