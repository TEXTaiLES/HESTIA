"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
exports.BaseException = void 0;
class BaseException extends Error {
    constructor(message, status, code, extensions) {
        super(message);
        this.status = status;
        this.code = code;
        this.extensions = extensions || {};
    }
}
exports.BaseException = BaseException;
