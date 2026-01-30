<template>
  <div style="display:flex; gap:12px; align-items:center; flex-wrap:wrap;">
    <div style="min-width:160px;">
      <label style="display:block; font-size:12px; opacity:.8; margin-bottom:4px;">
        View index (0–23)
      </label>
      <select v-model.number="viewIndex" :disabled="disabled || loading">
        <option v-for="n in 24" :key="n - 1" :value="n - 1">{{ n - 1 }}</option>
      </select>
    </div>

    <v-button
      :loading="loading"
      :disabled="disabled || loading || !artefactId || !gltfFileId"
      @click="generate"
    >
      Generate thumbnail
    </v-button>

    <div v-if="hint" style="font-size:12px; opacity:.8;">{{ hint }}</div>
    <div v-if="error" style="font-size:12px; color:var(--danger);">{{ error }}</div>
  </div>
</template>

<script setup>
import { computed, ref, watch } from 'vue';
import { useApi } from '@directus/extensions-sdk';

const props = defineProps({
  value: { type: [String, null], default: null }, // thumbnail file id
  disabled: { type: Boolean, default: false },

  // Directus passes the current item's PK here
  primaryKey: { type: [String, Number], default: null },

  // keep these (harmless), but we won't rely on them
  collection: { type: String, default: null },
  values: { type: Object, default: null },
  item: { type: Object, default: null },
});

const emit = defineEmits(['input']);

const api = useApi();
const loading = ref(false);
const error = ref('');
const viewIndex = ref(7);

// Current item id (artefacts primary key)
const artefactId = computed(() => props.primaryKey ?? null);

// We fetch the GLB file id from the API and store it here
const gltfFileId = ref(null);

const hint = computed(() => {
  if (!artefactId.value) return 'Save the item once so it has an ID.';
  if (!gltfFileId.value) return 'This item has no GLB/GLTF in "gltf_file".';
  return '';
});

// Fetch gltf_file when the item id is available/changes
watch(
  artefactId,
  async (id) => {
    gltfFileId.value = null;
    error.value = '';

    if (!id) return;

    try {
      // Read only what we need
      const res = await api.get(`/items/artefacts/${id}`, {
        params: { fields: 'id,gltf_file' },
      });

      // Directus response shape: { data: { data: { ... } } }
      gltfFileId.value = res?.data?.data?.gltf_file ?? null;
    } catch (e) {
      error.value = e?.message ?? String(e);
    }
  },
  { immediate: true }
);

async function generate() {
  error.value = '';
  loading.value = true;

  try {
    const { data } = await api.post('/archive/thumbnail/generate', {
      artefact_id: artefactId.value,
      glb_file_id: gltfFileId.value,
      view_index: viewIndex.value,
    });

    if (!data?.success) {
      throw new Error(data?.error || 'Thumbnail generation failed');
    }

    // Your endpoint returns thumbnail_id and updates the DB too
    emit('input', data.thumbnail_id);
  } catch (e) {
    error.value = e?.message ?? String(e);
  } finally {
    loading.value = false;
  }
}
</script>
