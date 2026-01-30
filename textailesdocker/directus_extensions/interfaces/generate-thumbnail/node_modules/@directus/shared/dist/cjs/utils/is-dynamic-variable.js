"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
exports.isDynamicVariable = void 0;
const dynamicVariablePrefixes = ['$NOW', '$CURRENT_USER', '$CURRENT_ROLE'];
function isDynamicVariable(value) {
    return typeof value === 'string' && dynamicVariablePrefixes.some((prefix) => value.startsWith(prefix));
}
exports.isDynamicVariable = isDynamicVariable;
