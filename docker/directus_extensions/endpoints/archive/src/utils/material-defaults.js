/**
 * Reference fiber materials for the Thread and Patch simulation forms.
 *
 * NOTE on duplicate names: the JSON has two entries called "flax, wet"
 * (material_9 and material_10) with very different Young's-modulus values.
 * Given the dry/wet pairing pattern of the other materials, material_9 is
 * almost certainly "flax, dry" (17.5 GPa — the stiffer of the two, matching
 * dry-fiber behavior everywhere else).
 *
 * Two dropdown values are reserved as sentinels — see MATERIAL_NAMES:
 *   ''         → placeholder, no fill
 *   __other__  → reveal a text input for a custom name, no fill
 */

// Full reference data — Young's modulus + tensile strength + description +
// source per material. `youngsModulus` and `tensileStrength` carry mean +
// standard deviation from the source paper.
export const MATERIAL_REFERENCES = {
    'lime bast, dry': {
        youngsModulus:   { unit: 'GPa', value: 10.090, standardDeviation: 3.697 },
        tensileStrength: { unit: 'MPa', value: 131.201, standardDeviation: 38.838 },
        description: 'Strong, flexible fiber harvested from the inner bark of the lime (linden) tree, traditionally used for making rope, matting, and woven textiles.',
        source: 'Harris, S., Haigh, S., Handley, A., and Sampson, W. (2017) Material Choices for Fibre in the Neolithic: An Approach through the Measurement of Mechanical Properties. Archaeometry, 59: 574-591. doi: 10.1111/arcm.12267.',
    },
    'lime bast, wet': {
        youngsModulus:   { unit: 'GPa', value: 6.351, standardDeviation: 2.095 },
        tensileStrength: { unit: 'MPa', value: 90.078, standardDeviation: 33.616 },
        description: 'Strong, flexible fiber harvested from the inner bark of the lime (linden) tree, traditionally used for making rope, matting, and woven textiles.',
        source: 'Harris, S., Haigh, S., Handley, A., and Sampson, W. (2017) Material Choices for Fibre in the Neolithic: An Approach through the Measurement of Mechanical Properties. Archaeometry, 59: 574-591. doi: 10.1111/arcm.12267.',
    },
    'willow bast, dry': {
        youngsModulus:   { unit: 'GPa', value: 4.144, standardDeviation: 1.509 },
        tensileStrength: { unit: 'MPa', value: 72.454, standardDeviation: 35.574 },
        description: 'Fibrous inner bark of willow trees, historically used for cordage, basketry, and binding, known for being flexible and strong, though typically finer and less coarse than lime bast.',
        source: 'Harris, S., Haigh, S., Handley, A., and Sampson, W. (2017) Material Choices for Fibre in the Neolithic: An Approach through the Measurement of Mechanical Properties. Archaeometry, 59: 574-591. doi: 10.1111/arcm.12267.',
    },
    'willow bast, wet': {
        youngsModulus:   { unit: 'GPa', value: 0.946, standardDeviation: 0.383 },
        tensileStrength: { unit: 'MPa', value: 26.762, standardDeviation: 8.486 },
        description: 'Fibrous inner bark of willow trees, historically used for cordage, basketry, and binding, known for being flexible and strong, though typically finer and less coarse than lime bast.',
        source: 'Harris, S., Haigh, S., Handley, A., and Sampson, W. (2017) Material Choices for Fibre in the Neolithic: An Approach through the Measurement of Mechanical Properties. Archaeometry, 59: 574-591. doi: 10.1111/arcm.12267.',
    },
    'willow bast boiled, dry': {
        youngsModulus:   { unit: 'GPa', value: 1.487, standardDeviation: 0.811 },
        tensileStrength: { unit: 'MPa', value: 35.248, standardDeviation: 17.624 },
        description: 'Willow bast that has been simmered in hot water to soften it, making it more pliable and easier to weave or bind without snapping, while retaining its strength.',
        source: 'Harris, S., Haigh, S., Handley, A., and Sampson, W. (2017) Material Choices for Fibre in the Neolithic: An Approach through the Measurement of Mechanical Properties. Archaeometry, 59: 574-591. doi: 10.1111/arcm.12267.',
    },
    'willow bast boiled, wet': {
        youngsModulus:   { unit: 'GPa', value: 1.216, standardDeviation: 0.766 },
        tensileStrength: { unit: 'MPa', value: 31.332, standardDeviation: 16.971 },
        description: 'Willow bast that has been simmered in hot water to soften it, making it more pliable and easier to weave or bind without snapping, while retaining its strength.',
        source: 'Harris, S., Haigh, S., Handley, A., and Sampson, W. (2017) Material Choices for Fibre in the Neolithic: An Approach through the Measurement of Mechanical Properties. Archaeometry, 59: 574-591. doi: 10.1111/arcm.12267.',
    },
    'raffia, dry': {
        youngsModulus:   { unit: 'GPa', value: 3.243, standardDeviation: 0.698 },
        tensileStrength: { unit: 'MPa', value: 107.702, standardDeviation: 24.804 },
        description: 'Natural fiber harvested from the leaves of the raffia palm, known for its lightweight, durable, and silky texture, commonly used in weaving, tying, and decorative crafts.',
        source: 'Harris, S., Haigh, S., Handley, A., and Sampson, W. (2017) Material Choices for Fibre in the Neolithic: An Approach through the Measurement of Mechanical Properties. Archaeometry, 59: 574-591. doi: 10.1111/arcm.12267.',
    },
    'raffia, wet': {
        youngsModulus:   { unit: 'GPa', value: 1.892, standardDeviation: 0.495 },
        tensileStrength: { unit: 'MPa', value: 62.010, standardDeviation: 16.971 },
        description: 'Natural fiber harvested from the leaves of the raffia palm, known for its lightweight, durable, and silky texture, commonly used in weaving, tying, and decorative crafts.',
        source: 'Harris, S., Haigh, S., Handley, A., and Sampson, W. (2017) Material Choices for Fibre in the Neolithic: An Approach through the Measurement of Mechanical Properties. Archaeometry, 59: 574-591. doi: 10.1111/arcm.12267.',
    },
    // Two entries labelled "flax, wet" in the source JSON — likely material_9
    // was meant to be "flax, dry" (matching the dry/wet pattern of every other
    // material and the ~5x-stiffer value).
    'flax, wet (17.5 GPa)': {
        youngsModulus:   { unit: 'GPa', value: 17.523, standardDeviation: 5.293 },
        tensileStrength: { unit: 'MPa', value: 343.342, standardDeviation: 132.507 },
        description: 'Natural bast fiber from the stem of the flax plant, prized for its strength, durability, and softness, and used to make linen cloth, rope, and paper.',
        source: 'Harris, S., Haigh, S., Handley, A., and Sampson, W. (2017) Material Choices for Fibre in the Neolithic: An Approach through the Measurement of Mechanical Properties. Archaeometry, 59: 574-591. doi: 10.1111/arcm.12267.',
    },
    'flax, wet (3.6 GPa)': {
        youngsModulus:   { unit: 'GPa', value: 3.604, standardDeviation: 2.230 },
        tensileStrength: { unit: 'MPa', value: 69.843, standardDeviation: 22.520 },
        description: 'Natural bast fiber from the stem of the flax plant, prized for its strength, durability, and softness, and used to make linen cloth, rope, and paper.',
        source: 'Harris, S., Haigh, S., Handley, A., and Sampson, W. (2017) Material Choices for Fibre in the Neolithic: An Approach through the Measurement of Mechanical Properties. Archaeometry, 59: 574-591. doi: 10.1111/arcm.12267.',
    },
};

// Dropdown list order = insertion order of MATERIAL_REFERENCES. Modal
// templates render one <option> per name.
export const MATERIAL_NAMES = Object.keys(MATERIAL_REFERENCES);

export const CUSTOM_MATERIAL_TOKEN = '__other__';

// Form defaults for the Thread modal — only singleYarnYoungsModulus. Other
// fields stay empty / user-provided.
export const THREAD_MATERIAL_DEFAULTS = Object.fromEntries(
    Object.entries(MATERIAL_REFERENCES).map(([name, m]) => [name, {
        singleYarnYoungsModulus: { unit: m.youngsModulus.unit, value: m.youngsModulus.value },
    }])
);

// Form defaults for a Patch modal side (warp OR weft) — only youngsModulus.
export const PATCH_SIDE_MATERIAL_DEFAULTS = Object.fromEntries(
    Object.entries(MATERIAL_REFERENCES).map(([name, m]) => [name, {
        youngsModulus: { unit: m.youngsModulus.unit, value: m.youngsModulus.value },
    }])
);
