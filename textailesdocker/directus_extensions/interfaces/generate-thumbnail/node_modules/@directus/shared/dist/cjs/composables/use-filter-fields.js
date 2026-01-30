"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
exports.useFilterFields = void 0;
const vue_1 = require("vue");
function useFilterFields(fields, filters) {
    const fieldGroups = (0, vue_1.computed)(() => {
        const acc = {};
        for (const name in filters) {
            acc[name] = [];
        }
        return fields.value.reduce((acc, field) => {
            for (const name in filters) {
                if (filters[name](field) === false)
                    continue;
                acc[name].push(field);
            }
            return acc;
        }, acc);
    });
    return { fieldGroups };
}
exports.useFilterFields = useFilterFields;
