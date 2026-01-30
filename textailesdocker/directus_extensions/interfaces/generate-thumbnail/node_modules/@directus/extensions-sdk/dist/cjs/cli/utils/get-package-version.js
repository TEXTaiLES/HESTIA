"use strict";
var __importDefault = (this && this.__importDefault) || function (mod) {
    return (mod && mod.__esModule) ? mod : { "default": mod };
};
Object.defineProperty(exports, "__esModule", { value: true });
const execa_1 = __importDefault(require("execa"));
async function getPackageVersion(name, tag = 'latest') {
    const npmView = await (0, execa_1.default)('npm', ['view', name, '--json']);
    const packageInfo = JSON.parse(npmView.stdout);
    return packageInfo['dist-tags'][tag];
}
exports.default = getPackageVersion;
