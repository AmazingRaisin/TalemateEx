<template>
    <div ref="el" class="private-tooltip" :style="position">
        <v-icon size="x-small" class="mr-1">mdi-eye-lock-outline</v-icon>Only {{ text }}
    </div>
</template>

<script>
// Who sees a private part of a message (talemate.private_text): shown at the
// top left of the cursor, or where there's room for it.
export default {
    name: 'PrivateTooltip',
    props: {
        x: { type: Number, default: 0 },
        y: { type: Number, default: 0 },
        text: { type: String, default: '' },
    },
    data() {
        return { width: 0, height: 0 };
    },
    computed: {
        position() {
            const gap = 8;
            let left = this.x - this.width - gap;
            let top = this.y - this.height - gap;
            if (left < 4) {
                left = this.x + gap;
            }
            if (top < 4) {
                top = this.y + gap + 12;
            }
            return { left: `${left}px`, top: `${top}px` };
        },
    },
    watch: {
        text() {
            this.$nextTick(this.measure);
        },
    },
    methods: {
        measure() {
            const rect = this.$refs.el?.getBoundingClientRect();
            if (rect && (rect.width !== this.width || rect.height !== this.height)) {
                this.width = rect.width;
                this.height = rect.height;
            }
        },
    },
    mounted() {
        this.measure();
    },
};
</script>

<style>
.private-tooltip {
    position: fixed;
    pointer-events: none;
    z-index: 3000;
    padding: 2px 8px;
    border-radius: 4px;
    font-size: 12px;
    background: rgba(30, 30, 30, 0.92);
    color: #e0e0e0;
    border: 1px solid rgba(255, 255, 255, 0.15);
    white-space: nowrap;
}
</style>
