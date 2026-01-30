"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
exports.mergeFilters = void 0;
function mergeFilters(filterA, filterB, strategy = 'and') {
    if (!filterA)
        return filterB;
    if (!filterB)
        return filterA;
    return {
        [`_${strategy}`]: [filterA, filterB],
    };
}
exports.mergeFilters = mergeFilters;
