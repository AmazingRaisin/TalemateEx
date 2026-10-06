<template>

    <v-list density="compact" slim>
        <v-list-subheader color="grey">
            <v-icon color="primary" class="mr-1">mdi-plus</v-icon>
            Create
        </v-list-subheader>
        <v-list-item prepend-icon="mdi-text-box-plus" @click.stop="createNewEntry">
            <v-list-item-title>New Entry</v-list-item-title>
            <v-list-item-subtitle class="text-caption">Information and details.</v-list-item-subtitle>
        </v-list-item>
        <v-list-item prepend-icon="mdi-image-auto-adjust" @click.stop="createNewState">
            <v-list-item-title>New State Reinforcement</v-list-item-title>
            <v-list-item-subtitle class="text-caption">Automatically tracked state</v-list-item-subtitle>
        </v-list-item>
        <v-list-item prepend-icon="mdi-book-plus" @click.stop="triggerLorebookImport">
            <v-list-item-title>Import Lorebook</v-list-item-title>
            <v-list-item-subtitle class="text-caption">SillyTavern world info JSON</v-list-item-subtitle>
        </v-list-item>
        <input ref="lorebookImport" type="file" accept=".json,application/json" class="d-none" @change="importLorebookFile" />
        <v-dialog v-model="lorebookLibraryDialog" max-width="760" scrollable>
            <v-card>
                <v-card-title class="d-flex align-center">
                    <v-icon color="primary" class="mr-2">mdi-book-plus</v-icon>
                    Import Lorebook
                </v-card-title>
                <v-card-text>
                    <div class="lorebook-library-toolbar">
                        <v-text-field
                            v-model="lorebookLibraryFilter"
                            label="Search lorebooks"
                            prepend-inner-icon="mdi-magnify"
                            variant="outlined"
                            density="compact"
                            hide-details
                            clearable
                        />
                        <v-btn
                            color="primary"
                            prepend-icon="mdi-file-plus"
                            :disabled="libraryBusy || busy"
                            @click.stop="triggerLorebookFileAdd"
                        >
                            Add New Lorebook
                        </v-btn>
                    </div>

                    <v-alert
                        v-if="!libraryBusy && filteredLorebookLibrary.length === 0"
                        class="mt-3"
                        color="muted"
                        variant="tonal"
                    >
                        No lorebooks found.
                    </v-alert>

                    <div class="lorebook-library-list">
                        <div
                            v-for="lorebook in filteredLorebookLibrary"
                            :key="`library-lorebook-${lorebook.id}`"
                            class="lorebook-library-item"
                        >
                            <div class="lorebook-library-row">
                                <v-btn
                                    icon
                                    variant="text"
                                    size="small"
                                    @click.stop="toggleLibraryDescription(lorebook.id)"
                                >
                                    <v-icon>
                                        {{ expandedLibraryDescriptions.includes(lorebook.id) ? 'mdi-chevron-up' : 'mdi-chevron-down' }}
                                    </v-icon>
                                    <v-tooltip activator="parent" location="bottom">Description</v-tooltip>
                                </v-btn>
                                <div class="lorebook-library-summary">
                                    <div class="lorebook-library-title">{{ lorebook.name }}</div>
                                    <div class="text-caption text-medium-emphasis">
                                        {{ lorebook.entry_count }} entries
                                        <span v-if="lorebook.file_name"> · {{ lorebook.file_name }}</span>
                                    </div>
                                </div>
                                <v-btn
                                    color="primary"
                                    size="small"
                                    :loading="pendingLibraryImport === lorebook.id"
                                    :disabled="busy || libraryBusy"
                                    @click.stop="importLorebookFromLibrary(lorebook)"
                                >
                                    Import
                                </v-btn>
                                <v-btn
                                    icon
                                    variant="text"
                                    size="small"
                                    color="error"
                                    :loading="pendingLibraryDelete === lorebook.id"
                                    :disabled="busy || libraryBusy"
                                    @click.stop="deleteLorebookFromLibrary(lorebook)"
                                >
                                    <v-icon>mdi-delete-outline</v-icon>
                                    <v-tooltip activator="parent" location="bottom">Delete</v-tooltip>
                                </v-btn>
                            </div>
                            <v-expand-transition>
                                <div
                                    v-if="expandedLibraryDescriptions.includes(lorebook.id)"
                                    class="lorebook-library-description"
                                >
                                    <v-textarea
                                        v-model="lorebook.description"
                                        label="Description"
                                        variant="outlined"
                                        density="compact"
                                        rows="3"
                                        auto-grow
                                        hide-details
                                    />
                                    <div class="d-flex justify-end mt-2">
                                        <v-btn
                                            color="primary"
                                            variant="text"
                                            size="small"
                                            prepend-icon="mdi-content-save"
                                            :loading="pendingLibraryDescriptionSave === lorebook.id"
                                            :disabled="libraryBusy || busy"
                                            @click.stop="saveLorebookLibraryDescription(lorebook)"
                                        >
                                            Save Description
                                        </v-btn>
                                    </div>
                                </div>
                            </v-expand-transition>
                        </div>
                    </div>
                </v-card-text>
                <v-card-actions>
                    <v-spacer />
                    <v-btn variant="text" @click="lorebookLibraryDialog = false">Close</v-btn>
                </v-card-actions>
            </v-card>
        </v-dialog>
        <v-list-item>
            <v-text-field class="mt-1" variant="underlined" v-model="filter" label="Filter" density="compact"></v-text-field>
        </v-list-item>
        <v-list-item>
            <div class="px-2">
                <div class="text-caption text-medium-emphasis mb-1">Show max.: {{ maxEntriesDisplay }}</div>
                <v-slider 
                    v-model="maxEntriesDisplay" 
                    :min="10" 
                    :max="300" 
                    :step="10"
                    density="compact"
                    hide-details
                    thumb-label
                    color="primary"
                ></v-slider>
            </div>
        </v-list-item>
    </v-list>

    <v-list selectable slim density="compact" v-model:opened="groupsOpen" color="secondary" v-model:selected="selected">

        <v-list-group fluid value="lorebooks" color="primary">
            <template v-slot:activator="{ props }">
                <v-list-item v-bind="props">
                    <v-list-item-title>Lorebooks ({{ displayedLorebooksCount }} of {{ totalLorebooksCount }})</v-list-item-title>
                </v-list-item>
            </template>
            <v-list-item v-for="lorebook in displayedLorebooks" :key="`lorebook-${lorebook.id}`" :value="`lorebook:${lorebook.id}`" prepend-icon="mdi-book-open-page-variant">
                <v-list-item-title>{{ lorebook.name }}</v-list-item-title>
                <v-list-item-subtitle>{{ lorebook.entry_count }} entries</v-list-item-subtitle>
            </v-list-item>
            <v-card v-if="totalLorebooksCount == 0" class="ma-2 text-muted">
                <v-card-text>No lorebooks</v-card-text>
            </v-card>
        </v-list-group>

        <v-list-group fluid value="entries" color="primary">
            <template v-slot:activator="{ props }">
                <v-list-item v-bind="props">
                    <v-list-item-title>Entries ({{ displayedEntriesCount }} of {{ totalEntriesCount }})</v-list-item-title>
                </v-list-item>
            </template>
            <v-list-item v-for="entry in displayedEntries" :key="`entry-${entry.id}`" :value="`entry:${entry.id}`" prepend-icon="mdi-text-box-outline">
                <v-list-item-title>{{ entry.id }}</v-list-item-title>
                <v-list-item-subtitle>{{ entry.text }}</v-list-item-subtitle>
            </v-list-item>
            <v-card v-if="totalEntriesCount == 0" class="ma-2 text-muted">
                <v-card-text>No entries</v-card-text>
            </v-card>
        </v-list-group>

        <v-list-group fluid value="reinforcements" color="primary">
            <template v-slot:activator="{ props }">
                <v-list-item v-bind="props">
                    <v-list-item-title>States ({{ displayedReinforcementsCount }} of {{ totalReinforcementsCount }})</v-list-item-title>
                </v-list-item>
            </template>
            <v-list-item v-for="reinforcement in displayedReinforcements" :key="`state-${reinforcement.question}`" :value="`state:${reinforcement.question}`" prepend-icon="mdi-text-box-outline">
                <v-list-item-title>{{ reinforcement.question }}</v-list-item-title>
                <v-list-item-subtitle>{{ reinforcement.text }}</v-list-item-subtitle>
            </v-list-item>
            <v-card v-if="totalReinforcementsCount == 0" class="ma-2 text-muted">
                <v-card-text>No reinforcements</v-card-text>
            </v-card>
        </v-list-group>

    </v-list>

