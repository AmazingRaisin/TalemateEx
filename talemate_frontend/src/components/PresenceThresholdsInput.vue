<template>
    <div>
        <div v-for="(value, index) in thresholds" :key="index" class="d-flex align-center">
            <span class="text-caption text-medium-emphasis" style="width: 64px; flex: 0 0 auto">Layer {{ index + 1 }}</span>
            <v-slider
                :model-value="value"
                @update:model-value="setThreshold(index, $event)"
                @end="$emit('change')"
                :min="0"
                :max="100"
                :step="1"
                :disabled="disabled"
                color="primary"
                density="compact"
                hide-details
                class="mx-2"
            ></v-slider>
            <span class="text-caption" style="width: 40px; flex: 0 0 auto; text-align: right">{{ value }}%</span>
        </div>
    </div>
</template>

<script>
import { normalizePresenceThresholds } from '@/utils/presence';

/**
 * Character dependent history: one slider per layered history layer for how
 * much of a summary a character needs to have been present for to see it.
 */
export default {
    name: 'PresenceThresholdsInput',
    props: {
        modelValue: Array,
        disabled: {
            type: Boolean,
            default: false,
        },
    },
    // change: a slider was released (for saving)
    emits: ['update:modelValue', 'change'],
    computed: {
        thresholds() {
            return normalizePresenceThresholds(this.modelValue);
        },
    },
    methods: {
        setThreshold(index, value) {
            const thresholds = [...this.thresholds];
            thresholds[index] = Math.round(value);
            this.$emit('update:modelValue', thresholds);
        },
    },
};
</script>
