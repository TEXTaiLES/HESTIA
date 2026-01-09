import { CSP_POLICY } from '../utils/constants.js';
import { userIsAuthenticated } from '../utils/auth.js';
import { renderNavbar } from '../templates/navbar.js';
import { renderHtmlPage, renderFooter } from '../templates/layout.js';

export default (router, { services }) => {
	const { AuthenticationService } = services;
	
	router.get('/toolbox', async (req, res) => {
		try {
			const isAuthenticated = await userIsAuthenticated(req, res, AuthenticationService);

			const tools = [
				{ name: 'AmalthAI', image: 'Logos/Tools/AmalthAI_Logo.png', links: [
					{ label: 'Documentation', url: 'https://textailes.github.io/AmalthAI-documentation/' },
					{ label: 'GitHub', url: 'https://github.com/TEXTaiLES/AmalthAI' },
					{ label: 'Server', url: 'https://amalthai.textailes.athenarc.gr' }
				]},
				{ name: 'THOTH', image: 'Logos/Tools/thoth-logo.png', links: [
					{ label: 'Documentation', url: 'https://textailes.github.io/thoth-documentation/' },
					{ label: 'GitHub', url: 'https://github.com/TEXTaiLES/THOTH' },
					{ label: 'Server', url: 'https://thoth.textailes.athenarc.gr' }
				]},
				{ name: 'ZEUSbot', image: 'Logos/Tools/zeusBot-logo.png', links: [
					{ label: 'Documentation', url: 'https://textailes.github.io/UGV-documentation/' },
					{ label: 'GitHub', url: 'https://github.com/TEXTaiLES/ZEUSbot' },	
				]},
				{ name: 'NEPHELE', image: 'Logos/Tools/nephele_logo_2.png', links: [
					{ label: 'Documentation', url: 'https://textailes.github.io/SAMplify_SuGaR-documentation/' },
					{ label: 'GitHub', url: 'https://github.com/TEXTaiLES/SAMplify_SuGaR' },
					{ label: 'Server', url: '#' }
				]},
				{ name: 'HESTIA', image: 'Icons/Icon-Textailes-Colour-RGB-Ver.png', links: [
					{ label: 'Documentation', url: 'http://textailes.athenarc.gr:5000/docs' },
					{ label: 'GitHub', url: 'https://github.com/TEXTaiLES/HESTIA' }
				]},
				{ name: 'INDRA', image: 'Logos/Tools/env_cond_mon_logo.png', links: [
					{ label: 'Documentation', url: 'https://textailes.github.io/Environmental-Condition-Monitoring-documentation/' },
					{ label: 'GitHub', url: 'https://github.com/TEXTaiLES/Environmental-Condition-Monitoring' },
				]}
			];

			const toolsHtml = tools.map(tool => {
                // Builds image URL from tool.image
				const imageUrl = `/archive/static/${tool.image}`;
				// Creates HTML links (Documentation, GitHub, Server)
                const linksHtml = tool.links.map(link => 
					`<div><a href="${link.url}" class="text-decoration-none text-primary small">${link.label}</a></div>`
				).join('');
				
                // Return HTML card for this tool
				return `
					<div class="col-md-6 mb-3">
						<div class="card">
							<div class="card-body">
								<div class="row align-items-center">
									<div class="col-3">
										<img src="${imageUrl}" alt="${tool.name}" class="img-fluid" style="max-height: 80px; object-fit: contain;">
									</div>
									<div class="col-5">
										<h5 class="card-title mb-0">${tool.name}</h5>
									</div>
									<div class="col-4 text-end">
										${linksHtml}
									</div>
								</div>
							</div>
						</div>
					</div>`;
			}).join('\n');

			const content = `
${renderNavbar('toolbox', isAuthenticated)}

<!-- Hero Section -->
<div class="hero-section">
    <div class="container">
        <h1>Digital Toolbox</h1>
        <p>Our Tools for Cultural Heritage</p>
    </div>
</div>

<div class="container mb-5">
    <div class="row mt-3">
        <div class="col-0 col-lg-2"></div>
        <div class="col-12 col-lg-10">
			<div class="row mt-4">
				${toolsHtml}
			</div>
			<p class="mt-3"><a href="/archive">Back to Home</a></p>
        </div>
    </div>
</div>

${renderFooter()}`;

			const html = renderHtmlPage({
				title: 'Toolbox - Digital TEXTaiLES Archive',
				content,
				cspPolicy: CSP_POLICY
			});

			res.set('Content-Type', 'text/html');
			res.set('Content-Security-Policy', CSP_POLICY);
			res.send(html);
		} catch (error) {
			console.error('Toolbox error:', error);
			res.status(500).send('Error: ' + error.message);
		}
	});
};
