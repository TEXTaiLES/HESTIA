export class BaseException extends Error {
    constructor(message, status, code, extensions) {
        super(message);
        this.status = status;
        this.code = code;
        this.extensions = extensions || {};
    }
}
