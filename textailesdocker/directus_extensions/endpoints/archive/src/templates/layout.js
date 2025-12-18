export const renderFooter = () => `
<footer class="footer mt-auto">
    <div class="dark-footer h-75">
        <div class="container py-4">
            <div class="row dark-footer">
                <div class="col-lg-4 col-md-4 col-sm-12 vertical-line-right text-lg-start text-sm-center py-2">
                    <p class="pt-4">
                        <img src="/archive/static/Logos/EN_FundedbytheEU_RGB_NEG-1024x228.png" style="max-width: 350px;">
                    </p>
					<div class="footerLink pt-1">
                        <p>TEXTaiLES is a project funded by the European Commission under Grant Agreement n.101158328. The views and opinions expressed in this website are the sole responsibility of the author and do not necessarily reflect the views of the European Commission.</p>
                    </div>
                </div>
                <div class="col-lg-4 col-md-4 col-sm-12 vertical-line-right text-center pt-4">
                    <p class="pt-4">
                        <img src="/archive/static/Logos/ECHOES_Logo_White_Horizontal_300x300-1024x221.png" style="max-width: 300px;">
                    </p>
					<p>TEXTaiLES is part of the <a href="https://www.echoes-eccch.eu/">ECCCH initiative</a>.</p>
                </div>
                <div class="col-lg-4 col-md-4 col-sm-12 text-center pt-4">
                    <p class="pt-4">
                        <img src="/archive/static/Logos/WBF_SBFI_EU_Frameworkprogramme_E_RGB_neg_hoch.png" style="max-width: 350px;">
                    </p>
                </div>
            </div>
        </div>
    </div>
</footer>`;

export const renderHtmlPage = ({ title, content, includeModelViewer = false, bodyClass = '', cspPolicy }) => `<!DOCTYPE html>
<html lang="en" dir="ltr" data-bs-theme="light">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width,initial-scale=1,shrink-to-fit=no">
    <title>${title}</title>
    <link rel="icon" type="image/png" href="/archive/static/Icons/Icon-Textailes-Colour-RGB-Ver.png">
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/bootstrap/5.1.3/css/bootstrap.min.css" integrity="sha512-GQGU0fMMi238uA+a/bdWJfpUGKUkBdgfFdgBm72SUQ6BeyWjoY/ton0tEjH+OSH9iP4Dfh+7HM0I9f5eR0L/4w==" crossorigin="anonymous" referrerpolicy="no-referrer" />
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.5.0/css/all.min.css" integrity="sha512-Avb2QiuDEEvB4bZJYdft2mNjVShBftLdPG8FJ0V7irTLQ8Uo0qcPxh4Plq7G5tGm0rU+1SPhVotteLpBERwTkw==" crossorigin="anonymous" referrerpolicy="no-referrer" />

    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
    <link href="https://fonts.googleapis.com/css2?family=Lexend+Deca:wght@100..900&display=swap" rel="stylesheet">

    <link type="text/css" href="/archive/static/css/style.css" rel="stylesheet">
    <link type="text/css" href="/archive/static/css/hero.css" rel="stylesheet">
    ${includeModelViewer ? '<script type="module" src="https://ajax.googleapis.com/ajax/libs/model-viewer/3.3.0/model-viewer.min.js"></script>' : ''}
</head>
<body${bodyClass ? ` ${bodyClass}` : ''}>

${content}

<script src="https://cdnjs.cloudflare.com/ajax/libs/jquery/3.6.0/jquery.min.js" integrity="sha512-894YE6QWD5I59HgZOGReFYm4dnWc1Qt5NtvYSaNcOP+u1T9qYdvdihz0PPSiiqn/+/3e7Jo4EaG7TubfWGUrMQ==" crossorigin="anonymous" referrerpolicy="no-referrer"></script>
<script src="https://cdnjs.cloudflare.com/ajax/libs/bootstrap/5.1.3/js/bootstrap.bundle.min.js" integrity="sha512-pax4MlgXjHEPfCwcJLQhigY7+N8rt6bVvWLFyUMuxShv170X53TRzGPmPkZmGBhk+jikR8WBM4yl7A9WMHHqvg==" crossorigin="anonymous" referrerpolicy="no-referrer"></script>
</body>
</html>`;
