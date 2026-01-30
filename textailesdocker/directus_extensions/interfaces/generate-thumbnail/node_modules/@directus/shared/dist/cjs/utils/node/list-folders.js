"use strict";
var __importDefault = (this && this.__importDefault) || function (mod) {
    return (mod && mod.__esModule) ? mod : { "default": mod };
};
Object.defineProperty(exports, "__esModule", { value: true });
exports.listFolders = void 0;
const path_1 = __importDefault(require("path"));
const fs_extra_1 = __importDefault(require("fs-extra"));
async function listFolders(location) {
    const fullPath = path_1.default.resolve(location);
    const files = await fs_extra_1.default.readdir(fullPath);
    const directories = [];
    for (const file of files) {
        const filePath = path_1.default.join(fullPath, file);
        const stats = await fs_extra_1.default.stat(filePath);
        if (stats.isDirectory()) {
            directories.push(file);
        }
    }
    return directories;
}
exports.listFolders = listFolders;
