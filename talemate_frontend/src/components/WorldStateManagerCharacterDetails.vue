<template>
    <v-row class="mb-2">
        <v-col cols="12" lg="6">
            <v-number-input
                v-model="character.character_dependent_history_override"
                label="Character-Dependent History Override"
                :min="-1"
                :step="1"
                control-variant="hidden"
                messages="-1 disables filtering, 0 uses the scene setting, and positive values always include that many recent entries."
                :color="historyOverrideDirty ? 'dirty' : ''"
                @update:model-value="historyOverrideDirty = true"
                @blur="updateHistoryOverride(true)"
            ></v-number-input>
            <!-- the scene's intro, though it wasn't there for its start -->
            <v-switch
                :model-value="character.scene_intro_visible"
                class="mt-2"
                color="primary"
                label="Show Scene Intro"
                messages="With character dependent history, the scene's intro is only for characters that were there when it started (or whose override reaches back to it). This shows it to this character anyway."
                @update:model-value="updateSceneIntroVisible"
            ></v-switch>
        </v-col>
        <v-col cols="12" lg="3">
            <v-switch
                v-model="character.narrative_omniscience_disable"
                label="Narrative Omniscience Disable"
                messages="This character sees display-revised versions of other messages in its prompts."
                :color="omniscienceDirty ? 'dirty' : 'primary'"
                @update:model-value="updateOmniscienceSettings"
            ></v-switch>
        </v-col>
        <v-col cols="12" lg="3">
            <v-switch
                v-model="character.history_omniscience_disable"
                label="History Omniscience Disable"
                messages="This character's display-revised messages are used when creating history."
                :color="omniscienceDirty ? 'dirty' : 'primary'"
                @update:model-value="updateOmniscienceSettings"
            ></v-switch>
        </v-col>
    </v-row>
    <v-row class="mb-2">
        <v-col cols="12" lg="6">
            <v-number-input
                v-model="character.converse_length_override"
                label="Converse Length Overwrite"
                :min="0"
                :step="16"
                control-variant="hidden"
                messages="Generation length (tokens) used when the conversation agent writes for this character. 0 uses the conversation agent setting."
                :color="converseLengthDirty ? 'dirty' : ''"
                @update:model-value="converseLengthDirty = true"
                @blur="updateConverseLengthOverride(true)"
            ></v-number-input>
        </v-col>
        <v-col cols="12" lg="6">
            <v-autocomplete
                v-model="character.lorebook_disabled"
                :items="character.lorebook_filter_options"
                item-title="name"
                item-value="id"
                label="Lorebook Disable Filter"
                multiple
                chips
                closable-chips
                clearable
                :no-data-text="'This scene has no lore entries.'"
                messages="Lore from the selected lorebooks is not used in this character's prompts. 'Scene lore' covers world entries that don't come from a lorebook."
                :color="lorebookDisabledDirty ? 'dirty' : ''"
                @update:model-value="updateLorebookDisabled"
            ></v-autocomplete>
            <!-- it only knows what it brought (talemate.imported_lore) -->
            <v-switch
                v-if="character.scene_knowledge_blocked || (character.origin_scenes || []).length"
                :model-value="character.scene_knowledge_blocked"
                class="mt-2"
                color="primary"
                label="Block New Scene Knowledge"
                :messages="character.scene_knowledge_blocked
                    ? 'This scene\'s own lore (also lorebooks added later) and its characters\' context database entries are kept from it. Turning this off keeps its lore filter as it is.'
                    : 'Keep this scene\'s own lore (also lorebooks added later) and its characters\' context database entries from it, it only knows what it brought.'"
                @update:model-value="updateSceneKnowledgeBlocked"
            ></v-switch>
        </v-col>
    </v-row>
    <!-- IMPORTED HISTORY (talemate.character_history) -->
    <v-card v-if="character.imported_history" variant="tonal" class="mb-4 imported-history">
        <v-card-title class="text-subtitle-1">
            <v-icon size="small" class="mr-1">mdi-history</v-icon>
            Imported History
        </v-card-title>
        <v-card-text>
            <div class="text-caption text-muted mb-3">
                What {{ character.name }} perceived in the scenes it was imported from. It goes before this scene's
                history in {{ character.name }}'s own prompts (its share of the budget is in the summarizer's Scene History settings).
            </div>
            <v-select
                :model-value="character.imported_history.mode"
                :items="importedHistoryModes"
                item-title="title"
                item-value="value"
                label="Kept"
                :messages="importedHistoryModes.find(m => m.value === character.imported_history.mode)?.subtitle"
                @update:model-value="(mode) => updateImportedHistory({ mode })"
            ></v-select>
            <div v-for="(chapter, index) in character.imported_history.chapters" :key="index" class="mt-4">
                <div class="text-body-2">
                    <v-icon size="small" class="mr-1">mdi-book-open-variant</v-icon>
                    {{ chapter.scene_title || 'Untitled scene' }}
                    <span class="text-caption text-muted ml-2">{{ chapterStats(chapter) }}</span>
                </div>
                <v-textarea
                    v-model="chapter.bridge"
                    class="mt-2"
                    label="Intro after it"
                    rows="1"
                    auto-grow
                    :messages="index === character.imported_history.chapters.length - 1
                        ? 'Optional. Comes between this history and this scene\'s.'
                        : 'Optional. Comes between this history and the next scene\'s.'"
                    :color="bridgeDirty[index] ? 'dirty' : ''"
                    @update:model-value="bridgeDirty[index] = true"
                    @blur="saveBridge(index)"
                ></v-textarea>
            </div>
            <!-- what it reads of the context database copied from there (talemate.imported_context) -->
            <div v-if="(character.imported_context || []).length" class="mt-4">
                <div class="text-caption text-muted mb-1">Context database entries it reads (copied from those scenes)</div>
                <div v-for="(source, index) in character.imported_context" :key="'context-' + index" class="text-body-2 mb-1">
                    <v-icon size="small" class="mr-1">mdi-database-arrow-right</v-icon>
                    {{ source.title || 'Untitled scene' }}
                    <span class="text-caption text-muted ml-2">{{ contextStats(source) }}</span>
                </div>
            </div>
        </v-card-text>
        <v-card-actions>
            <ConfirmActionInline
                action-label="Remove imported history"
                confirm-label="Confirm removal"
                icon="mdi-delete"
                @confirm="removeImportedHistory"
            />
        </v-card-actions>
    </v-card>

    <v-row class="mb-2">
        <v-col cols="12" lg="6">
            <v-text-field
                :model-value="character.room_name || 'Main Room'"
                label="Current Room"
                readonly
                prepend-inner-icon="mdi-door"
                messages="Move characters with Control Scene > Move Characters in the chat tools."
            ></v-text-field>
        </v-col>
        <v-col cols="12" lg="3">
            <v-number-input
                v-model="character.background_turn_count"
                label="Background Turn Count"
                :min="1"
                :step="1"
                control-variant="hidden"
                messages="While not in the player character's room, takes every Nth of its turns (1 = every turn)."
                :color="backgroundTurnCountDirty ? 'dirty' : ''"
                @update:model-value="backgroundTurnCountDirty = true"
                @blur="updateBackgroundTurnCount(true)"
            ></v-number-input>
        </v-col>
        <v-col cols="12" lg="3">
            <v-switch
                v-model="character.location_shown_always"
                label="Location Shown Always"
                messages="Everyone always knows which room this character is in, and its moves are always announced."
                :color="locationShownDirty ? 'dirty' : 'primary'"
                @update:model-value="updateLocationShownAlways"
            ></v-switch>
        </v-col>
    </v-row>
    <v-divider></v-divider>
    <v-row floating color="grey-darken-5">
        <v-col cols="3">

        </v-col>
        <v-col cols="3"></v-col>
        <v-col cols="2"></v-col>
        <v-col cols="4">
            <v-text-field v-model="newName"
                label="New detail" append-inner-icon="mdi-plus"
                class="mr-1 mb-1 mt-1" variant="underlined" density="compact"
                @keyup.enter="handleNew"
                hint="Descriptive name or question."></v-text-field>
        </v-col>
    </v-row>
    <v-divider></v-divider>

    <v-row>
        <v-col cols="4" style="max-height: 60vh; overflow: auto" class="mt-4">
            <v-list density="compact" slim v-model:opened="groupsOpen">
                <v-list-group value="templates" fluid>
                    <template v-slot:activator="{ props }">
                        <v-list-item  prepend-icon="mdi-cube-scan" v-bind="props">Templates</v-list-item>
                    </template>
                    <v-list-item>
                        <WorldStateManagerTemplateApplicator
                        ref="templateApplicator"
                        :validateTemplate="validateTemplate"
                        :templates="templates"
                        :source="source"
                        :template-types="['character_detail']"
                        @apply-selected="applyTemplates"
                        @done="applyTemplatesDone"
                    />
                    </v-list-item>
                </v-list-group>
            </v-list>
            <v-text-field v-model="search" v-if="Object.keys(this.character.details).length > 10"
            label="Filter details" append-inner-icon="mdi-magnify"
            clearable density="compact" variant="underlined"
            class="ml-1 mb-1 mt-1"
            @update:model-value="autoSelect"></v-text-field>
            <v-list :disabled="busy" density="compact" color="primary">
                <v-list-item
                    v-for="(value, detail) in filteredList"
                    :key="detail"
                    :active="selected === detail"
                    @click="selected = detail"
                >
                    <v-list-item-title class="text-caption">{{ detail }}
                        <v-icon v-if="character.shared_details.includes(detail)" color="highlight6" class="ml-1">mdi-earth</v-icon>
                    </v-list-item-title>
                </v-list-item>
            </v-list>
        </v-col>
        <v-col cols="8">


            <v-card v-if="selected && character.details[selected] !== undefined">

                <v-card-text>
                    <ContextualGenerate 
                        ref="contextualGenerate"
                        uid="wsm.character_detail"
                        :context="'character detail:'+selected" 

                        :original="character.details[selected]"
                        :character="character.name"
                        :templates="templates"
                        :generationOptions="generationOptions"
                        :specifyLength="true"

                        @generate="content => setAndUpdate(selected, content)"
                    />


                    <v-textarea rows="5" max-rows="18" auto-grow
                        ref="detail"
                        :label="selected"
                        :color="dirty ? 'dirty' : ''"

                        :disabled="busy"
                        :loading="busy"
                        :hint="autocompleteInfoMessage(busy)"

                        @keyup.ctrl.enter.stop="sendAutocompleteRequest"

                        @update:modelValue="dirty = true"
                        @blur="update(selected, true)"

                        v-model="character.details[selected]">
                    </v-textarea>

                </v-card-text>

                <v-card-actions>
                    <ConfirmActionInline action-label="Remove detail" confirm-label="Confirm removal" @confirm="remove(selected)" />
                    <v-btn v-if="!selectedIsShared && character.shared" color="highlight6" prepend-icon="mdi-earth" @click="setShared(selected, true)">
                        <v-tooltip activator="parent">
                            Add this detail to the shared world context.
                        </v-tooltip>
                        Share with world
                    </v-btn>
                    <v-btn v-else-if="selectedIsShared && character.shared" color="highlight6" prepend-icon="mdi-earth-off" @click="setShared(selected, false)">
                        <v-tooltip activator="parent">
                            Remove this detail from the shared world context.
                        </v-tooltip>
                        Unshare from world
                    </v-btn>
                    
                    <v-btn v-if="entryHasPin" @click="$emit('load-pin', selectedPinId)" color="primary" prepend-icon="mdi-pin">View pin</v-btn>
                    <v-btn v-else @click="$emit('add-pin', selectedPinId)" color="primary" prepend-icon="mdi-pin">Add pin</v-btn>

                    <v-spacer></v-spacer>
                    <v-btn v-if="character.reinforcements[selected]" @click.stop="viewCharacterStateReinforcer(selected)" color="primary" prepend-icon="mdi-image-auto-adjust">Manage auto state</v-btn>
                    <v-btn v-else @click.stop="viewCharacterStateReinforcer(selected)" color="primary" prepend-icon="mdi-image-auto-adjust">Setup auto state</v-btn>
                </v-card-actions>
            </v-card>
        </v-col>
    </v-row>
    <v-divider class="mt-2"></v-divider>
    <v-row class="mt-2 mb-2">
        <v-col cols="12" lg="6">
            <v-textarea
                v-model="character.scene_description_override"
                label="Scene Description Override"
                rows="3"
                auto-grow
                clearable
                messages="Blank uses the global scene description for this character."
                :color="sceneContextDirty ? 'dirty' : ''"
                @update:model-value="sceneContextDirty = true"
                @blur="updateSceneContext(true)"
            ></v-textarea>
        </v-col>
        <v-col cols="12" lg="6">
            <v-textarea
                v-model="character.scene_intent_override"
                label="Overall Intention Override"
                rows="3"
                auto-grow
                clearable
                messages="Blank uses the global overall intention for this character."
                :color="sceneContextDirty ? 'dirty' : ''"
                @update:model-value="sceneContextDirty = true"
                @blur="updateSceneContext(true)"
            ></v-textarea>
        </v-col>
    </v-row>
    <SpiceAppliedNotification :uids="['wsm.character_detail']"></SpiceAppliedNotification>

