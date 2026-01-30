"use strict";
var __importDefault = (this && this.__importDefault) || function (mod) {
    return (mod && mod.__esModule) ? mod : { "default": mod };
};
Object.defineProperty(exports, "__esModule", { value: true });
exports.build = exports.create = void 0;
const create_1 = __importDefault(require("./commands/create"));
exports.create = create_1.default;
const build_1 = __importDefault(require("./commands/build"));
exports.build = build_1.default;