</template>
<script>

export default {
    name: 'WorldStateManagerMenuWorldTools',
    props: {
        scene: Object,
        manager: Object,
        worldStateTemplates: Object,
    },
    inject: [
        'getWebsocket',
        'autocompleteInfoMessage',
        'autocompleteRequest',
        'registerMessageHandler',
        'unregisterMessageHandler',
    ],
    data() {
        return {
            selected: null,
            groupsOpen: ['lorebooks', 'entries', 'reinforcements'],
            entries: {},
            reinforcements: {},
            lorebooks: {},
            lorebookLibrary: [],
            lorebookLibraryDialog: false,
            lorebookLibraryFilter: '',
            expandedLibraryDescriptions: [],
            filter: '',
            maxEntriesDisplay: 50,
            busy: false,
            libraryBusy: false,
            pendingLorebookSelection: null,
            pendingLibraryImport: null,
            pendingLibraryDescriptionSave: null,
            pendingLibraryDelete: null,
        }
    },
    watch: {
        selected: {
            immediate: true,
            handler(selected) {
                console.log('selected', selected);
                if (selected) {
                    this.load(selected[0]);
                }
            }
        },
        lorebookLibraryDialog(open) {
            if (open) {
                this.requestLorebookLibrary();
            }
        }
    },
    computed: {
        filteredLorebookLibrary() {
            const items = [...this.lorebookLibrary].sort((a, b) =>
                String(a.name || '').localeCompare(String(b.name || ''))
            );
            if (!this.lorebookLibraryFilter) {
                return items;
            }
            const filterLower = this.lorebookLibraryFilter.toLowerCase();
            return items.filter(lorebook =>
                lorebook.id.toLowerCase().includes(filterLower) ||
                (lorebook.name && lorebook.name.toLowerCase().includes(filterLower)) ||
                (lorebook.file_name && lorebook.file_name.toLowerCase().includes(filterLower)) ||
                (lorebook.description && lorebook.description.toLowerCase().includes(filterLower))
            );
        },
        filteredEntries() {
            if (!this.filter) {
                return Object.values(this.entries);
            }
            const filterLower = this.filter.toLowerCase();
            return Object.values(this.entries).filter(entry => 
                entry.id.toLowerCase().includes(filterLower) || 
                (entry.text && entry.text.toLowerCase().includes(filterLower))
            );
        },
        displayedEntries() {
            return this.filteredEntries.slice(0, this.maxEntriesDisplay);
        },
        displayedEntriesCount() {
            return this.displayedEntries.length;
        },
        totalEntriesCount() {
            return this.filteredEntries.length;
        },
        filteredLorebooks() {
            if (!this.filter) {
                return Object.values(this.lorebooks);
            }
            const filterLower = this.filter.toLowerCase();
            return Object.values(this.lorebooks).filter(lorebook =>
                lorebook.id.toLowerCase().includes(filterLower) ||
                (lorebook.name && lorebook.name.toLowerCase().includes(filterLower)) ||
                (lorebook.description && lorebook.description.toLowerCase().includes(filterLower))
            );
        },
        displayedLorebooks() {
            return this.filteredLorebooks.slice(0, this.maxEntriesDisplay);
        },
        displayedLorebooksCount() {
            return this.displayedLorebooks.length;
        },
        totalLorebooksCount() {
            return this.filteredLorebooks.length;
        },
        filteredReinforcements() {
            if (!this.filter) {
                return Object.values(this.reinforcements);
            }
            const filterLower = this.filter.toLowerCase();
            return Object.values(this.reinforcements).filter(reinforcement => 
                reinforcement.question.toLowerCase().includes(filterLower) ||
                (reinforcement.answer && reinforcement.answer.toLowerCase().includes(filterLower)) ||
                (reinforcement.text && reinforcement.text.toLowerCase().includes(filterLower))
            );
        },
        displayedReinforcements() {
            return this.filteredReinforcements.slice(0, this.maxEntriesDisplay);
        },
        displayedReinforcementsCount() {
            return this.displayedReinforcements.length;
        },
        totalReinforcementsCount() {
            return this.filteredReinforcements.length;
        },
        entriesNumber() {
            return Object.keys(this.entries).length;
        },
        reinforcementsNumber() {
            return Object.keys(this.reinforcements).length;
        },
    },
    emits: [
        'world-state-manager-navigate'
    ],
    methods: {
        reset() {
            this.selected = null;
        },
        handleMessage(message) {
            if (message.type !== 'world_state_manager') {
                return;
            }

            if (message.action == 'world') {
                this.entries = message.data.entries;
                this.reinforcements = message.data.reinforcements;
                this.lorebooks = message.data.lorebooks || {};
                console.log('entries', this.entries);
                console.log('reinforcements', this.reinforcements);
                if (this.pendingLorebookSelection && this.lorebooks[this.pendingLorebookSelection]) {
                    this.$emit('world-state-manager-navigate', 'world', `lorebook:${this.pendingLorebookSelection}`);
                    this.pendingLorebookSelection = null;
                }
            } else if(message.action === 'lorebook_library') {
                this.lorebookLibrary = message.data || [];
                this.libraryBusy = false;
            } else if(message.action === 'lorebook_imported') {
                this.pendingLorebookSelection = message.data.lorebook_id;
                this.pendingLibraryImport = null;
                this.lorebookLibraryDialog = false;
            } else if(message.action === 'lorebook_library_deleted') {
                const deletedId = message.data?.id;
                this.pendingLibraryDelete = null;
                this.expandedLibraryDescriptions = this.expandedLibraryDescriptions.filter(id => id !== deletedId);
            } else if(message.action === 'lorebook_library_upload_failed' || message.action === 'lorebook_library_description_save_failed' || message.action === 'lorebook_library_delete_failed') {
                this.libraryBusy = false;
                this.pendingLibraryDescriptionSave = null;
                this.pendingLibraryImport = null;
                this.pendingLibraryDelete = null;
            } else if(message.action === 'operation_done') {
                this.busy = false;
                this.libraryBusy = false;
                this.pendingLibraryDescriptionSave = null;
                this.pendingLibraryImport = null;
                this.pendingLibraryDelete = null;
            }
        },
        load(id) {
            this.$emit('world-state-manager-navigate', 'world', id);
        },
        createNewEntry() {
            this.$emit('world-state-manager-navigate', 'world', '$NEW_ENTRY');
            this.selected = [];
        },
        createNewState() {
            this.$emit('world-state-manager-navigate', 'world', '$NEW_STATE');
            this.selected = [];
        },
        triggerLorebookImport() {
            if (this.busy) {
                return;
            }
            this.lorebookLibraryDialog = true;
        },
        requestLorebookLibrary() {
            if (!this.getWebsocket()) {
                this.libraryBusy = false;
                return;
            }
            this.libraryBusy = true;
            this.getWebsocket().send(JSON.stringify({
                type: 'world_state_manager',
                action: 'get_lorebook_library',
            }));
        },
        triggerLorebookFileAdd() {
            if (this.libraryBusy || this.busy) {
                return;
            }
            this.$refs.lorebookImport.click();
        },
        toggleLibraryDescription(id) {
            if (this.expandedLibraryDescriptions.includes(id)) {
                this.expandedLibraryDescriptions = this.expandedLibraryDescriptions.filter(item => item !== id);
                return;
            }
            this.expandedLibraryDescriptions = [...this.expandedLibraryDescriptions, id];
        },
        importLorebookFromLibrary(lorebook) {
            if (this.busy || this.libraryBusy) {
                return;
            }
            this.busy = true;
            this.libraryBusy = true;
            this.pendingLibraryImport = lorebook.id;
            this.getWebsocket().send(JSON.stringify({
                type: 'world_state_manager',
                action: 'import_lorebook_from_library',
                id: lorebook.id,
            }));
        },
        saveLorebookLibraryDescription(lorebook) {
            if (this.libraryBusy || this.busy) {
                return;
            }
            this.libraryBusy = true;
            this.pendingLibraryDescriptionSave = lorebook.id;
            this.getWebsocket().send(JSON.stringify({
                type: 'world_state_manager',
                action: 'save_lorebook_library_description',
                id: lorebook.id,
                description: lorebook.description || '',
            }));
        },
        deleteLorebookFromLibrary(lorebook) {
            if (this.libraryBusy || this.busy) {
                return;
            }
            if (!window.confirm(`Delete "${lorebook.name}" from imported lorebooks?`)) {
                return;
            }
            this.libraryBusy = true;
            this.pendingLibraryDelete = lorebook.id;
            this.getWebsocket().send(JSON.stringify({
                type: 'world_state_manager',
                action: 'delete_lorebook_from_library',
                id: lorebook.id,
            }));
        },
        importLorebookFile(event) {
            const file = event.target.files[0];
            event.target.value = null;
            if (!file) {
                return;
            }

            const reader = new FileReader();
            reader.onload = () => {
                this.libraryBusy = true;
                this.getWebsocket().send(JSON.stringify({
                    type: 'world_state_manager',
                    action: 'upload_lorebook_to_library',
                    file_name: file.name,
                    content: reader.result,
                }));
            };
            reader.readAsText(file);
        }
    },
    mounted() {
        this.registerMessageHandler(this.handleMessage);
    },
    unmounted() {
        this.unregisterMessageHandler(this.handleMessage);
    }
}

