"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
exports.stripQuotes = void 0;
/**
 * Strip leading/trailing quotes from a string and handle null values.
 */
function stripQuotes(value) {
    if (value === null || value === undefined) {
        return null;
    }
    var trimmed = value.trim();
    if ((trimmed.startsWith("'") && trimmed.endsWith("'")) ||
        (trimmed.startsWith('"') && trimmed.endsWith('"'))) {
        return trimmed.slice(1, -1);
    }
    return value;
}
exports.stripQuotes = stripQuotes;
