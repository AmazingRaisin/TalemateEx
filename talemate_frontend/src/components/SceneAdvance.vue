<template>
    <!-- Control Scene > Advance Scene (talemate.agents.creator.advance_scene) -->
    <v-dialog :model-value="modelValue" max-width="620" scrollable @update:model-value="$emit('update:modelValue', $event)">
        <v-card class="scene-advance">
            <v-card-title>
                <v-icon class="mr-2" size="small" color="primary">mdi-update</v-icon>
                Advance Scene
            </v-card-title>

            <!-- the options -->
            <template v-if="view === 'options'">
                <v-card-text class="pt-0">
                    <p class="text-caption text-muted mb-2">
                        Rewrite the scene's information for where the story is now, from the old values and what has happened since.
                    </p>

                    <div v-for="option in rewriteOptions" :key="option.key" class="advance-option">
                        <v-checkbox
                            v-model="options[option.key]"
                            :disabled="!option.available"
                            :label="option.label"
                            density="compact"
                            color="primary"
                            hide-details
                        ></v-checkbox>
                        <div class="option-hint text-caption text-muted">{{ option.hint }}</div>
                    </div>

                    <v-divider class="my-2"></v-divider>

                    <v-checkbox
                        v-model="options.private_info"
                        label="Use Private Character Information"
                        density="compact"
                        color="primary"
                        hide-details
                    ></v-checkbox>
                    <v-checkbox
                        v-model="options.self_info"
                        :disabled="!options.private_info"
                        label="Use 'Self' Character Information"
                        density="compact"
                        color="primary"
                        hide-details
                        class="ml-8 self-option"
                    ></v-checkbox>
                    <div class="option-hint text-caption text-muted">
                        For the scene's own description and intentions. Overrides are always written from what that character knows.
                    </div>
                    <v-alert
                        v-if="options.private_info"
                        type="warning"
                        variant="tonal"
                        density="compact"
                        class="mt-2 privacy-warning text-body-2"
                    >
                        The scene description and intentions are in every character's prompts, and the narrator's: private or self details used here can become known to everyone.
                    </v-alert>

                    <v-alert v-if="error" type="error" variant="tonal" density="compact" class="mt-2">{{ error }}</v-alert>
                </v-card-text>
                <v-card-actions>
                    <v-btn variant="text" color="cancel" prepend-icon="mdi-cancel" @click="close">Cancel</v-btn>
                    <v-spacer></v-spacer>
                    <v-btn variant="text" color="primary" prepend-icon="mdi-update" :disabled="!canBegin" @click="begin">Begin Advancement</v-btn>
                </v-card-actions>
            </template>

            <!-- running -->
            <template v-else-if="view === 'running'">
                <v-card-text class="pt-0">
                    <div class="mb-2">
                        Rewriting {{ progressLabel }}
                    </div>
                    <div v-if="progress.current" class="text-caption text-muted mb-2">{{ progress.current }}</div>
                    <v-progress-linear
                        :model-value="progressPercent"
                        :indeterminate="!progress.total"
                        color="primary"
                        height="6"
                        rounded
                    ></v-progress-linear>
                </v-card-text>
                <v-card-actions>
                    <v-btn variant="text" color="delete" prepend-icon="mdi-stop" @click="stop">Stop</v-btn>
                    <v-spacer></v-spacer>
                    <v-btn variant="text" @click="close">Hide</v-btn>
                </v-card-actions>
            </template>

            <!-- results -->
            <template v-else>
                <v-card-text class="pt-0">
                    <p class="text-caption text-muted mb-2">
                        The rewrites are in place. Revert any of them while this is open (or reopen it from Control Scene).
                    </p>
                    <v-expansion-panels multiple variant="accordion" class="advance-results">
                        <v-expansion-panel v-for="result in results" :key="result.id" :value="result.id">
                            <v-expansion-panel-title>
                                <span class="result-title">{{ result.title }}</span>
                                <v-spacer></v-spacer>
                                <v-chip :color="statusColor(result.status)" size="x-small" label class="ml-2">{{ statusLabel(result.status) }}</v-chip>
                            </v-expansion-panel-title>
                            <v-expansion-panel-text>
                                <div v-if="result.message" class="text-caption text-warning mb-2">{{ result.message }}</div>
                                <template v-if="result.old_value">
                                    <div class="text-caption text-muted">Before</div>
                                    <div class="result-value mb-2">{{ result.old_value }}</div>
                                </template>
                                <template v-if="result.new_value">
                                    <div class="text-caption text-muted">After</div>
                                    <div class="result-value">{{ result.new_value }}</div>
                                </template>
                                <div v-if="result.status === 'rewritten'" class="d-flex mt-2">
                                    <v-spacer></v-spacer>
                                    <v-btn size="small" variant="text" color="delete" prepend-icon="mdi-undo" @click="revert([result.id])">Revert</v-btn>
                                </div>
                            </v-expansion-panel-text>
                        </v-expansion-panel>
                    </v-expansion-panels>
                </v-card-text>
                <v-card-actions>
                    <v-btn variant="text" color="delete" prepend-icon="mdi-undo" :disabled="!rewrittenIds.length" @click="revert(rewrittenIds)">Undo All</v-btn>
                    <v-spacer></v-spacer>
                    <v-btn variant="text" color="primary" prepend-icon="mdi-check" @click="dismiss">Done</v-btn>
                </v-card-actions>
            </template>
        </v-card>
    </v-dialog>
</template>

<script>
const STATUS_LABELS = {
    rewritten: 'Rewritten',
    failed: 'Failed',
    reverted: 'Reverted',
    cancelled: 'Stopped',
};

const STATUS_COLORS = {
    rewritten: 'success',
    failed: 'error',
    reverted: 'muted',
    cancelled: 'warning',
};

