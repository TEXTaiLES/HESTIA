"use strict";
var __importDefault = (this && this.__importDefault) || function (mod) {
    return (mod && mod.__esModule) ? mod : { "default": mod };
};
Object.defineProperty(exports, "__esModule", { value: true });
exports.ensureExtensionDirs = void 0;
const path_1 = __importDefault(require("path"));
const fs_extra_1 = __importDefault(require("fs-extra"));
const pluralize_1 = require("../pluralize");
async function ensureExtensionDirs(extensionsPath, types) {
    for (const extensionType of types) {
        const dirPath = path_1.default.resolve(extensionsPath, (0, pluralize_1.pluralize)(extensionType));
        try {
            await fs_extra_1.default.ensureDir(dirPath);
        }
        catch {
            throw new Error(`Extension folder "${dirPath}" couldn't be opened`);
        }
    }
}
exports.ensureExtensionDirs = ensureExtensionDirs;
