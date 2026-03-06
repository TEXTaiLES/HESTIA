import { renderNavbar } from './navbar.js';
import { renderHtmlPage, renderFooter } from './layout.js';

export const renderLoginBox = () => `
<div class="container mb-5">
    <div class="row mt-5">
        <div class="col-12 text-center">
            <div class="alert alert-info" role="alert" style="max-width: 600px; margin: 0 auto;">
                <i class="fas fa-lock fa-3x mb-3"></i>
                <h4>Authentication Required</h4>
                <p>Collections require login to access.</p>

                <!-- Login Form -->
                <form id="loginForm" class="mt-4">
                    <div class="mb-3">
                        <input type="email" class="form-control form-control-lg" id="email" placeholder="Email" required style="border-radius: 8px;">
                    </div>
                    <div class="mb-3">
                        <input type="password" class="form-control form-control-lg" id="password" placeholder="Password" required style="border-radius: 8px;">
                    </div>
                    <button type="submit" class="btn btn-lg w-100">
                        <i class="fas fa-sign-in-alt"></i> Login
                    </button>
                    <div id="loginError" class="alert alert-danger mt-3" style="display: none; border-radius: 8px;"></div>
                    <div id="loginSuccess" class="alert alert-success mt-3" style="display: none; border-radius: 8px;">Login successful! Redirecting...</div>
                </form>

                <script>
                    document.getElementById('loginForm').addEventListener('submit', async (e) => {
                        e.preventDefault();
                        const email = document.getElementById('email').value;
                        const password = document.getElementById('password').value;
                        const errorDiv = document.getElementById('loginError');
                        const successDiv = document.getElementById('loginSuccess');
                        
                        errorDiv.style.display = 'none';
                        successDiv.style.display = 'none';
                        
                        try {
                            // Authenticate with Directus API
                            const response = await fetch('/auth/login', {
                                method: 'POST',
                                headers: { 'Content-Type': 'application/json' },
                                body: JSON.stringify({ email, password, mode: 'cookie' })
                            });
                            
                            if (response.ok) {
                                successDiv.style.display = 'block';
                                // Reload the page.
                                setTimeout(() => {
                                    window.location.reload();
                                }, 1000);
                            } else {
                                const data = await response.json();
                                errorDiv.textContent = data.errors?.[0]?.message || 'Invalid username or password';
                                errorDiv.style.display = 'block';
                            }
                        } catch (error) {
                            errorDiv.textContent = 'Error connecting to Directus: ' + error.message;
                            errorDiv.style.display = 'block';
                        }
                    });
                </script>
            </div>
        </div>
    </div>
</div>`;

export const renderLoginPage = ({ navbar = 'home', title = 'Please login', subtitle = 'This page requires login to access.' }) => {
    const content = `
${renderNavbar(navbar, false)}
<!-- Hero Section -->
<div class="hero-section">
    <div class="container">
        <h1>${title}</h1>
        <p>${subtitle}</p>
    </div>
</div>
${renderLoginBox()}
${renderFooter()}
`
    return renderHtmlPage({
        title: title + ' - Digital TEXTaiLES Archive',
        content
    });
};
