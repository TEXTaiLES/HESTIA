"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
exports.toArray = void 0;
function toArray(val) {
    if (typeof val === 'string') {
        return val.split(',');
    }
    return Array.isArray(val) ? val : [val];
}
exports.toArray = toArray;
