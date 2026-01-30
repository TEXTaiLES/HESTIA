"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
exports.getSimpleHash = void 0;
/**
 * Generate a simple short hash for a given string
 * This is not cryptographically secure in any way, and has a high chance of collision
 */
function getSimpleHash(str) {
    let hash = 0;
    for (let i = 0; i < str.length; hash &= hash) {
        hash = 31 * hash + str.charCodeAt(i++);
    }
    return Math.abs(hash).toString(16);
}
exports.getSimpleHash = getSimpleHash;
