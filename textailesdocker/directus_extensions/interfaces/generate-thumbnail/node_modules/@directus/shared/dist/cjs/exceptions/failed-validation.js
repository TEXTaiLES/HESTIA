"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
exports.FailedValidationException = void 0;
const base_1 = require("./base");
class FailedValidationException extends base_1.BaseException {
    constructor(error) {
        var _a, _b, _c, _d, _e, _f, _g, _h, _j, _k, _l, _m, _o, _p;
        const extensions = {
            field: error.path[0],
        };
        const joiType = error.type;
        // eq | in | null | empty
        if (joiType.endsWith('only')) {
            if (((_a = error.context) === null || _a === void 0 ? void 0 : _a.valids.length) > 1) {
                extensions.type = 'in';
                extensions.valid = (_b = error.context) === null || _b === void 0 ? void 0 : _b.valids;
            }
            else {
                const valid = (_c = error.context) === null || _c === void 0 ? void 0 : _c.valids[0];
                if (valid === null) {
                    extensions.type = 'null';
                }
                else if (valid === '') {
                    extensions.type = 'empty';
                }
                else {
                    extensions.type = 'eq';
                    extensions.valid = (_d = error.context) === null || _d === void 0 ? void 0 : _d.valids[0];
                }
            }
        }
        // neq | nin | nnull | nempty
        if (joiType.endsWith('invalid')) {
            if (((_e = error.context) === null || _e === void 0 ? void 0 : _e.invalids.length) > 1) {
                extensions.type = 'nin';
                extensions.invalid = (_f = error.context) === null || _f === void 0 ? void 0 : _f.invalids;
            }
            else {
                const invalid = (_g = error.context) === null || _g === void 0 ? void 0 : _g.invalids[0];
                if (invalid === null) {
                    extensions.type = 'nnull';
                }
                else if (invalid === '') {
                    extensions.type = 'nempty';
                }
                else {
                    extensions.type = 'neq';
                    extensions.invalid = invalid;
                }
            }
        }
        // gt
        if (joiType.endsWith('greater')) {
            extensions.type = 'gt';
            extensions.valid = (_h = error.context) === null || _h === void 0 ? void 0 : _h.limit;
        }
        // gte
        if (joiType.endsWith('min')) {
            extensions.type = 'gte';
            extensions.valid = (_j = error.context) === null || _j === void 0 ? void 0 : _j.limit;
        }
        // lt
        if (joiType.endsWith('less')) {
            extensions.type = 'lt';
            extensions.valid = (_k = error.context) === null || _k === void 0 ? void 0 : _k.limit;
        }
        // lte
        if (joiType.endsWith('max')) {
            extensions.type = 'lte';
            extensions.valid = (_l = error.context) === null || _l === void 0 ? void 0 : _l.limit;
        }
        // contains
        if (joiType.endsWith('contains')) {
            extensions.type = 'contains';
            extensions.substring = (_m = error.context) === null || _m === void 0 ? void 0 : _m.substring;
        }
        // ncontains
        if (joiType.endsWith('ncontains')) {
            extensions.type = 'ncontains';
            extensions.substring = (_o = error.context) === null || _o === void 0 ? void 0 : _o.substring;
        }
        // required
        if (joiType.endsWith('required')) {
            extensions.type = 'required';
        }
        if (joiType.endsWith('.pattern.base')) {
            extensions.type = 'regex';
            extensions.invalid = (_p = error.context) === null || _p === void 0 ? void 0 : _p.value;
        }
        super(error.message, 400, 'FAILED_VALIDATION', extensions);
    }
}
exports.FailedValidationException = FailedValidationException;
