import { CSP_POLICY } from '../utils/constants.js';
import { userIsAuthenticated } from '../utils/auth.js';
import { renderNavbar } from '../templates/navbar.js';
import { renderFooter } from '../templates/layout.js';

export default (router, { services }) => {
	const { AuthenticationService, ItemsService } = services;

	router.get('/', async (req, res) => {
		try {
            const isAuthenticated = await userIsAuthenticated(req, res, AuthenticationService);

			// Get total artefact count.
            const artefactsService = new ItemsService('artefacts', {
                schema: req.schema,
                accountability: null,
            });
			const allArtefacts = await artefactsService.readByQuery({
				fields: ['id'],
				limit: -1
			});
			const totalArtefacts = allArtefacts.length;

			// TODO: Make renderHtmlPage take additional CSS and replace below.
            const html = `<!DOCTYPE html>
<html lang="en" dir="ltr" data-bs-theme="light">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width,initial-scale=1,shrink-to-fit=no">
    <title>Digital TEXTaiLES Archive</title>
    <link rel="icon" type="image/png" href="/archive/static/Icons/Icon-Textailes-Colour-RGB-Ver.png">
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/bootstrap/5.1.3/css/bootstrap.min.css" integrity="sha512-GQGU0fMMi238uA+a/bdWJfpUGKUkBdgfFdgBm72SUQ6BeyWjoY/ton0tEjH+OSH9iP4Dfh+7HM0I9f5eR0L/4w==" crossorigin="anonymous" referrerpolicy="no-referrer" />
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.5.0/css/all.min.css" integrity="sha512-Avb2QiuDEEvB4bZJYdft2mNjVShBftLdPG8FJ0V7irTLQ8Uo0qcPxh4Plq7G5tGm0rU+1SPhVotteLpBERwTkw==" crossorigin="anonymous" referrerpolicy="no-referrer" />

    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
    <link href="https://fonts.googleapis.com/css2?family=Lexend+Deca:wght@100..900&display=swap" rel="stylesheet">

    <link type="text/css" href="/archive/static/css/style.css" rel="stylesheet">
    <link type="text/css" href="/archive/static/css/hero.css" rel="stylesheet">
    <link type="text/css" href="/archive/static/css/home.css" rel="stylesheet">
    <script type="module" src="https://ajax.googleapis.com/ajax/libs/model-viewer/3.3.0/model-viewer.min.js"></script>
</head>
<body>

${renderNavbar('home', isAuthenticated)}

<!-- Hero Section -->
<div class="hero-section">
    <div class="container">
        <h1>Digital TEXTaiLES Archive</h1>
        <p>Preserving Cultural Heritage Through 3D Digitization</p>
    </div>
</div>

<!-- About Section -->
<div class="container">
    <div class="about-section">
        <h2>About TEXTaiLES</h2>
        <p>The TEXTaiLES project aims to preserve and digitize cultural heritage artefacts through advanced 3D scanning and AI-powered reconstruction techniques. Our archive brings together ancient textiles, archaeological finds, and museum collections, making them accessible to researchers and the public worldwide.</p>
    </div>

    <!-- Features Grid -->
    <div class="row mb-5 justify-content-center">
        <!-- Collections Card -->
        <div class="col-md-4 mb-4">
            <div class="card feature-card">
                <div class="card-body text-center">
                    <div class="mb-3">
                        <img src="/archive/static/Icons/explore_collections_icon.png" alt="Explore Collections" style="width: 80px; height: 80px;">
                    </div>
                    <h3>Explore Collections</h3>
                    <p>Browse 8 use cases featuring ancient textiles, archaeological artefacts, and museum collections digitized in 3D.</p>
                    <a href="/archive/collections" class="btn btn-primary">View Collections</a>
                </div>
            </div>
        </div>

        <!-- Toolbox Card -->
        <div class="col-md-4 mb-4">
            <div class="card feature-card">
                <div class="card-body text-center">
                    <div class="mb-3">
                        <img src="/archive/static/Icons/toolbox_icon.png" alt="Digital Toolbox" style="width: 80px; height: 80px;">
                    </div>
                    <h3>Digital Toolbox</h3>
                    <p>Explore our comprehensive suite of tools, including our robotic imaging system, 3D reconstruction pipeline, annotation platform, and AI-powered detection, segmentation, and classification tools. Access GitHub repositories, detailed documentation, and live demos to test and implement these solutions for your own projects.</p>
                    <a href="/archive/toolbox" class="btn btn-primary">Explore Tools</a>
                </div>
            </div>
        </div>
    </div>
</div>

<!-- Statistics Bar -->
<div class="stats-bar">
    <div class="container">
        <div class="row">
            <div class="col-md-4 stat-item">
                <div class="stat-number">${totalArtefacts}</div>
                <div class="stat-label">Digitized Artefacts</div>
            </div>
            <div class="col-md-4 stat-item">
                <div class="stat-number">8</div>
                <div class="stat-label">Use Cases</div>
            </div>
            <div class="col-md-4 stat-item">
                <div class="stat-number">6</div>
                <div class="stat-label">Our Tools</div>
            </div>
        </div>
    </div>
</div>

<div class="container mb-5"></div>

${renderFooter()}

<script src="https://cdnjs.cloudflare.com/ajax/libs/jquery/3.6.0/jquery.min.js" integrity="sha512-894YE6QWD5I59HgZOGReFYm4dnWc1Qt5NtvYSaNcOP+u1T9qYdvdihz0PPSiiqn/+/3e7Jo4EaG7TubfWGUrMQ==" crossorigin="anonymous" referrerpolicy="no-referrer"></script>
<script src="https://cdnjs.cloudflare.com/ajax/libs/bootstrap/5.1.3/js/bootstrap.bundle.min.js" integrity="sha512-pax4MlgXjHEPfCwcJLQhigY7+N8rt6bVvWLFyUMuxShv170X53TRzGPmPkZmGBhk+jikR8WBM4yl7A9WMHHqvg==" crossorigin="anonymous" referrerpolicy="no-referrer"></script>
</body>
</html>`;

			res.set('Content-Type', 'text/html');
			res.set('Content-Security-Policy', CSP_POLICY);
			res.send(html);
		} catch (error) {
			console.error('Root route error:', error);
			res.status(500).send('Error: ' + error.message);
		}
	});
};