function defaultOptions() {
    return {
        scene_description: false,
        story_intention: false,
        phase_intention: false,
        description_overrides: false,
        intention_overrides: false,
        private_info: false,
        self_info: false,
    };
}

export default {
    name: 'SceneAdvance',
    props: {
        modelValue: Boolean,
        // the agents are working
        busy: Boolean,
    },
    emits: ['update:modelValue'],
    inject: ['getWebsocket', 'registerMessageHandler', 'unregisterMessageHandler'],
    data() {
        return {
            options: defaultOptions(),
            state: null,
            progress: { done: 0, total: 0, current: null },
            error: null,
        };
    },
    computed: {
        available() {
            return (this.state && this.state.available) || {};
        },
        view() {
            if (this.state && this.state.running) {
                return 'running';
            }
            if (this.state && this.state.results) {
                return 'results';
            }
            return 'options';
        },
        rewriteOptions() {
            const available = this.available;
            const overrides = (key, empty) => {
                const info = available[key] || { targets: [], rewrites: 0 };
                if (!info.targets.length) {
                    return { available: false, hint: empty };
                }
                const merged = info.rewrites < info.targets.length
                    ? ` (${info.rewrites} rewrite${info.rewrites === 1 ? '' : 's'}, same values together)`
                    : '';
                return { available: true, hint: info.targets.join(', ') + merged };
            };
            const descriptions = overrides('description_overrides', 'No active character or group has one.');
            const intentions = overrides('intention_overrides', 'No active character or group has one.');
            return [
                {
                    key: 'scene_description',
                    label: 'Rewrite Scene Description',
                    available: !!available.scene_description,
                    hint: available.scene_description ? 'The scene\'s backdrop: setting and situation.' : 'Empty, nothing to rewrite.',
                },
                {
                    key: 'story_intention',
                    label: 'Rewrite Overall Intention',
                    available: !!available.story_intention,
                    hint: available.story_intention ? 'Where the story is heading.' : 'Empty, nothing to rewrite.',
                },
                {
                    key: 'phase_intention',
                    label: 'Rewrite Current Scene Intention',
                    available: !!available.phase_intention,
                    hint: available.phase_intention ? 'What the current part of the story is about.' : 'Empty, nothing to rewrite.',
                },
                {
                    key: 'description_overrides',
                    label: 'Rewrite Characters\' Scene Description Overrides',
                    ...descriptions,
                },
                {
                    key: 'intention_overrides',
                    label: 'Rewrite Characters\' Overall Intention Overrides',
                    ...intentions,
                },
            ];
        },
        selectedRewrites() {
            return this.rewriteOptions.filter(option => option.available && this.options[option.key]);
        },
        canBegin() {
            return !this.busy && this.selectedRewrites.length > 0;
        },
        progressPercent() {
            return this.progress.total ? (100 * this.progress.done) / this.progress.total : 0;
        },
        progressLabel() {
            if (!this.progress.total) {
                return '...';
            }
            return `${Math.min(this.progress.done + 1, this.progress.total)} of ${this.progress.total}`;
        },
        results() {
            return (this.state && this.state.results) || [];
        },
        rewrittenIds() {
            return this.results.filter(result => result.status === 'rewritten').map(result => result.id);
        },
    },
    watch: {
        modelValue: {
            immediate: true,
            handler(open) {
                if (open) {
                    this.error = null;
                    this.requestState();
                }
            },
        },
        'options.private_info'(value) {
            if (!value) {
                this.options.self_info = false;
            }
        },
    },
    methods: {
        send(action, data = {}) {
            this.getWebsocket().send(JSON.stringify({
                type: 'world_state_manager',
                action,
                ...data,
            }));
        },
        requestState() {
            this.send('advance_scene_state');
        },
        close() {
            this.$emit('update:modelValue', false);
        },
        begin() {
            if (!this.canBegin) {
                return;
            }
            const payload = {};
            for (const option of this.rewriteOptions) {
                payload[option.key] = !!(option.available && this.options[option.key]);
            }
            payload.private_info = this.options.private_info;
            payload.self_info = this.options.private_info && this.options.self_info;
            this.progress = { done: 0, total: 0, current: null };
            this.error = null;
            this.send('advance_scene', payload);
        },
        stop() {
            this.getWebsocket().send(JSON.stringify({ type: 'interrupt' }));
        },
        revert(ids) {
            if (ids.length) {
                this.send('advance_scene_revert', { ids });
            }
        },
        dismiss() {
            this.send('advance_scene_dismiss');
            this.options = defaultOptions();
            this.close();
        },
        statusLabel(status) {
            return STATUS_LABELS[status] || status;
        },
        statusColor(status) {
            return STATUS_COLORS[status] || 'muted';
        },
        handleMessage(message) {
            if (message.type !== 'world_state_manager') {
                return;
            }
            if (message.action === 'advance_scene_state') {
                this.state = message.data;
            } else if (message.action === 'advance_scene_progress') {
                this.progress = message.data;
            } else if (message.action === 'advance_scene_failed') {
                this.error = message.data && message.data.message;
            }
        },
    },
    mounted() {
        this.registerMessageHandler(this.handleMessage);
    },
    unmounted() {
        this.unregisterMessageHandler(this.handleMessage);
    },
};
</script>

<style scoped>
.advance-option {
    margin-bottom: 2px;
}
.option-hint {
    margin-left: 40px;
    margin-top: -6px;
    margin-bottom: 4px;
}
.self-option + .option-hint {
    margin-top: 0;
}
.result-title {
    font-size: 0.9rem;
}
.result-value {
    white-space: pre-wrap;
    font-size: 0.85rem;
    padding: 6px 8px;
    border-left: 2px solid rgba(var(--v-theme-primary), 0.5);
}
</style>