</template>

<script>
import ContextualGenerate from './ContextualGenerate.vue';
import WorldStateManagerTemplateApplicator from './WorldStateManagerTemplateApplicator.vue';
import SpiceAppliedNotification from './SpiceAppliedNotification.vue';
import ConfirmActionInline from './ConfirmActionInline.vue';

export default {
    name: 'WorldStateManagerCharacterDetails',
    components: {
        ContextualGenerate,
        WorldStateManagerTemplateApplicator,
        SpiceAppliedNotification,
        ConfirmActionInline,
    },
    props: {
        immutableCharacter: Object,
        templates: Object,
        generationOptions: Object,
        pins: Object,
    },
    data() {
        return {
            selected: null,
            newName: null,
            newValue: null,
            removeConfirm: false,
            search: null,
            dirty: false,
            sceneContextDirty: false,
            historyOverrideDirty: false,
            omniscienceDirty: false,
            converseLengthDirty: false,
            lorebookDisabledDirty: false,
            locationShownDirty: false,
            backgroundTurnCountDirty: false,
            // chapter index -> the intro after it was edited
            bridgeDirty: {},
            importedHistoryModes: [
                { value: 'clean', title: 'Clean', subtitle: 'Condensed further over time as this scene grows, until it is a compact recap.' },
                { value: 'unclean', title: 'Unclean', subtitle: 'Kept as it was, never summarized again. It gives way, oldest first, as this scene grows.' },
            ],
            busy: false,
            updateTimeout: null,
            character: null,
            groupsOpen: [],
            templateApplicatorCallback: null,
            source: "wsm.character_details",
        }
    },
    inject: [
        'getWebsocket',
        'autocompleteInfoMessage',
        'autocompleteRequest',
        'registerMessageHandler',
        'unregisterMessageHandler',
        'formatWorldStateTemplateString',
    ],
    emits:[
        'require-scene-save',
        'load-character-state-reinforcement',
        'load-pin',
        'add-pin',
    ],
    watch: {
        immutableCharacter: {
            immediate: true,
            handler(value) {
                if(value && this.character && value.name !== this.character.name) {
                    this.character = null;
                    this.selected = null;
                    this.newName = null;
                }
                if (!value) {
                    this.character = null;
                } else {
                    this.character = {
                        ...value,
                        scene_description_override: value.scene_description_override || '',
                        scene_intent_override: value.scene_intent_override || '',
                        character_dependent_history_override: value.character_dependent_history_override ?? 0,
                        narrative_omniscience_disable: value.narrative_omniscience_disable || false,
                        history_omniscience_disable: value.history_omniscience_disable || false,
                        converse_length_override: value.converse_length_override ?? 0,
                        lorebook_disabled: [...(value.lorebook_disabled || [])],
                        lorebook_filter_options: value.lorebook_filter_options || [],
                        location_shown_always: value.location_shown_always || false,
                        background_turn_count: value.background_turn_count || 1,
                        // its intros are edited here (talemate.character_history)
                        imported_history: value.imported_history
                            ? JSON.parse(JSON.stringify(value.imported_history))
                            : null,
                    };
                }
            }
        }
    },
    computed: {
        selectedIsShared() {
            return this.character.shared_details.includes(this.selected);
        },
        filteredList() {
            if(!this.character) {
                return {};
            }

            if (!this.search) {
                return this.character.details;
            }

            let result = {};
            for (let detail in this.character.details) {
                if (detail.toLowerCase().includes(this.search.toLowerCase()) || detail === this.selected) {
                    result[detail] = this.character.details[detail];
                }
            }
            return result;
        },
        selectedPinId() {
            if (!this.character || !this.selected) return null;
            return `${this.character.name}.detail.${this.selected}`;
        },
        entryHasPin() {
            return this.selectedPinId && this.pins && this.pins[this.selectedPinId];
        },
    },
    methods: {
        updateSceneIntroVisible(visible) {
            this.getWebsocket().send(JSON.stringify({
                type: 'world_state_manager',
                action: 'update_character_scene_intro_visible',
                name: this.character.name,
                visible: !!visible,
            }));
        },
        updateSceneKnowledgeBlocked(blocked) {
            this.getWebsocket().send(JSON.stringify({
                type: 'world_state_manager',
                action: 'update_character_scene_knowledge_blocked',
                name: this.character.name,
                blocked: !!blocked,
            }));
        },
        contextStats(source) {
            const parts = [];
            if (source.history) {
                parts.push(`${source.history} history ${source.history === 1 ? 'summary' : 'summaries'}`);
            }
            if (source.characters && source.characters.length) {
                parts.push(`info about ${source.characters.join(', ')}`);
            }
            if (source.private_access && source.private_access.length) {
                parts.push(`private info of ${source.private_access.join(', ')}`);
            }
            return parts.join(', ');
        },
        chapterStats(chapter) {
            const parts = [];
            if (chapter.summaries) {
                parts.push(`${chapter.summaries} ${chapter.summaries === 1 ? 'summary' : 'summaries'}`);
            }
            if (chapter.messages) {
                parts.push(`${chapter.messages} ${chapter.messages === 1 ? 'message' : 'messages'}`);
            }
            if (chapter.condensed) {
                parts.push(`${chapter.condensed} condensed`);
            }
            return parts.join(', ') || 'nothing perceived';
        },
        updateImportedHistory(data) {
            this.getWebsocket().send(JSON.stringify({
                type: 'world_state_manager',
                action: 'update_character_imported_history',
                name: this.character.name,
                ...data,
            }));
        },
        saveBridge(index) {
            if (!this.bridgeDirty[index]) {
                return;
            }
            const chapter = this.character.imported_history.chapters[index];
            this.updateImportedHistory({ bridges: { [index]: chapter.bridge || '' } });
        },
        removeImportedHistory() {
            this.getWebsocket().send(JSON.stringify({
                type: 'world_state_manager',
                action: 'remove_character_imported_history',
                name: this.character.name,
            }));
        },
        updateSceneContext(only_if_dirty = false) {
            if (only_if_dirty && !this.sceneContextDirty) {
                return;
            }

            this.getWebsocket().send(JSON.stringify({
                type: 'world_state_manager',
                action: 'update_character_scene_context',
                name: this.character.name,
                scene_description_override: this.character.scene_description_override || '',
                scene_intent_override: this.character.scene_intent_override || '',
            }));
        },

        updateHistoryOverride(only_if_dirty = false) {
            if (only_if_dirty && !this.historyOverrideDirty) {
                return;
            }

            const override = Number(this.character.character_dependent_history_override ?? 0);
            this.character.character_dependent_history_override = Math.max(
                Number.isInteger(override) ? override : 0,
                -1,
            );
            this.getWebsocket().send(JSON.stringify({
                type: 'world_state_manager',
                action: 'update_character_dependent_history_override',
                name: this.character.name,
                override: this.character.character_dependent_history_override,
            }));
        },

        updateOmniscienceSettings() {
            this.omniscienceDirty = true;
            this.getWebsocket().send(JSON.stringify({
                type: 'world_state_manager',
                action: 'update_character_omniscience_settings',
                name: this.character.name,
                narrative_omniscience_disable: this.character.narrative_omniscience_disable || false,
                history_omniscience_disable: this.character.history_omniscience_disable || false,
            }));
        },

        updateConverseLengthOverride(only_if_dirty = false) {
            if (only_if_dirty && !this.converseLengthDirty) {
                return;
            }

            const length = Number(this.character.converse_length_override ?? 0);
            this.character.converse_length_override = Math.max(
                Number.isInteger(length) ? length : 0,
                0,
            );
            this.getWebsocket().send(JSON.stringify({
                type: 'world_state_manager',
                action: 'update_character_converse_length_override',
                name: this.character.name,
                length: this.character.converse_length_override,
            }));
        },

        updateBackgroundTurnCount(only_if_dirty = false) {
            if (only_if_dirty && !this.backgroundTurnCountDirty) {
                return;
            }

            const count = Number(this.character.background_turn_count ?? 1);
            this.character.background_turn_count = Math.max(
                Number.isInteger(count) ? count : 1,
                1,
            );
            this.getWebsocket().send(JSON.stringify({
                type: 'world_state_manager',
                action: 'update_character_background_turn_count',
                name: this.character.name,
                count: this.character.background_turn_count,
            }));
        },

        updateLocationShownAlways() {
            this.locationShownDirty = true;
            this.getWebsocket().send(JSON.stringify({
                type: 'world_state_manager',
                action: 'update_character_location_shown_always',
                name: this.character.name,
                location_shown_always: this.character.location_shown_always || false,
            }));
        },

        updateLorebookDisabled() {
            this.lorebookDisabledDirty = true;
            this.getWebsocket().send(JSON.stringify({
                type: 'world_state_manager',
                action: 'update_character_lorebook_disabled',
                name: this.character.name,
                lorebook_ids: this.character.lorebook_disabled || [],
            }));
        },

        applyTemplates(templateUIDs, callback) {
            this.templateApplicatorCallback = callback;

            this.busy = true;

            // collect templates

            let templates = [];

            for (let group of this.templates.managed.groups) {
                for (let templateId in group.templates) {
                    let template = group.templates[templateId];
                    if(templateUIDs.includes(template.uid)) {
                        templates.push(template);
                    }
                }
            }

            this.getWebsocket().send(JSON.stringify({
                type: 'world_state_manager',
                action: 'apply_templates',
                templates: templates,
                run_immediately: true,
                character_name: this.character.name,
                source: this.source,
                generation_options: this.generationOptions,
            }));
        },
        
        applyTemplatesDone() {
            this.busy = false;
        },

        validateTemplate(template) {
            if(template.template_type !== 'character_detail')
                return false;
            const formattedDetail = this.formatWorldStateTemplateString(template.detail, this.character.name);

            if(this.character.details[formattedDetail]) {
                return false;
            }

            return true;
        },

        autoSelect() {
            this.selected = null;
            // if there is only one detail in the filtered list, select it
            if (Object.keys(this.filteredList).length > 0) {
                this.selected = Object.keys(this.filteredList)[0];
            }
        },

        update(name, only_if_dirty = false) {

            if(only_if_dirty && !this.dirty) {
                return;
            }

            // if field is currently empty, don't send update, because that
            // will cause a deletion
            if (this.character.details[name] === "") {
                return;
            }

            // if value == "$DELETE", blank it out
            if (this.character.details[name] === "$DELETE") {
                this.character.details[name] = "";
            }

            return this.getWebsocket().send(JSON.stringify({
                type: 'world_state_manager',
                action: 'update_character_detail',
                name: this.character.name,
                detail: name,
                value: this.character.details[name],
            }));
        },

        setShared(name, shared) {
            this.getWebsocket().send(JSON.stringify({
                type: 'world_state_manager',
                action: 'update_character_shared_detail',
                name: this.character.name,
                detail: name,
                shared: shared,
            }));
        },

        setAndUpdate(name, value) {
            this.character.details[name] = value;
            this.update(name);
        },

        handleNew() {
            this.character.details[this.newName] = "";
            this.selected = this.newName;
            this.newName = null;
            // set focus to the new detail
            this.$nextTick(() => {
                this.$refs.detail.focus();
            });
        },

        remove(name) {
            // set value to blank
            this.character.details[name] = "$DELETE";
            this.removeConfirm = false;
            // send update
            this.update(name);
            // remove detail from list
            delete this.character.details[name];
            this.selected = null;
        },

        sendAutocompleteRequest() {
            this.busy = true;
            this.autocompleteRequest({
                partial: this.character.details[this.selected],
                context: `character detail:${this.selected}`,
                character: this.character.name
            }, (completion) => {
                this.character.details[this.selected] += completion;
                this.busy = false;
            }, this.$refs.detail);

        },
        
        viewCharacterStateReinforcer(name) {
            this.$emit('load-character-state-reinforcement', name)
        },

        handleMessage(message) {
            if (message.type !== 'world_state_manager') {
                return;
            }
            
            else if (message.action === 'character_detail_updated') {
                this.dirty = false;
                this.$emit('require-scene-save');
            }
            else if (message.action === 'character_scene_context_updated') {
                this.sceneContextDirty = false;
                this.$emit('require-scene-save');
            }
            else if (message.action === 'character_scene_intro_visible_updated') {
                this.$emit('require-scene-save');
            }
            else if (message.action === 'character_scene_knowledge_updated') {
                this.$emit('require-scene-save');
            }
            else if (message.action === 'character_imported_history_updated') {
                this.bridgeDirty = {};
                this.$emit('require-scene-save');
            }
            else if (message.action === 'character_dependent_history_override_updated') {
                this.historyOverrideDirty = false;
                this.$emit('require-scene-save');
            }
            else if (message.action === 'character_omniscience_settings_updated') {
                this.omniscienceDirty = false;
                this.$emit('require-scene-save');
            }
            else if (message.action === 'character_converse_length_override_updated') {
                this.converseLengthDirty = false;
                this.$emit('require-scene-save');
            }
            else if (['characters_moved', 'room_deleted', 'room_updated'].includes(message.action) && this.character) {
                // the room shown may have changed
                this.getWebsocket().send(JSON.stringify({
                    type: 'world_state_manager',
                    action: 'get_character_details',
                    name: this.character.name,
                }));
            }
            else if (message.action === 'character_background_turn_count_updated') {
                this.backgroundTurnCountDirty = false;
                this.$emit('require-scene-save');
            }
            else if (message.action === 'character_location_shown_always_updated') {
                this.locationShownDirty = false;
                this.$emit('require-scene-save');
            }
            else if (message.action === 'character_lorebook_disabled_updated') {
                this.lorebookDisabledDirty = false;
                this.$emit('require-scene-save');
            }
            else if (message.action === 'character_detail_deleted') {
                if(message.data.name === this.selected) {
                    this.selected = null;
                }
                this.$emit('require-scene-save');
            }
            else if (message.action === 'template_applied' && message.source === this.source){
                
                if(this.templateApplicatorCallback && message.status === 'done') {
                    this.templateApplicatorCallback();
                    this.templateApplicatorCallback = null;
                }

                if(message.result && message.result.character === this.character.name){
                    let detail = message.result.detail;
                    this.character.details[detail] = message.result.value;
                    this.selected = detail;
                }
            }
            else if (message.action === 'templates_applied' && message.source === this.source) {
                if(this.templateApplicatorCallback) {
                    this.templateApplicatorCallback();
                    this.templateApplicatorCallback = null;
                }
            }
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
