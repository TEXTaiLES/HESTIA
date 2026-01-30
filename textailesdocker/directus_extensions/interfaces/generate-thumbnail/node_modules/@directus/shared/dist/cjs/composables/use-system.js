"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
exports.useExtensions = exports.useApi = exports.useStores = void 0;
const vue_1 = require("vue");
const constants_1 = require("../constants");
function useStores() {
    const stores = (0, vue_1.inject)(constants_1.STORES_INJECT);
    if (!stores)
        throw new Error('[useStores]: The stores could not be found.');
    return stores;
}
exports.useStores = useStores;
function useApi() {
    const api = (0, vue_1.inject)(constants_1.API_INJECT);
    if (!api)
        throw new Error('[useApi]: The api could not be found.');
    return api;
}
exports.useApi = useApi;
function useExtensions() {
    const extensions = (0, vue_1.inject)(constants_1.EXTENSIONS_INJECT);
    if (!extensions)
        throw new Error('[useExtensions]: The extensions could not be found.');
    return extensions;
}
exports.useExtensions = useExtensions;
