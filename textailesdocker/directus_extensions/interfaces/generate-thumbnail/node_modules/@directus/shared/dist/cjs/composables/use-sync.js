"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
exports.useSync = void 0;
const vue_1 = require("vue");
function useSync(props, key, emit) {
    return (0, vue_1.computed)({
        get() {
            return props[key];
        },
        set(newVal) {
            emit(`update:${key}`, newVal);
        },
    });
}
exports.useSync = useSync;
