<template>
    <!-- the Editor agent's custom steps (talemate.agents.editor.custom_steps) -->
    <div class="editor-custom-steps mt-2">
        <div class="d-flex align-center flex-wrap mb-2">
            <v-btn color="primary" variant="tonal" size="small" prepend-icon="mdi-plus" class="mr-2 mb-1" @click="openCreate">New Step</v-btn>
            <v-btn variant="tonal" size="small" prepend-icon="mdi-import" class="mb-1" @click="$refs.importFile.click()">Import</v-btn>
            <input ref="importFile" type="file" accept=".json,application/json" class="d-none" @change="importFile">
            <v-spacer></v-spacer>
            <span class="text-caption text-muted mb-1">Top runs first. Drag to reorder.</span>
        </div>

        <v-alert v-if="error" type="error" variant="tonal" density="compact" closable class="mb-2" @click:close="error = null">{{ error }}</v-alert>

        <div v-if="!steps.length" class="text-caption text-muted py-4 text-center no-steps">
            No custom steps yet.
        </div>

        <v-list v-else density="compact" class="step-list" bg-color="transparent">
            <v-list-item
                v-for="(step, index) in steps"
                :key="step.id"
                :class="['step-row', { 'drag-over': dragOver === index, 'dragging': dragIndex === index }]"
                draggable="true"
                @dragstart="dragStart(index, $event)"
                @dragover.prevent="dragOver = index"
                @dragleave="dragOver = (dragOver === index ? null : dragOver)"
                @drop.prevent="drop(index)"
                @dragend="dragIndex = null; dragOver = null"
            >
                <template v-slot:prepend>
                    <v-icon class="drag-handle mr-2" size="small">mdi-drag</v-icon>
                </template>
                <v-list-item-title :class="{ 'text-muted': !step.enabled }">
                    {{ step.name }}
                    <v-chip v-if="step.narrator" size="x-small" label class="ml-1" color="secondary" variant="tonal">Narrator</v-chip>
                    <v-chip v-if="step.user_messages" size="x-small" label class="ml-1" color="secondary" variant="tonal">Your lines</v-chip>
                    <v-chip size="x-small" label class="ml-1" variant="tonal">{{ displayLabel(step.display) }}</v-chip>
                    <v-chip v-if="step.client" size="x-small" label class="ml-1" variant="tonal" prepend-icon="mdi-network-outline">{{ step.client }}</v-chip>
                    <v-chip v-if="!step.template_found" size="x-small" label class="ml-1" color="error" variant="tonal">Template missing</v-chip>
                </v-list-item-title>
                <v-list-item-subtitle>{{ step.description || step.template_uid }}</v-list-item-subtitle>
                <template v-slot:append>
                    <div class="d-flex align-center">
                        <v-btn v-if="!step.template_found" size="small" variant="text" color="primary" prepend-icon="mdi-restore" @click="send('custom_step_restore_template', { step_id: step.id })">Restore</v-btn>
                        <v-switch
                            :model-value="step.enabled"
                            color="primary"
                            density="compact"
                            hide-details
                            class="step-toggle mr-1"
                            @update:model-value="send('custom_step_toggle', { step_id: step.id, enabled: !!$event })"
                        ></v-switch>
                        <v-btn icon="mdi-pencil" size="small" variant="text" title="Edit" @click="openEdit(step)"></v-btn>
                        <v-btn icon="mdi-export" size="small" variant="text" title="Export" @click="send('custom_step_export', { step_id: step.id })"></v-btn>
                        <v-btn v-if="confirmDelete !== step.id" icon="mdi-delete" size="small" variant="text" color="delete" title="Delete" @click="confirmDelete = step.id"></v-btn>
                        <v-btn v-else size="small" variant="tonal" color="delete" prepend-icon="mdi-delete" @click="remove(step)">Delete</v-btn>
                    </div>
                </template>
            </v-list-item>
        </v-list>

        <!-- create / edit -->
        <v-dialog v-model="dialog" max-width="680" scrollable>
            <v-card class="step-dialog">
                <v-card-title>
                    <v-icon class="mr-2" size="small" color="primary">mdi-format-list-numbered</v-icon>
                    {{ editing ? 'Edit Step' : 'New Custom Step' }}
                </v-card-title>
                <v-card-text>
                    <v-text-field v-model="form.name" label="Name" density="compact" :rules="[v => !!(v && v.trim()) || 'A name is needed']" autofocus></v-text-field>
                    <div v-if="!editing && form.name && form.name.trim()" class="text-caption text-muted mt-n2 mb-2">Template: editor › custom-steps/{{ previewId }}</div>
                    <div v-else-if="editing" class="text-caption text-muted mt-n2 mb-2">Template: {{ editing.template_uid }} (edit its prompt on the Templates page)</div>
                    <v-textarea v-model="form.description" label="Description" rows="2" auto-grow density="compact"></v-textarea>

                    <div class="form-section">Runs on</div>
                    <div class="text-caption text-muted mb-1">Characters' and groups' lines always.</div>
                    <v-checkbox v-model="form.user_messages" label="Your character's lines too" density="compact" hide-details color="primary"></v-checkbox>
                    <v-checkbox v-model="form.narrator" label="The narrator too" density="compact" hide-details color="primary"></v-checkbox>

                    <div class="form-section">History</div>
                    <v-number-input
                        v-model="form.history_entries"
                        :min="-1"
                        :step="1"
                        label="History entries before the passage"
                        density="compact"
                        messages="-1: everything that fits, 0: none, n: the last n entries"
                        control-variant="stacked"
                    ></v-number-input>
                    <v-checkbox v-model="form.number_history" label="Number the entries (1 oldest to n newest)" density="compact" hide-details color="primary"></v-checkbox>
                    <v-checkbox v-model="form.entire_history" label="Use the entire history" messages="Everything that happened, not only what the character was there for (character dependent history)" density="compact" color="primary"></v-checkbox>

                    <div class="form-section">Characters</div>
                    <v-checkbox v-model="form.character_info" label="Show character info" density="compact" hide-details color="primary"></v-checkbox>
                    <div class="sub-options">
                        <v-checkbox v-model="form.all_character_info" :disabled="!form.character_info" label="Show all characters' info" messages="Every active character's, not only the speaker's (Hide Info still applies)" density="compact" color="primary"></v-checkbox>
                        <v-checkbox v-model="form.ignore_private" :disabled="!form.character_info" label="Ignore private restrictions" messages="Private values, whatever the speaker may see" density="compact" color="primary"></v-checkbox>
                        <v-checkbox v-model="form.ignore_self" :disabled="!form.character_info" label="Ignore self restrictions" messages="Self values where characters have them (they take the place of private / public ones)" density="compact" color="primary"></v-checkbox>
                    </div>
                    <v-checkbox v-model="form.locations" label="Show character locations" density="compact" hide-details color="primary"></v-checkbox>
                    <div class="sub-options">
                        <v-checkbox v-model="form.true_locations" :disabled="!form.locations" label="Show true locations" messages="Where everyone actually is, not where the speaker thinks (always, for the narrator)" density="compact" color="primary"></v-checkbox>
                    </div>

                    <div class="form-section">Answer</div>
                    <v-checkbox v-model="form.length_limit" label="Length limit" messages="About the passage's length (capped by Settings › Advance Scene: max paragraphs)" density="compact" color="primary"></v-checkbox>
                    <v-checkbox v-model="form.coercion" label="Use the client's editor coercion" density="compact" hide-details color="primary"></v-checkbox>
                    <v-select
                        v-model="form.client"
                        :items="clientItems"
                        label="LLM client"
                        density="compact"
                        class="mt-3"
                        messages="If it isn't available, the editor's client is used."
                    ></v-select>

                    <div class="form-section">While it runs</div>
                    <v-radio-group v-model="form.display" density="compact" hide-details>
                        <v-radio value="previous" label="Show the message from before the step, replace it when done" color="primary"></v-radio>
                        <v-radio value="stream" label="Stream the step's answer into the message" color="primary"></v-radio>
                        <v-radio value="hide" label="Hide the message until all steps are done" color="primary"></v-radio>
                    </v-radio-group>
                </v-card-text>
                <v-card-actions>
                    <v-btn variant="text" color="cancel" prepend-icon="mdi-cancel" @click="dialog = false">Cancel</v-btn>
                    <v-spacer></v-spacer>
                    <v-btn variant="text" color="primary" :prepend-icon="editing ? 'mdi-content-save' : 'mdi-plus'" :disabled="!canSave" @click="save">{{ editing ? 'Save' : 'Create' }}</v-btn>
                </v-card-actions>
            </v-card>
        </v-dialog>
    </div>
