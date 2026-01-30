"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
/**
 * Extracts the length value out of a given datatype
 * For example: `varchar(32)` => 32
 */
function extractMaxLength(type) {
    var regex = /\(([^)]+)\)/;
    var matches = regex.exec(type);
    if (matches && matches.length > 0 && matches[1]) {
        return Number(matches[1]);
    }
    return null;
}
exports.default = extractMaxLength;
