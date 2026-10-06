<template>
    <v-row>
        <v-col cols="12" xl="8" xxl="5">
            <v-card v-if="lorebook">
                <v-card-title class="d-flex align-center">
                    <v-icon color="primary" class="mr-2">mdi-book-open-page-variant</v-icon>
                    {{ lorebook.name }}
                </v-card-title>
                <v-card-subtitle>
                    {{ entryCount }} entries
                </v-card-subtitle>
                <v-card-text>
                    <v-form ref="form" @submit.prevent>
                        <v-text-field
                            v-model="lorebook.name"
                            label="Name"
                            density="compact"
                            @blur="save"
                        />
                        <v-textarea
                            v-model="lorebook.description"
                            label="Description"
                            density="compact"
                            rows="2"
                            auto-grow
                            @blur="save"
                        />

                        <v-row>
                            <v-col cols="12" md="6">
                                <v-checkbox
                                    v-model="lorebook.enabled"
                                    label="Enabled"
                                    @change="save"
                                />
                            </v-col>
                            <v-col cols="12" md="6">
                                <v-checkbox
                                    v-model="lorebook.recursive_scanning"
                                    label="Recursive scanning"
                                    @change="save"
                                />
                            </v-col>
                        </v-row>

                        <v-row>
                            <v-col cols="12" md="4">
                                <v-text-field
                                    v-model.number="lorebook.token_budget"
                                    label="Token budget"
                                    type="number"
                                    min="0"
                                    density="compact"
                                    @blur="save"
                                />
                            </v-col>
                            <v-col cols="12" md="4">
                                <v-text-field
                                    v-model.number="lorebook.scan_depth"
                                    label="Scan depth"
                                    type="number"
                                    min="0"
                                    max="100"
                                    density="compact"
                                    @blur="save"
                                />
                            </v-col>
                            <v-col cols="12" md="4">
                                <v-text-field
                                    v-model.number="lorebook.max_recursion_steps"
                                    label="Recursion steps"
                                    type="number"
                                    min="1"
                                    max="10"
                                    density="compact"
                                    :disabled="!lorebook.recursive_scanning"
                                    @blur="save"
                                />
                            </v-col>
                        </v-row>

                        <v-row>
                            <v-col cols="12" md="4">
                                <v-checkbox
                                    v-model="lorebook.case_sensitive"
                                    label="Case sensitive"
                                    @change="save"
                                />
                            </v-col>
                            <v-col cols="12" md="4">
                                <v-checkbox
                                    v-model="lorebook.match_whole_words"
                                    label="Match whole words"
                                    @change="save"
                                />
                            </v-col>
                            <v-col cols="12" md="4">
                                <v-checkbox
                                    v-model="lorebook.include_names"
                                    label="Include names"
                                    @change="save"
                                />
                            </v-col>
                        </v-row>
                    </v-form>
                </v-card-text>
                <v-card-actions>
                    <ConfirmActionInline @confirm="remove" action-label="Remove Lorebook" confirm-label="Confirm removal" />
                    <v-spacer></v-spacer>
                    <v-btn @click="save" color="primary" prepend-icon="mdi-content-save">Save</v-btn>
                </v-card-actions>
            </v-card>
            <v-card v-else>
                <v-alert color="muted" density="compact" variant="text">
                    Select an imported lorebook to edit its keyword matching settings.
                </v-alert>
            </v-card>
        </v-col>
        <v-col cols="12" xl="4" xxl="7"></v-col>
    </v-row>
</template>

<script>

import ConfirmActionInline from './ConfirmActionInline.vue';

export default {
    name: 'WorldStateManagerWorldLorebook',
    components: {
        ConfirmActionInline,
    },
    props: {
        immutableLorebooks: Object,
        entries: Object,
    },
    inject: [
        'getWebsocket',
        'registerMessageHandler',
        'unregisterMessageHandler',
    ],
    data() {
        return {
            lorebooks: {},
            lorebook: null,
            selected: null,
            busy: false,
        }
    },
    computed: {
        entryCount() {
            if (!this.lorebook) {
                return 0;
            }
            return Object.values(this.entries || {}).filter(entry => entry.meta?.lorebook_id === this.lorebook.id).length;
        },
    },
    watch: {
        immutableLorebooks: {
            immediate: true,
            handler(lorebooks) {
                this.lorebooks = {...(lorebooks || {})};
                this.$nextTick(() => {
                    if (this.selected && this.lorebooks[this.selected]) {
                        this.lorebook = this.lorebooks[this.selected];
                    }
                });
            },
        },
    },
    methods: {
        select(id) {
            if (!this.lorebooks[id]) {
                console.warn(`Lorebook "${id}" not found`);
                return;
            }
            this.selected = id;
            this.lorebook = this.lorebooks[id];
        },
        save() {
            if (!this.lorebook || this.busy) {
                return;
            }
            this.busy = true;
            this.getWebsocket().send(JSON.stringify({
                type: 'world_state_manager',
                action: 'save_lorebook_settings',
                settings: this.lorebook,
            }));
        },
        remove() {
            if (!this.lorebook || this.busy) {
                return;
            }
            this.busy = true;
            this.getWebsocket().send(JSON.stringify({
                type: 'world_state_manager',
                action: 'delete_lorebook',
                id: this.lorebook.id,
            }));
        },
        handleMessage(message) {
            if (message.type !== 'world_state_manager') {
                return;
            }
            if (message.action === 'operation_done') {
                this.busy = false;
            } else if (message.action === 'lorebook_deleted') {
                this.selected = null;
                this.lorebook = null;
                this.busy = false;
            }
        },
    },
    mounted() {
        this.registerMessageHandler(this.handleMessage);
    },
    unmounted() {
        this.unregisterMessageHandler(this.handleMessage);
    },
}

</script>
