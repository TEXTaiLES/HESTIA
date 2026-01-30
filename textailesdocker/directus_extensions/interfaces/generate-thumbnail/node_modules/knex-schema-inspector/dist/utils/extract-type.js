"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
/**
 * Extracts the type out of a given datatype
 * For example: `varchar(32)` => varchar
 */
function extractType(type) {
    return type.replace(/[^a-zA-Z]/g, '').toLowerCase();
}
exports.default = extractType;
