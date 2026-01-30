"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
exports.depluralize = exports.pluralize = void 0;
function pluralize(str) {
    return `${str}s`;
}
exports.pluralize = pluralize;
function depluralize(str) {
    return str.slice(0, -1);
}
exports.depluralize = depluralize;