</template>

<script>
const DISPLAY_LABELS = {
    previous: 'Shows previous',
    stream: 'Streams',
    hide: 'Hides',
};

function defaultForm() {
    return {
        name: '',
        description: '',
        enabled: true,
        history_entries: 10,
        number_history: false,
        entire_history: false,
        user_messages: false,
        narrator: false,
        character_info: true,
        all_character_info: false,
        ignore_private: false,
        ignore_self: false,
        locations: true,
        true_locations: false,
        length_limit: false,
        coercion: true,
        client: '',
        display: 'previous',
    };
}

export default {
    name: 'EditorCustomSteps',
    inject: ['getWebsocket', 'registerMessageHandler', 'unregisterMessageHandler'],
    data() {
        return {
            steps: [],
            clients: [],
            error: null,
            dialog: false,
            editing: null,
            form: defaultForm(),
            confirmDelete: null,
            dragIndex: null,
            dragOver: null,
        };
    },
    computed: {
        canSave() {
            return !!(this.form.name && this.form.name.trim());
        },
        previewId() {
            const base = (this.form.name || '').toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '').slice(0, 48) || 'step';
            let id = base;
            let number = 2;
            const taken = new Set(this.steps.map(step => step.id));
            while (taken.has(id)) {
                id = `${base}-${number}`;
                number += 1;
            }
            return id;
        },
        clientItems() {
            const items = [{ title: "Editor's client (default)", value: '' }];
            for (const name of this.clients) {
                items.push({ title: name, value: name });
            }
            if (this.form.client && !this.clients.includes(this.form.client)) {
                items.push({ title: `${this.form.client} (unavailable, using default)`, value: this.form.client });
            }
            return items;
        },
    },
    methods: {
        send(action, data = {}) {
            this.getWebsocket().send(JSON.stringify({ type: 'editor', action, ...data }));
        },
        displayLabel(display) {
            return DISPLAY_LABELS[display] || display;
        },
        openCreate() {
            this.editing = null;
            this.form = defaultForm();
            this.dialog = true;
        },
        openEdit(step) {
            this.editing = step;
            const form = defaultForm();
            for (const key of Object.keys(form)) {
                if (step[key] !== undefined) {
                    form[key] = step[key];
                }
            }
            this.form = form;
            this.dialog = true;
        },
        save() {
            if (!this.canSave) {
                return;
            }
            const step = { ...this.form, name: this.form.name.trim() };
            step.history_entries = Math.max(-1, parseInt(step.history_entries, 10) || 0);
            this.send('custom_step_save', { step_id: this.editing ? this.editing.id : null, step });
            this.dialog = false;
        },
        remove(step) {
            this.confirmDelete = null;
            this.send('custom_step_delete', { step_id: step.id });
        },
        dragStart(index, event) {
            this.dragIndex = index;
            if (event.dataTransfer) {
                event.dataTransfer.effectAllowed = 'move';
                event.dataTransfer.setData('text/plain', String(index));
            }
        },
        drop(index) {
            const from = this.dragIndex;
            this.dragIndex = null;
            this.dragOver = null;
            if (from === null || from === index) {
                return;
            }
            const steps = [...this.steps];
            const [moved] = steps.splice(from, 1);
            steps.splice(index, 0, moved);
            this.steps = steps;
            this.send('custom_steps_order', { step_ids: steps.map(step => step.id) });
        },
        importFile(event) {
            const file = event.target.files && event.target.files[0];
            event.target.value = '';
            if (!file) {
                return;
            }
            const reader = new FileReader();
            reader.onload = () => {
                try {
                    this.send('custom_step_import', { data: JSON.parse(reader.result) });
                } catch (e) {
                    this.error = `Couldn't read ${file.name}: it isn't a JSON file.`;
                }
            };
            reader.readAsText(file);
        },
        download(stepId, data) {
            const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' });
            const url = URL.createObjectURL(blob);
            const link = document.createElement('a');
            link.href = url;
            link.download = `editor-step-${stepId}.json`;
            document.body.appendChild(link);
            link.click();
            link.remove();
            setTimeout(() => URL.revokeObjectURL(url), 1000);
        },
        handleMessage(message) {
            if (message.type !== 'editor') {
                return;
            }
            if (message.action === 'custom_steps') {
                this.steps = message.data.steps || [];
                this.clients = message.data.clients || [];
                this.error = null;
            } else if (message.action === 'custom_steps_failed') {
                this.error = message.data && message.data.message;
            } else if (message.action === 'custom_step_exported') {
                this.download(message.data.step_id, message.data.export);
            }
        },
    },
    mounted() {
        this.registerMessageHandler(this.handleMessage);
        this.send('custom_steps');
    },
    unmounted() {
        this.unregisterMessageHandler(this.handleMessage);
    },
};
</script>

<style scoped>
.step-row {
    border: 1px solid rgba(var(--v-border-color), var(--v-border-opacity));
    border-radius: 4px;
    margin-bottom: 4px;
    cursor: grab;
}
.step-row.dragging {
    opacity: 0.5;
}
.step-row.drag-over {
    border-color: rgb(var(--v-theme-primary));
}
.step-toggle {
    flex: none;
}
.form-section {
    font-size: 0.8rem;
    text-transform: uppercase;
    letter-spacing: 0.05em;
    margin-top: 14px;
    margin-bottom: 2px;
    color: rgb(var(--v-theme-primary));
}
.sub-options {
    margin-left: 32px;
}
</style>
