import { CSP_POLICY, USE_CASES, USE_CASE_MAP } from '../utils/constants.js';
import { matchesByUseCase } from '../utils/helpers.js';
import { userIsAuthenticated, hasPermission } from '../utils/auth.js';
import { renderLoginPage } from '../templates/login.js';
import { renderNavbar } from '../templates/navbar.js';
import { renderHtmlPage, renderFooter } from '../templates/layout.js';

export default (router, { services }) => {
	const { AuthenticationService, ItemsService } = services;
	
	router.get('/collections/:usecase?', async (req, res) => {
		try {
			// Set response headers.
			res.set('Content-Type', 'text/html');
			res.set('Content-Security-Policy', CSP_POLICY);

			// If the user is not authenticated, load login page.
			const isAuthenticated = await userIsAuthenticated(req, res, AuthenticationService);
			if (!isAuthenticated) {
				const html = renderLoginPage({
					navbar: 'collections',
					title: 'Collections',
					subtitle: 'Explore Our Cultural Heritage Archive',
				});
				return res.send(html);
			}

			// Collections requires read permission; redirect to 401 if not allowed.
			const canRead = await hasPermission(req, res, ItemsService, 'artefacts', 'read');
			if (!canRead) return res.redirect('/archive/error/401');

			// Check create permission to decide whether to show Add New Artefact button.
			const isEditor = await hasPermission(req, res, ItemsService, 'artefacts', 'create');

			const rawParam = req.params.usecase;
			const showCards = !rawParam;
			
			let usecase = rawParam || 'all';
			if (usecase === 'all-use-cases') {
				usecase = 'all';
			} else if (usecase.startsWith('use-case-')) {
				const useCaseNum = usecase.replace('use-case-', '');
				usecase = USE_CASE_MAP[useCaseNum] || usecase; // e.g., '1' -> '1. Textile Artefacts'
			}

    // Connects to Directus database.
	const artefactsService = new ItemsService('artefacts', {
		schema: req.schema,
		accountability: null,
	});

	// Fetches all artefacts from database.
	const allArtefacts = await artefactsService.readByQuery({
		fields: ['id', 'title', 'thumbnail', 'use_case', 'collection', 'source', 'time_period'],
		limit: -1
	});

	// Extract use case number (e.g., from "1. Greek Ancient Textiles" get "1").
	// Since artefact.use_case only contains numbers, we just extract the digit.
	const useCaseNumber = usecase !== 'all' ? usecase.match(/\d+/)?.[0] : null;
	
	// Filter artefacts based on selected use case.
	const filteredArtefacts = usecase === 'all' 
		? allArtefacts 
		: allArtefacts.filter(a => matchesByUseCase(a, usecase, useCaseNumber));

    // Generates HTML for each artefact card.
	const artefactsHtml = filteredArtefacts.length
				? filteredArtefacts.map(a => `
					<div class="col-md-4 col-sm-6 mb-4">
						<a href="/archive/artefacts/${a.id}" class="text-decoration-none">
							<div class="card h-100">
								<div style="height: 200px; background: #f8f9fa; display: flex; align-items: center; justify-content: center;">
									${a.thumbnail ? `<img src="/archive/assets/${a.thumbnail}"
										alt="Thumbnail image of Artefact ${a.title}"
										style="max-width:200px;max-height:200px;">` : `<div class="text-muted">No thumbnail</div>`}
								</div>
								<div class="card-body">
					<h6 class="card-title">${a.title || 'Untitled'}</h6>
					<span class="badge mb-2" style="color: #265d72;">Artefact</span>
					${a.use_case ? `<p class="text-muted small mb-1">Use Case ${a.use_case}</p>` : ''}
					<small class="text-muted">${a.collection || ''} ${a.time_period ? '• ' + a.time_period : ''}</small>
				</div>
			</div>
		</a>
	</div>
`).join('\n')
	: '';

// Handles empty results (no artefacts match the filter).
const allItemsHtml = artefactsHtml 
	? artefactsHtml
	: '<div class="col-12"><p class="text-muted">No artefacts found for this use case</p></div>';

// Generates grid of 8 use case cards
const useCaseMenu = USE_CASES.map(uc => {
				const isActive = uc.key === usecase.toLowerCase();
				const useCaseNumber = uc.key.match(/^(\d+)\./)?.[1];
				const url = uc.key === 'all' 
					? '/archive/collections/all-use-cases' 
					: `/archive/collections/use-case-${useCaseNumber}`;
				
				const count = uc.key === 'all' 
					? allArtefacts.length
					: allArtefacts.filter(a => matchesByUseCase(a, uc.key, useCaseNumber)).length;
				const imageUrl = `/archive/static/${uc.image}`;
				
				return `
					<div class="col-md-4 col-sm-6 mb-3">
						<a href="${url}" class="text-decoration-none">
							<div class="card ${isActive ? 'border-primary' : ''}">
								<img src="${imageUrl}" class="card-img-top" alt="${uc.label}" style="height: 150px; object-fit: contain; padding: 10px;">
								<div class="card-body">
									<h6 class="card-title ${isActive ? 'text-primary' : ''}">${uc.label}</h6>
									<p class="card-text text-muted">${count} artefact(s)</p>
								</div>
							</div>
						</a>
					</div>`;
			}).join('\n');

			const content = `
${renderNavbar('collections', true)}

<!-- Hero Section -->
<div class="hero-section">
    <div class="container">
        <h1>Collections</h1>
        <p>Explore Our Cultural Heritage Archives</p>
    </div>
</div>

<div class="container mb-5">
    <div class="row mt-3">
        <div class="col-0 col-lg-2"></div>
        <div class="col-12 col-lg-10">
			${isEditor ? `
			<!-- Add New Artefact button - only visible to Editor users -->
			<div class="d-flex justify-content-end mb-3">
				<a href="/archive/artefact/add" class="btn btn-primary">
					<i class="fas fa-plus"></i> Add New Artefact
				</a>
			</div>
			` : ''}
			${showCards ? `
				<div class="row mt-4">
					${useCaseMenu}
				</div>
			` : `
				<h2>${usecase === 'all' ? 'All Use Cases' : (USE_CASES.find(uc => uc.key === usecase.toLowerCase())?.label || usecase.toUpperCase())}</h2>
				<p class="text-muted">${filteredArtefacts.length} artefact(s) found</p>
				<div class="row mt-4">
					${allItemsHtml}
				</div>
				<p class="mt-3"><a href="/archive/collections">← Back to Collections</a></p>
			`}
		</div>
    </div>
</div>

${renderFooter()}`;

			const html = renderHtmlPage({
				title: 'Collections - Digital TEXTaiLES Archive',
				content,
				includeModelViewer: false,
				cspPolicy: CSP_POLICY
			});
			res.send(html);
		} catch (error) {
			console.error('Collections error:', error);
			res.status(500).send('Error: ' + error.message);
		}
	});
};
