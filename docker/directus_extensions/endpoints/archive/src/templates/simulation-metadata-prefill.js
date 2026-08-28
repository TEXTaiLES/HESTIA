/**
 * Simulation Metadata Prefill
 *
 * Renders a <script> that exposes window.HestiaMetaPrefill — a small client
 * helper that, given a simulation form and the artefact's physics metadata
 * (window.HESTIA_PHYSICS_METADATA), populates and HIDES every form field
 * whose name/data-field matches a metadata key. The value still lands in the
 * submission payload (the input stays in the form, just display:none).
 *
 * Naming contract — no explicit map:
 *   - Top-level inputs   → looked up by `name="<metaKey>"` or `name="<metaKey>_value"/_unit"`
 *   - Patch side fields  → looked up by `data-field="<metaKey>"` on each .patch-side-card
 *
 * The Thread modal has no repeating cards (flat form since the new
 * hierarchyLevel schema replaced the old inputLevelN structure), so its
 * helper only walks the top-level form.
 *
 * Metadata value shapes accepted:
 *   - { unit: "...", value: ... }  → fills value-unit pair inputs
 *   - primitive (string/number)    → fills single text/number/select inputs
 *
 * If the metadata is empty (the situation today), this helper is a no-op:
 * no fields are hidden, nothing changes UX-wise.
 */

export const renderSimulationMetadataPrefillScript = () => `
<script>
(function () {
    function hideWrapper(el) {
        // Walk up to the Bootstrap column the input sits in, so the
        // label+input pair disappears as a unit. Fallback to the input itself.
        const col = el.closest('[class*="col-"]') || el;
        col.style.display = 'none';
    }

    function fillValueUnitTopLevel(form, key, payload) {
        if (!payload || typeof payload !== 'object') return false;
        const v = form.querySelector('[name="' + key + '_value"]');
        const u = form.querySelector('[name="' + key + '_unit"]');
        if (!v && !u) return false;
        if (v && payload.value !== undefined && payload.value !== null) v.value = payload.value;
        if (u && payload.unit !== undefined && payload.unit !== null) u.value = payload.unit;
        if (v) hideWrapper(v);
        if (u) hideWrapper(u);
        return true;
    }

    function fillScalarTopLevel(form, key, val) {
        const el = form.querySelector('[name="' + key + '"]');
        if (!el) return false;
        if (val !== undefined && val !== null) el.value = val;
        hideWrapper(el);
        return true;
    }

    function fillValueUnitInCard(card, key, payload) {
        if (!payload || typeof payload !== 'object') return false;
        const wrapper = card.querySelector('[data-field="' + key + '"]');
        if (!wrapper || !wrapper.classList.contains('input-group')) return false;
        const v = wrapper.querySelector('[data-part="value"]');
        const u = wrapper.querySelector('[data-part="unit"]');
        if (v && payload.value !== undefined && payload.value !== null) v.value = payload.value;
        if (u && payload.unit !== undefined && payload.unit !== null) u.value = payload.unit;
        hideWrapper(wrapper);
        return true;
    }

    function fillScalarInCard(card, key, val) {
        const el = card.querySelector('[data-field="' + key + '"]');
        if (!el) return false;
        // Skip if this is a value-unit input-group (handled by fillValueUnitInCard).
        if (el.classList && el.classList.contains('input-group')) return false;
        if (val !== undefined && val !== null) el.value = val;
        hideWrapper(el);
        return true;
    }

    // Apply every metadata key to:
    //   - top-level form (matches by name=)
    //   - every card matching cardSelector (matches by data-field=)
    function applyPrefill(form, metadata, cardSelector) {
        if (!form || !metadata) return;
        const cards = cardSelector ? form.querySelectorAll(cardSelector) : [];
        for (const [key, val] of Object.entries(metadata)) {
            const isUv = val && typeof val === 'object' && ('unit' in val || 'value' in val);
            // 1) top-level
            if (isUv) fillValueUnitTopLevel(form, key, val);
            else fillScalarTopLevel(form, key, val);
            // 2) inside every card
            cards.forEach(card => {
                if (isUv) fillValueUnitInCard(card, key, val);
                else fillScalarInCard(card, key, val);
            });
        }
    }

    window.HestiaMetaPrefill = {
        applyThread: function (form) {
            // Thread form is flat — no repeating cards.
            applyPrefill(form, window.HESTIA_PHYSICS_METADATA || {}, null);
        },
        applyPatch: function (form) {
            applyPrefill(form, window.HESTIA_PHYSICS_METADATA || {}, '.patch-side-card');
        },
    };
})();
</script>
`;
