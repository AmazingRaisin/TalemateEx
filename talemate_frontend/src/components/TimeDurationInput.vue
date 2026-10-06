<template>
    <div class="d-flex flex-wrap ga-2">
        <v-number-input
            v-for="unit in units"
            :key="unit"
            :model-value="modelValue[unit]"
            @update:model-value="setUnit(unit, $event)"
            :label="toTitle(unit)"
            :min="0"
            :step="1"
            control-variant="stacked"
            density="compact"
            hide-details
            :disabled="disabled"
            style="width: 110px; flex: 0 0 auto"
        />
    </div>
</template>

<script>
import { DURATION_UNITS } from '@/utils/time';

/**
 * Multi-unit duration picker (years, months, weeks, days, hours, minutes).
 *
 * v-model is an object like { years: 2, months: 0, weeks: 3, days: 0, hours: 2, minutes: 0 }.
 * Convert it with componentsToIsoDuration() from utils/time.
 */
export default {
    name: 'TimeDurationInput',
    props: {
        modelValue: {
            type: Object,
            required: true,
        },
        disabled: {
            type: Boolean,
            default: false,
        },
    },
    emits: ['update:modelValue'],
    data() {
        return {
            units: DURATION_UNITS,
        };
    },
    methods: {
        setUnit(unit, value) {
            this.$emit('update:modelValue', {
                ...this.modelValue,
                [unit]: Math.max(0, parseInt(value) || 0),
            });
        },
        toTitle(unit) {
            return unit.charAt(0).toUpperCase() + unit.slice(1);
        },
    },
};
</script>
