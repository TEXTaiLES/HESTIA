"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
exports.useLayout = void 0;
const vue_1 = require("vue");
const use_system_1 = require("./use-system");
const NAME_SUFFIX = 'wrapper';
const WRITABLE_PROPS = ['selection', 'layoutOptions', 'layoutQuery'];
function isWritableProp(prop) {
    return WRITABLE_PROPS.includes(prop);
}
function createLayoutWrapper(layout) {
    return (0, vue_1.defineComponent)({
        name: `${layout.id}-${NAME_SUFFIX}`,
        props: {
            collection: {
                type: String,
                required: true,
            },
            selection: {
                type: Array,
                default: () => [],
            },
            layoutOptions: {
                type: Object,
                default: () => ({}),
            },
            layoutQuery: {
                type: Object,
                default: () => ({}),
            },
            filter: {
                type: Object,
                default: null,
            },
            filterUser: {
                type: Object,
                default: null,
            },
            filterSystem: {
                type: Object,
                default: null,
            },
            search: {
                type: String,
                default: null,
            },
            showSelect: {
                type: String,
                default: 'multiple',
            },
            selectMode: {
                type: Boolean,
                default: false,
            },
            readonly: {
                type: Boolean,
                default: false,
            },
            resetPreset: {
                type: Function,
                default: null,
            },
            clearFilters: {
                type: Function,
                default: null,
            },
        },
        emits: WRITABLE_PROPS.map((prop) => `update:${prop}`),
        setup(props, { emit }) {
            const state = (0, vue_1.reactive)({ ...layout.setup(props, { emit }), ...(0, vue_1.toRefs)(props) });
            for (const key in state) {
                state[`onUpdate:${key}`] = (value) => {
                    if (isWritableProp(key)) {
                        emit(`update:${key}`, value);
                    }
                    else if (!Object.keys(props).includes(key)) {
                        state[key] = value;
                    }
                };
            }
            return { state };
        },
        render(ctx) {
            return ctx.$slots.default !== undefined ? ctx.$slots.default({ layoutState: ctx.state }) : null;
        },
    });
}
function useLayout(layoutId) {
    const { layouts } = (0, use_system_1.useExtensions)();
    const layoutWrappers = (0, vue_1.computed)(() => layouts.value.map((layout) => createLayoutWrapper(layout)));
    const layoutWrapper = (0, vue_1.computed)(() => {
        const layout = layoutWrappers.value.find((layout) => layout.name === `${layoutId.value}-${NAME_SUFFIX}`);
        if (layout === undefined) {
            return layoutWrappers.value.find((layout) => layout.name === `tabular-${NAME_SUFFIX}`);
        }
        return layout;
    });
    return { layoutWrapper };
}
exports.useLayout = useLayout;
