import { Field } from '../types';
import { Ref, ComputedRef } from 'vue';
export declare function useFilterFields<T extends string>(fields: Ref<Field[]>, filters: Record<T, (field: Field) => boolean>): {
    fieldGroups: ComputedRef<Record<Extract<T, string>, Field[]>>;
};
//# sourceMappingURL=use-filter-fields.d.ts.map