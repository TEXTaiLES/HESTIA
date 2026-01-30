"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
exports.deepMap = void 0;
const lodash_1 = require("lodash");
function deepMap(
// eslint-disable-next-line @typescript-eslint/explicit-module-boundary-types
object, iterator, 
// eslint-disable-next-line @typescript-eslint/explicit-module-boundary-types
context) {
    if (Array.isArray(object)) {
        return object.map(function (val, key) {
            return (0, lodash_1.isObjectLike)(val) ? deepMap(val, iterator, context) : iterator.call(context, val, key);
        });
    }
    else if ((0, lodash_1.isObjectLike)(object)) {
        const res = {};
        for (const key in object) {
            const val = object[key];
            if ((0, lodash_1.isObjectLike)(val)) {
                res[key] = deepMap(val, iterator, context);
            }
            else {
                res[key] = iterator.call(context, val, key);
            }
        }
        return res;
    }
    else {
        return object;
    }
}
exports.deepMap = deepMap;
