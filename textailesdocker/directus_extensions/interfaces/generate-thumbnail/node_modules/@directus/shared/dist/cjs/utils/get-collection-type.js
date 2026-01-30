"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
exports.getCollectionType = void 0;
/**
 * Get the type of collection. One of alias | table. (And later: view)
 *
 * @param collection Collection object to get the type of
 * @returns collection type
 */
function getCollectionType(collection) {
    if (collection.schema)
        return 'table';
    if (collection.meta)
        return 'alias';
    return 'unknown';
}
exports.getCollectionType = getCollectionType;
