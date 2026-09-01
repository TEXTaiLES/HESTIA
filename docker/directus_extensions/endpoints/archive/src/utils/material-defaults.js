/**
 * Material presets for the Thread and Patch simulation forms.
 *
 * When a user picks a material from the dropdown, the form auto-fills every
 * other field with these defaults. Two special dropdown values are reserved:
 *   ''         → placeholder, no fill
 *   __other__  → reveal a text input for a custom material name, no fill
 *
 * Field naming matches the JSON schema (camelCase) so the same key can drive
 * both DOM population and payload building.
 *
 * TODO: Values below are PLACEHOLDERS. Replace with the real per-material
 * defaults once Fraunhofer provides them. The material *names* are also
 * placeholders (Cotton/Linen/Silk) — swap for the real list when known.
 */

// Presets for the Thread modal. Fills EVERY field except structureType,
// hierarchyLevel (user choice), and singleYarnMaterial (the trigger itself).
// Ply-level entries are always filled; they only take effect at hierarchyLevel=2.
export const THREAD_MATERIAL_DEFAULTS = {
    Cotton: {
        friction:                { unit: '1',   value: 0.30 },
        adhesion:                { unit: '1',   value: 0.05 },
        discretizationPeriodCount:         4,
        discretizationNodesPerPeriodCount: 20,
        appliedElongation:       { unit: '%',   value: 5 },
        threadTotalDiameter:     { unit: 'mm',  value: 0.50 },
        threadTwistDirection:    'S',
        threadPitch:             { unit: 'mm',  value: 3.0 },
        threadFoldCount:         2,
        singleYarnDiameter:      { unit: 'mm',  value: 0.25 },
        singleYarnYoungsModulus: { unit: 'MPa', value: 5000 },
        singleYarnPoissonRatio:  { unit: '1',   value: 0.30 },
        plyTotalDiameter:        { unit: 'mm',  value: 0.35 },
        plyTwistDirection:       'Z',
        plyPitch:                { unit: 'mm',  value: 2.0 },
        plyFoldCount:            3,
    },
    Linen: {
        friction:                { unit: '1',   value: 0.35 },
        adhesion:                { unit: '1',   value: 0.06 },
        discretizationPeriodCount:         4,
        discretizationNodesPerPeriodCount: 20,
        appliedElongation:       { unit: '%',   value: 4 },
        threadTotalDiameter:     { unit: 'mm',  value: 0.60 },
        threadTwistDirection:    'Z',
        threadPitch:             { unit: 'mm',  value: 3.5 },
        threadFoldCount:         2,
        singleYarnDiameter:      { unit: 'mm',  value: 0.30 },
        singleYarnYoungsModulus: { unit: 'MPa', value: 30000 },
        singleYarnPoissonRatio:  { unit: '1',   value: 0.35 },
        plyTotalDiameter:        { unit: 'mm',  value: 0.40 },
        plyTwistDirection:       'S',
        plyPitch:                { unit: 'mm',  value: 2.2 },
        plyFoldCount:            3,
    },
    Silk: {
        friction:                { unit: '1',   value: 0.25 },
        adhesion:                { unit: '1',   value: 0.04 },
        discretizationPeriodCount:         5,
        discretizationNodesPerPeriodCount: 24,
        appliedElongation:       { unit: '%',   value: 8 },
        threadTotalDiameter:     { unit: 'mm',  value: 0.30 },
        threadTwistDirection:    'S',
        threadPitch:             { unit: 'mm',  value: 2.5 },
        threadFoldCount:         2,
        singleYarnDiameter:      { unit: 'mm',  value: 0.15 },
        singleYarnYoungsModulus: { unit: 'MPa', value: 10000 },
        singleYarnPoissonRatio:  { unit: '1',   value: 0.33 },
        plyTotalDiameter:        { unit: 'mm',  value: 0.20 },
        plyTwistDirection:       'Z',
        plyPitch:                { unit: 'mm',  value: 1.5 },
        plyFoldCount:            3,
    },
};

// Presets for the Patch modal, applied per side (warp or weft independently).
// Only the intrinsic per-side properties — weavePattern / patternRepetition /
// discretization are not material-dependent so they stay untouched.
export const PATCH_SIDE_MATERIAL_DEFAULTS = {
    Cotton: {
        youngsModulus:        { unit: 'MPa',   value: 5000 },
        poissonRatio:         { unit: '1',     value: 0.30 },
        yarnDiameter:         { unit: 'mm',    value: 0.50 },
        yarnDiameterRatio:    { unit: '1',     value: 0.80 },
        yarnCountPerDistance: { unit: '1/mm',  value: 2.0 },
        yarnFriction:         { unit: '1',     value: 0.30 },
    },
    Linen: {
        youngsModulus:        { unit: 'MPa',   value: 30000 },
        poissonRatio:         { unit: '1',     value: 0.35 },
        yarnDiameter:         { unit: 'mm',    value: 0.60 },
        yarnDiameterRatio:    { unit: '1',     value: 0.85 },
        yarnCountPerDistance: { unit: '1/mm',  value: 1.8 },
        yarnFriction:         { unit: '1',     value: 0.35 },
    },
    Silk: {
        youngsModulus:        { unit: 'MPa',   value: 10000 },
        poissonRatio:         { unit: '1',     value: 0.33 },
        yarnDiameter:         { unit: 'mm',    value: 0.30 },
        yarnDiameterRatio:    { unit: '1',     value: 0.90 },
        yarnCountPerDistance: { unit: '1/mm',  value: 3.0 },
        yarnFriction:         { unit: '1',     value: 0.25 },
    },
};

export const MATERIAL_NAMES = ['Cotton', 'Linen', 'Silk'];
export const CUSTOM_MATERIAL_TOKEN = '__other__';
