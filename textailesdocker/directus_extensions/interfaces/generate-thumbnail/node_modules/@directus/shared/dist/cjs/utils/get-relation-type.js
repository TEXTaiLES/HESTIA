"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
exports.getRelationType = void 0;
function getRelationType(getRelationOptions) {
    var _a, _b, _c;
    const { relation, collection, field } = getRelationOptions;
    if (!relation)
        return null;
    if (relation.collection === collection &&
        relation.field === field &&
        ((_a = relation.meta) === null || _a === void 0 ? void 0 : _a.one_collection_field) &&
        ((_b = relation.meta) === null || _b === void 0 ? void 0 : _b.one_allowed_collections)) {
        return 'm2a';
    }
    if (relation.collection === collection && relation.field === field) {
        return 'm2o';
    }
    if (relation.related_collection === collection && ((_c = relation.meta) === null || _c === void 0 ? void 0 : _c.one_field) === field) {
        return 'o2m';
    }
    return null;
}
exports.getRelationType = getRelationType;
