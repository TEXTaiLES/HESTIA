"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
exports.getLanguageFromPath = exports.languageToShort = exports.isLanguage = void 0;
const constants_1 = require("@directus/shared/constants");
function isLanguage(language) {
    return constants_1.EXTENSION_LANGUAGES.includes(language);
}
exports.isLanguage = isLanguage;
function languageToShort(language) {
    if (language === 'javascript') {
        return 'js';
    }
    else {
        return 'ts';
    }
}
exports.languageToShort = languageToShort;
function getLanguageFromPath(path) {
    const fileExtension = path.substring(path.lastIndexOf('.') + 1);
    if (fileExtension === 'js') {
        return 'javascript';
    }
    else if (fileExtension === 'ts') {
        return 'typescript';
    }
    else {
        return fileExtension;
    }
}
exports.getLanguageFromPath = getLanguageFromPath;
