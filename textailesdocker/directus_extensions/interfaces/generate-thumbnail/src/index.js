import InterfaceComponent from './interface.vue';

export default {
	id: 'generate-thumbnail',
	name: 'Generate Thumbnail',
    description: 'Generate thumbnail from artefacts.gltf_file using /archive/thumbnail/generate',
	icon: 'image',
    component: InterfaceComponent,
    // thumbnail is a UUID (pointing to directus_files)
    types: ['uuid'],
    // make it appear for file fields
    localTypes: ['file'],
};
