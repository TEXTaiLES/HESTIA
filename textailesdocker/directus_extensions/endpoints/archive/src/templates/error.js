import { renderNavbar } from './navbar.js';
import { renderHtmlPage, renderFooter } from './layout.js';

export const render401Page = ({ activePage }) => {
    const content = `
${renderNavbar(activePage, true)}
<!-- Hero Section -->
<div class="hero-section">
    <div class="container">
        <h1>401 — Unauthorized</h1>
    </div>
</div>

<div class="container mb-5">
    <div class="row mt-5">
        <div class="col-12 text-center">
            <div class="alert alert-danger" role="alert" style="max-width: 600px; margin: 0 auto; border-radius: 12px;">
                <i class="fas fa-ban fa-3x mb-3"></i>
                <h4 class="mb-3">Access Denied</h4>
                <p>You don't have permission to access this page.</p>
            </div>
        </div>
    </div>
</div>
${renderFooter()}
`;

    return renderHtmlPage({
        title: '401 Unauthorized - Digital TEXTaiLES Archive',
        content
    });
};
