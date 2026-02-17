// Configuration and constants
// Correct content types for static files, for browser compatibility
export const CONTENT_TYPES = {
	'.ico': 'image/x-icon',
	'.css': 'text/css',
	'.js': 'application/javascript',
	'.download': 'application/javascript',
	'.png': 'image/png',
	'.jpg': 'image/jpeg',
	'.jpeg': 'image/jpeg',
	'.svg': 'image/svg+xml',
	'.gif': 'image/gif',
	'.woff': 'font/woff',
	'.woff2': 'font/woff2',
	'.ttf': 'font/ttf',
	'.eot': 'application/vnd.ms-fontobject'
};

// Static paths used in the application
export const PATHS = {
	STATIC_ROOT: '/directus/extensions/endpoints/archive/static',
	UPLOADS_ROOT: '/directus/uploads',
	FAVICON: 'Icon/Icon-Textailes-Colour-RGB-Ver.png'
};

// Content Security Policy for the web pages
// Allows inline scripts and styles needed for the UI to work
// blob: is needed for model-viewer to load embedded textures from GLB files
export const CSP_POLICY = "default-src 'self'; script-src 'self' 'unsafe-eval' 'unsafe-inline' https://ajax.googleapis.com; script-src-elem 'self' 'unsafe-inline' https://ajax.googleapis.com https://cdnjs.cloudflare.com; connect-src 'self' https://ajax.googleapis.com https://www.gstatic.com https://cdnjs.cloudflare.com blob:; style-src 'self' 'unsafe-inline' https://cdnjs.cloudflare.com https://fonts.googleapis.com; font-src 'self' https://cdnjs.cloudflare.com https://fonts.gstatic.com; img-src 'self' data: blob:; worker-src 'self' blob:; child-src 'self' blob:;";

// ATON Configuration - REST API v2
export const ATON_CONFIG = {
	BASE_URL: 'http://localhost:8080',      // Change this to your ATON server URL
	API_BASE: '/api/v2',                    // ATON REST API v2 endpoint
	THOTH_PATH: '/a/thoth_v2/',             // THOTH annotator path
	DEFAULT_USER: 'textailes'               // Default user for scene creation
};

// Use Cases for showing cards on collection page and filtering artefacts
export const USE_CASES = [
	{ key: 'all', label: 'All Use Cases', image: 'Icons/Icon-Textailes-Colour-RGB-Ver.png' },
	{ key: '1. greek ancient textiles', label: '1. Greek Ancient Textiles', image: 'Use Case Preview/greek-ancient.jpg' },
	{ key: '2. textile collection from pompeii', label: '2. Textile Collection from Pompeii', image: 'Use Case Preview/pompeii.png' },
	{ key: '3. greek bronz age clay sealings', label: '3. Greek Bronz Age clay sealings', image: 'Use Case Preview/bronze-age.jpg' },
	{ key: '4. imprints on human plaster casts from pompeii', label: '4. Imprints on human plaster casts from Pompeii', image: 'Use Case Preview/plaster-casts.jpg' },
	{ key: '5. benaki museum collection', label: '5. Benaki Museum collection', image: 'Use Case Preview/benaki.jpg' },
	{ key: '6. turku cathedral museum collection', label: '6. Turku Cathedral Museum collection', image: 'Use Case Preview/turku-cathedral-museum-collection.png' },
	{ key: '7. opera theatre archive in rome', label: '7. Opera Theatre Archive in Rome', image: 'Use Case Preview/opera.png' },
	{ key: '8. textile museum st. gallen collection', label: '8. Textile Museum St. Gallen Collection', image: 'Use Case Preview/st. gallen.png' }
];

// Mapping use case numbers to their keys for naming the route
export const USE_CASE_MAP = {
	'1': '1. greek ancient textiles',
	'2': '2. textile collection from pompeii',
	'3': '3. greek bronz age clay sealings',
	'4': '4. imprints on human plaster casts from pompeii',
	'5': '5. benaki museum collection',
	'6': '6. turku cathedral museum collection',
	'7': '7. opera theatre archive in rome',
	'8': '8. textile museum st. gallen collection'
};
