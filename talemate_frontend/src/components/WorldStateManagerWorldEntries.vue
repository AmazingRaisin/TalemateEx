<template>
    <v-row>
        <v-col cols="12" xl="8" xxl="5">
            <v-card v-if="entry != null">
                <v-form v-model="formValid" ref="form" @submit.prevent>
                    <v-text-field 
                        :disabled="!isNewEntry" 
                        v-model="entry.id" label="Entry ID" 
                        :rules="rules"
                        hint="The ID of the entry. This should be a unique identifier for the entry."
                        @keyup.enter.prevent="focusText"
                    >
                    </v-text-field>
                    <ContextualGenerate 
                        :context="'world context:'+entry.id" 
                        :original="entry.text"
                        :requires-instructions="true"
                        :generation-options="generationOptions"
                        :specify-length="true"
                        :length="512"
                        @generate="content => { entry.text=content; queueSave(500); dirty = true; }"
                    />
                    <v-textarea 
                        ref="textInput"
                        v-model="entry.text"
                        label="World information"
                        hint="Describe the world information here. This could be a description of a location, a historical event, or anything else that is relevant to the world." 
                        :color="dirty ? 'dirty' : ''"
                        @update:model-value="dirty = true"
                        @blur="handleBlur"
                        auto-grow
                        rows="5">
                    </v-textarea>

                    <v-row>
                        <v-col cols="12" md="6">
                            <v-select
                                v-model="entry.meta['retrieval_mode']"
                                :items="retrievalModeOptions"
                                label="Retrieval mode"
                                density="compact"
                                @update:model-value="queueSave(500)"
                            />
                        </v-col>
                        <v-col cols="12" md="6" v-if="entry.meta['lorebook_name']">
                            <v-text-field
                                :model-value="entry.meta['lorebook_name']"
                                label="Lorebook"
                                density="compact"
                                readonly
                            />
                        </v-col>
                    </v-row>

                    <div v-if="usesKeywordRetrieval">
                        <v-combobox
                            v-model="entry.meta['keyword_keys']"
                            label="Keywords"
                            density="compact"
                            multiple
                            chips
                            closable-chips
                            clearable
                            @update:model-value="queueSave(500)"
                        />
                        <v-combobox
                            v-model="entry.meta['keyword_secondary_keys']"
                            label="Secondary keywords"
                            density="compact"
                            multiple
                            chips
                            closable-chips
                            clearable
                            @update:model-value="queueSave(500)"
                        />

                        <v-row>
                            <v-col cols="12" md="4">
                                <v-checkbox
                                    v-model="entry.meta['keyword_constant']"
                                    label="Always active"
                                    @change="save()"
                                />
                            </v-col>
                            <v-col cols="12" md="4">
                                <v-checkbox
                                    v-model="entry.meta['keyword_use_probability']"
                                    label="Use probability"
                                    @change="save()"
                                />
                            </v-col>
                            <v-col cols="12" md="4">
                                <v-checkbox
                                    v-model="entry.meta['keyword_exclude_recursion']"
                                    label="Exclude recursion"
                                    @change="save()"
                                />
                            </v-col>
                        </v-row>

                        <v-row>
                            <v-col cols="12" md="3">
                                <v-text-field
                                    v-model.number="entry.meta['keyword_priority']"
                                    label="Priority"
                                    type="number"
                                    density="compact"
                                    @blur="save()"
                                />
                            </v-col>
                            <v-col cols="12" md="3">
                                <v-text-field
                                    v-model.number="entry.meta['keyword_probability']"
                                    label="Probability"
                                    type="number"
                                    min="0"
                                    max="100"
                                    density="compact"
                                    :disabled="!entry.meta['keyword_use_probability']"
                                    @blur="save()"
                                />
                            </v-col>
                            <v-col cols="12" md="3">
                                <v-text-field
                                    v-model="entry.meta['keyword_scan_depth']"
                                    label="Scan depth"
                                    type="number"
                                    min="0"
                                    density="compact"
                                    clearable
                                    @blur="save()"
                                />
                            </v-col>
                            <v-col cols="12" md="3">
                                <v-select
                                    v-model="entry.meta['keyword_selective_logic']"
                                    :items="selectiveLogicOptions"
                                    label="Secondary logic"
                                    density="compact"
                                    @update:model-value="queueSave(500)"
                                />
                            </v-col>
                        </v-row>

                        <v-row>
                            <v-col cols="12" md="6">
                                <v-select
                                    v-model="entry.meta['keyword_case_sensitive']"
                                    :items="inheritBooleanOptions"
                                    label="Case sensitivity"
                                    density="compact"
                                    @update:model-value="queueSave(500)"
                                />
                            </v-col>
                            <v-col cols="12" md="6">
                                <v-select
                                    v-model="entry.meta['keyword_match_whole_words']"
                                    :items="inheritBooleanOptions"
                                    label="Whole word matching"
                                    density="compact"
                                    @update:model-value="queueSave(500)"
                                />
                            </v-col>
                        </v-row>
                    </div>

                    <v-row>
                        <v-col cols="12" md="6">
                            <v-checkbox v-model="entry.meta['pin_only']" 
                            label="Include only when pinned" 
                            :hint="(
                                entry.meta['pin_only'] ? 
                                'This entry will only be included when pinned and never be included via automatic relevancy matching.' :
                                'This entry may be included via automatic relevancy matching.'
                            )"
                            @change="save()"></v-checkbox>
                        </v-col>
                        <v-col cols="12" md="6" v-if="!isNewEntry">
                            <v-card class="mt-2" elevation="2" :color="entry.shared ? 'highlight6' : 'muted'" variant="tonal">
                                <v-card-text>
                                    <v-checkbox v-model="entry.shared" label="Shared to World Context" @change="save()"  messages="Share this entry with other scenes linked to the same shared context."></v-checkbox>
                                </v-card-text>
                            </v-card>
                        </v-col>
                    </v-row>

                </v-form>

                <v-card-actions v-if="isNewEntry">
                    <v-spacer></v-spacer>
                    <v-btn @click="save()" color="primary" prepend-icon="mdi-text-box-plus">Create</v-btn>
                </v-card-actions>
                <v-card-actions v-else>
                    <ConfirmActionInline @confirm="remove" action-label="Remove Entry" confirm-label="Confirm removal" />
                    <v-spacer></v-spacer>
                    <v-btn v-if="entryHasPin" @click="$emit('load-pin', entry.id)" color="primary" prepend-icon="mdi-pin">View pin</v-btn>
                    <v-btn v-else @click="$emit('add-pin', entry.id)" color="primary" prepend-icon="mdi-pin">Add pin</v-btn>
                </v-card-actions>
            </v-card>
            <v-card v-else>
                <v-alert color="muted" density="compact" variant="text">
                    Add world information / lore and add extra details.
                    <br><br>
                    Add a new entry or select an existing one to get started.
                    <br><br>
                    <v-icon color="orange" class="mr-1">mdi-alert</v-icon> If you want to add details to an acting character do that through the character manager instead.
                </v-alert>
            </v-card>

        </v-col>
        <v-col cols="12" xl="4" xxl="7"></v-col>
    </v-row>

