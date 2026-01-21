export const renderBreadcrumb = (title = 'Collections') => {
	return `
<!-- Breadcrumbs -->
<div class="row mt-3">
	<div class="col-0 col-lg-2"></div>
	<div class="col-12 col-lg-8">
		<nav aria-label="breadcrumb">
			<ol class="breadcrumb">
				<li class="breadcrumb-item"><a href="/archive">Home</a></li>
				<li class="breadcrumb-item"><a href="/archive/collections">Collections</a></li>
				<li class="breadcrumb-item active" aria-current="page">${title}</li>
			</ol>
		</nav>
	</div>
</div>`;
};
