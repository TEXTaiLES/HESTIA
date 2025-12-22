export const renderLogin = (url = '') => `
<div class="container mb-5">
    <div class="row mt-5">
        <div class="col-12 text-center">
            <div class="alert alert-info" role="alert" style="max-width: 600px; margin: 0 auto;">
                <i class="fas fa-lock fa-3x mb-3"></i>
                <h4>Authentication Required</h4>
                <p>Collections can be accessed with login</p>
                
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
                                body: JSON.stringify({ email, password }),
                                credentials: 'include'
                            });
                            
                            if (response.ok) {
                                const data = await response.json();
                                // Check if we got an access token
                                const token = data?.data?.access_token || data?.access_token;
                                
                                if (token) {
                                    // Store token in sessionStorage and redirect with token as query param
                                    sessionStorage.setItem('directus_token', token);
                                    
                                    successDiv.style.display = 'block';
                                    // Redirect to same page with token to authenticate
                                    setTimeout(() => {
                                        window.location.href = '/archive/${url}?access_token=' + token;
                                    }, 1000);
                                } else {
                                    errorDiv.textContent = 'Authentication succeeded but no access token was returned';
                                    errorDiv.style.display = 'block';
                                }
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