</template>

<script>

import ContextualGenerate from './ContextualGenerate.vue';
import ConfirmActionInline from './ConfirmActionInline.vue';

export default {
    name: 'WorldStateManagerWorldEntries',
    components: {
        ContextualGenerate,
        ConfirmActionInline,
    },
    props: {
        pins: Object,
        templates: Object,
        generationOptions: Object,
        immutableEntries: Object,
    },
    computed: {
        isNewEntry() {
            return this.entry && this.entry.isNew;
        },
        entryHasPin() {
            return this.entry && this.pins[this.entry.id];
        },
        usesKeywordRetrieval() {
            if (!this.entry || !this.entry.meta) {
                return false;
            }
            return ['keyword', 'both'].includes(this.entry.meta.retrieval_mode);
        },
    },
    data() {
        return {
            entries: {},
            selected: null,
            timeout: null,
            entry: null,
            dirty: false,
            formValid: false,
            retrievalModeOptions: [
                { title: 'Semantic', value: 'semantic' },
                { title: 'Keyword', value: 'keyword' },
                { title: 'Both', value: 'both' },
            ],
            inheritBooleanOptions: [
                { title: 'Inherit lorebook setting', value: null },
                { title: 'Enabled', value: true },
                { title: 'Disabled', value: false },
            ],
            selectiveLogicOptions: [
                { title: 'Any secondary keyword', value: 0 },
                { title: 'Not all secondary keywords', value: 1 },
                { title: 'No secondary keywords', value: 2 },
                { title: 'All secondary keywords', value: 3 },
            ],
            rules: [
                v => !!v || 'Entry ID is required',
                // make sure id doesn't already exist
                v => {
                    if(this.entries[v] && this.isNewEntry) {
                        return 'Entry ID already exists';
                    }
                    return true;
                }
            ]
        }
    },
    emits:[
        'require-scene-save',
        'load-pin',
        'add-pin',
        'request-sync',
    ],
    inject: [
        'insertionModes',
        'getWebsocket',
        'loadContextDBEntry',
        'registerMessageHandler',
        'unregisterMessageHandler',
    ],
    watch: {
        immutableEntries: {
            immediate: true,
            handler(entries) {
                this.entries = {...entries}
                // If an entry is currently selected, re-bind it to the updated entries map
                this.$nextTick(() => {
                    const currentId = (this.entry && this.entry.id) || this.selected;
                    if (currentId && this.entries[currentId]) {
                        this.entry = this.entries[currentId];
                        this.ensureEntryMeta(this.entry);
                    }
                });
            }
        },
    },
    methods: {
        ensureEntryMeta(entry) {
            if (!entry.meta) {
                entry.meta = {};
            }
            if (!entry.meta.retrieval_mode) {
                entry.meta.retrieval_mode = 'semantic';
            }
            if (!Array.isArray(entry.meta.keyword_keys)) {
                entry.meta.keyword_keys = [];
            }
            if (!Array.isArray(entry.meta.keyword_secondary_keys)) {
                entry.meta.keyword_secondary_keys = [];
            }
            if (entry.meta.keyword_constant === undefined) {
                entry.meta.keyword_constant = false;
            }
            if (entry.meta.keyword_use_probability === undefined) {
                entry.meta.keyword_use_probability = false;
            }
            if (entry.meta.keyword_probability === undefined) {
                entry.meta.keyword_probability = 100;
            }
            if (entry.meta.keyword_priority === undefined) {
                entry.meta.keyword_priority = 0;
            }
            if (entry.meta.keyword_selective === undefined) {
                entry.meta.keyword_selective = true;
            }
            if (entry.meta.keyword_selective_logic === undefined) {
                entry.meta.keyword_selective_logic = 0;
            }
            if (entry.meta.keyword_case_sensitive === undefined) {
                entry.meta.keyword_case_sensitive = null;
            }
            if (entry.meta.keyword_match_whole_words === undefined) {
                entry.meta.keyword_match_whole_words = null;
            }
            if (entry.meta.keyword_exclude_recursion === undefined) {
                entry.meta.keyword_exclude_recursion = false;
            }
        },
        queueSave(delay = 1500) {

            if(this.isNewEntry) {
                return;
            }

            if (this.timeout !== null) {
                clearTimeout(this.timeout);
            }

            this.dirty = true;

            this.timeout = setTimeout(() => {
                this.save();
            }, delay);
        },

        save(only_if_dirty = false) {

            if(only_if_dirty && !this.dirty) {
                return;
            }

            this.$refs.form.validate();
            if(!this.formValid) {
                return;
            }
            this.ensureEntryMeta(this.entry);
            this.getWebsocket().send(JSON.stringify({
                type: 'world_state_manager',
                action: 'save_world_entry',
                id: this.entry.id,
                text: this.entry.text,
                meta: this.entry.meta,
                shared: this.entry.shared,
            }));
            if(this.entry.isNew) {
                this.entry.isNew = false;
            }
        },

        create() {
            this.selected = null;
            this.entry = {
                id: '',
                text: '',
                meta: {},
                isNew: true,
                shared: false,
            };
            this.ensureEntryMeta(this.entry);
        },

        select(id) {
            console.log({id, entries: this.entries})
            if (!this.entries[id]) {
                console.warn(`Entry "${id}" not found`);
                return;
            }
            this.entry = this.entries[id];
            this.ensureEntryMeta(this.entry);
            this.selected = id;
            this.$nextTick(() => {
                this.dirty = false;
                this.$refs.form.validate();
            });
        },

        remove() {
            if(this.entry == null || this.entry.isNew) {
                this.entry = null;
                return;
            }

            this.getWebsocket().send(JSON.stringify({
                type: 'world_state_manager',
                action: 'delete_world_entry',
                id: this.entry.id
            }));

            this.entry = null;
        },

        // responses
        focusText() {
            // Move focus to the world information textarea when Enter is pressed in Entry ID field
            this.$nextTick(() => {
                if (this.$refs.textInput && this.$refs.textInput.focus) {
                    this.$refs.textInput.focus();
                }
            });
        },
        handleBlur() {
            // Only auto-save on blur for existing entries, not new ones
            if (!this.isNewEntry) {
                this.save(true);
            }
        },

        handleMessage(message) {
            if (message.type !== 'world_state_manager') {
                return;
            }
            
            if (message.action == 'world_entry_saved') {
                this.dirty = false;
            } else if (message.action == 'world_entry_deleted') {
                this.selected = null;
                this.busy = false;
            } else if(message.action === 'operation_done') {
                this.busy = false;
            }
        },
    },
    mounted() {
        this.registerMessageHandler(this.handleMessage);
    },
}

</script>
