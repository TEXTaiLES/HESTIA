export const renderNavbar = (activePage = 'home') => {
	const isActive = (page) => activePage === page ? 'active' : '';
	
	return `
<!-- Social Media Bar -->
<div class="social-media-bar" style="background-color: #265d72; padding: 8px 0;">
    <div class="container">
        <div class="row">
            <div class="col-12 text-end">
                <a href="https://github.com/TEXTaiLES" target="_blank" style="color: #ffffff; margin: 0 8px; text-decoration: none;">
                    <i class="fab fa-github" style="font-size: 20px;"></i>
                </a>
                <a href="https://www.linkedin.com/company/textailes" target="_blank" style="color: #ffffff; margin: 0 8px; text-decoration: none;">
                    <i class="fab fa-linkedin" style="font-size: 20px;"></i>
                </a>
                <a href="https://www.facebook.com/profile.php?id=61573869830011" target="_blank" style="color: #ffffff; margin: 0 8px; text-decoration: none;">
                    <i class="fab fa-facebook" style="font-size: 20px;"></i>
                </a>
                <a href="https://www.instagram.com/textailes.eu/" target="_blank" style="color: #ffffff; margin: 0 8px; text-decoration: none;">
                    <i class="fab fa-instagram" style="font-size: 20px;"></i>
                </a>
                <a href="https://x.com/textailes_eu" target="_blank" style="color: #ffffff; margin: 0 8px; text-decoration: none;">
                    <i class="fab fa-x-twitter" style="font-size: 20px;"></i>
                </a>
                <a href="https://www.youtube.com/channel/UCcrHS8g095U2aCAo45d4eVg" target="_blank" style="color: #ffffff; margin: 0 8px; text-decoration: none;">
                    <i class="fab fa-youtube" style="font-size: 20px;"></i>
                </a>
            </div>
        </div>
    </div>
</div>

<nav id="menu" class="ntnavbar navbar-expand-lg" aria-label="navbar">
    <div class="container">
        <div class="row mt-1">
            <div class="col-md-2 col-sm-12">
                <a class="navbar-brand" href="/archive">
                    <img src="/archive/static/Logos/Logo-Textailes_Logo-Textailes-Colour-RGB-Hor.svg" alt="TEXTaiLES logo" style="margin-top: 10%;">Portal
                </a>
            </div>
            <div class="col-md-10 col-sm-12 text-end">
                <button class="navbar-toggler" type="button" data-bs-toggle="collapse" data-bs-target="#navbarMenu">
                    <span class="navbar-toggler-icon"><i class="fas fa-bars"></i></span>
                </button>
                <div class="collapse navbar-collapse horizontal_border" id="navbarMenu">
                    <ul class="navbar-nav me-auto mb-2 mb-lg-0 nt_mtop text-uppercase">
                        <li class="nav-item">
                            <a class="nav-link py-1 ${isActive('home')}" href="/archive">Home</a>
                        </li>
                        <li class="nav-item">
                            <a class="nav-link py-1 ${isActive('collections')}" href="/archive/collections">Collections</a>
                        </li>
                        <li class="nav-item">
                            <a class="nav-link py-1 ${isActive('toolbox')}" href="/archive/toolbox">Toolbox</a>
                        </li>
                    </ul>
                    <div class="d-flex align-items-center">
                        <a href="/admin/login" class="text-decoration-none">
                            <img src="/archive/static/Icons/header_login_icon.png" alt="Login" style="height: 30px;">
                        </a>
                    </div>
                </div>
            </div>
        </div>
    </div>
</nav>`;
};
