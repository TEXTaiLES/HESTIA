import { Filter } from '../types/filter';
import { JoiOptions } from './generate-joi';
import Joi from 'joi';
/**
 * Validate the payload against the given filter rules
 *
 * @param {Filter} filter - The filter rules to check against
 * @param {Record<string, any>} payload - The payload to validate
 * @param {JoiOptions} [options] - Optional options to pass to Joi
 * @returns Array of errors
 */
export declare function validatePayload(filter: Filter, payload: Record<string, any>, options?: JoiOptions): Joi.ValidationError[];
//# sourceMappingURL=validate-payload.d.ts.map