</script>

<style scoped>
.lorebook-library-toolbar {
    display: grid;
    grid-template-columns: minmax(0, 1fr) auto;
    gap: 10px;
    align-items: center;
}

.lorebook-library-list {
    display: flex;
    flex-direction: column;
    gap: 8px;
    margin-top: 12px;
}

.lorebook-library-item {
    border: 1px solid rgba(var(--v-theme-on-surface), 0.12);
    border-radius: 6px;
    background: rgba(var(--v-theme-surface), 0.28);
}

.lorebook-library-row {
    display: grid;
    grid-template-columns: auto minmax(0, 1fr) auto auto;
    gap: 8px;
    align-items: center;
    padding: 8px;
}

.lorebook-library-summary {
    min-width: 0;
}

.lorebook-library-title {
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    font-weight: 600;
}

.lorebook-library-description {
    padding: 0 12px 12px 48px;
}

@media (max-width: 640px) {
    .lorebook-library-toolbar {
        grid-template-columns: 1fr;
    }

    .lorebook-library-row {
        grid-template-columns: auto minmax(0, 1fr);
    }

    .lorebook-library-row > .v-btn:last-child {
        grid-column: 2;
        justify-self: start;
    }

    .lorebook-library-description {
        padding-left: 12px;
    }
}
</style>
