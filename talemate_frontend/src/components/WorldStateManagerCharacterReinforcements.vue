<template>
    <v-row floating color="grey-darken-5">
        <v-col cols="3">
        </v-col>
        <v-col cols="3"></v-col>
        <v-col cols="2"></v-col>
        <v-col cols="4">
            <v-text-field v-model="newQuestion"
                label="New state" append-inner-icon="mdi-plus"
                class="mr-1 mb-1 mt-1" variant="underlined" density="compact"
                @keyup.enter="handleNew"
                hint="Question or detail name."></v-text-field>
        </v-col>
    </v-row>
    <v-divider></v-divider>
    <v-row>
        <v-col cols="5" style="max-height: 60vh; overflow: auto" class="mt-4">
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
                        :template-types="['state_reinforcement']"
                        @apply-selected="applyTemplates"
                        @done="applyTemplatesDone"
                    />
                    </v-list-item>
                </v-list-group>
            </v-list>
            <v-text-field v-model="search" v-if="filteredList.length > 10"
                label="Filter" append-inner-icon="mdi-magnify"
                clearable density="compact" variant="underlined"
                class="ml-1 mb-1 mt-1"
                @update:modelValue="autoSelect"></v-text-field>
            <v-list :disabled="busy" density="compact" color="highlight1">
                <v-list-item
                    v-for="(value, detail) in filteredList"
                    :key="detail"
                    class="text-caption"
                    :active="selected === detail"
                    @click="selected = detail"
                >
                    <v-list-item-title>
                        <div class="text-left">{{ detail }}
                            <v-icon v-if="value.private" color="warning" size="small">mdi-lock</v-icon>
                            <v-icon v-if="value.paused" color="warning" size="small">mdi-pause-circle</v-icon>
                            <div>
                                <v-chip size="x-small" label variant="outlined" :color="onHold(value) ? 'warning' : 'info'">{{ dueLabel(value) }}</v-chip>
                                <v-chip v-if="value.priority" size="x-small" label variant="outlined" color="info" class="ml-1">priority {{ value.priority }}</v-chip>
                            </div>
                        </div>
                    </v-list-item-title>
                </v-list-item>
            </v-list>
        </v-col>
        <v-col cols="7">
            <v-alert v-if="character && character.muted" type="info" variant="tonal" density="compact" class="mb-3" icon="mdi-volume-off">
                {{ character.name }} is muted. Their state reinforcements won't count down or update until they're unmuted.
            </v-alert>
            <div v-if="selected && character.reinforcements[selected] !== undefined">
                <v-alert v-if="character.reinforcements[selected].paused" type="warning" variant="tonal" density="compact" class="mb-3" icon="mdi-pause-circle">
                    Paused. This state keeps its current value and won't count down or update until resumed. Refresh State still updates it manually.
                </v-alert>
                <v-textarea rows="5" auto-grow max-rows="15"
                    :label="selected"
                    :disabled="working"
                    v-model="character.reinforcements[selected].answer"
                    @update:modelValue="dirty = true"
                    @blur="update(selected, false, true)"
                    :color="dirty ? 'dirty' : ''">
                </v-textarea>

                <div class="d-flex flex-wrap align-center mb-3">
                    <v-btn-toggle
                        v-model="character.reinforcements[selected].private"
                        mandatory
                        density="compact"
                        color="primary"
                        class="mr-6"
                        @update:model-value="setPrivacy(selected, $event)"
                    >
                        <v-btn :value="false" prepend-icon="mdi-earth">Public</v-btn>
                        <v-btn :value="true" prepend-icon="mdi-lock">Private</v-btn>
                    </v-btn-toggle>

                    <div class="d-flex align-center">
                        <span class="text-caption text-medium-emphasis mr-2">Convo update order</span>
                        <v-btn-toggle
                            v-model="character.reinforcements[selected].update_order"
                            mandatory
                            density="compact"
                            color="primary"
                            :disabled="working"
                            @update:model-value="setUpdateOrder(selected, $event)"
                        >
                            <v-btn value="before" prepend-icon="mdi-arrow-collapse-up">Before</v-btn>
                            <v-btn value="after" prepend-icon="mdi-arrow-collapse-down">After</v-btn>
                            <v-btn value="any" prepend-icon="mdi-shuffle-variant">Any</v-btn>
                        </v-btn-toggle>
                        <v-tooltip max-width="400" location="top">
                            <template v-slot:activator="{ props }">
                                <v-icon v-bind="props" size="small" class="ml-2" color="grey">mdi-help-circle-outline</v-icon>
                            </template>
                            <div><strong>Before</strong>: updates right before {{ character.name }}'s line, so the answer can shape what they say.</div>
                            <div><strong>After</strong>: updates right after {{ character.name }}'s line.</div>
                            <div class="mb-2">Before and After only count {{ character.name }}'s own turns. On lines you write for {{ character.name }}, a due update always runs after the line.</div>
                            <div><strong>Any</strong>: legacy timing. Counts every round no matter who speaks and updates at the start of a round.</div>
                        </v-tooltip>
                    </div>
                </div>

                <v-row>
                    <v-col cols="4">
                        <v-number-input
                            v-model="character.reinforcements[selected].interval"
                            :label="intervalLabel(character.reinforcements[selected])"
                            :min="1" :max="100" :step="1"
                            :disabled="working"
                            class="mb-2"
                            @update:modelValue="dirty = true"
                            @blur="update(selected, false, true)"
                            control-variant="hidden"
                            :color="dirty ? 'dirty' : ''"></v-number-input>
                    </v-col>
                    <v-col cols="3">
                        <v-number-input
                            v-model="character.reinforcements[selected].priority"
                            label="Update priority"
                            :step="1"
                            :disabled="working"
                            class="mb-2"
                            hint="Higher updates first when several of this character's states update at the same time. Before, After and Any are ordered separately."
                            @update:modelValue="dirty = true"
                            @blur="update(selected, false, true)"
                            control-variant="hidden"
                            :color="dirty ? 'dirty' : ''"></v-number-input>
                    </v-col>
                    <v-col cols="5">
                        <v-select
                            v-model="character.reinforcements[selected].insert"
                            :items="insertionModes"
                            :disabled="working"
                            label="Context Attachment Method"
                            class="mr-1 mb-1" variant="underlined"
                            @update:modelValue="dirty = true"
                            @blur="update(selected, false, true)"
                            :color="dirty ? 'dirty' : ''">
                        </v-select>
                    </v-col>
                </v-row>



                <v-textarea rows="3" auto-grow max-rows="5"
                    label="Additional instructions to the AI for generating this state."
                    v-model="character.reinforcements[selected].instructions"
                    @update:modelValue="dirty = true"
                    @blur="update(selected, false, true)"
                    :disabled="working"
                    :color="dirty ? 'dirty' : ''"
                    ></v-textarea>

                <v-checkbox
                    v-model="character.reinforcements[selected].require_active"
                    label="Require character active"
                    @update:modelValue="dirty = true"
                    @blur="update(selected, false, true)"
                    :disabled="working"
                    :color="dirty ? 'dirty' : 'primary'"
                    messages="Only progress this reinforcement when the character is active in the scene.">
                </v-checkbox>

                <v-row class="mt-4">
                    <v-col cols="6">
                        <div
                            v-if="removeConfirm === false">
                            <v-btn rounded="sm" prepend-icon="mdi-close-box-outline"
                                color="delete" variant="text"
                                :disabled="working"
                                @click.stop="removeConfirm = true">
                                Remove state
                            </v-btn>
                        </div>
                        <div v-else>
                            <v-btn rounded="sm" prepend-icon="mdi-close-box-outline"
                                @click.stop="remove(selected)"
                                :disabled="working"
                                color="delete" variant="text">
                                Confirm removal
                            </v-btn>
                            <v-btn class="ml-1" rounded="sm"
                                prepend-icon="mdi-cancel"
                                @click.stop="removeConfirm = false"
                                :disabled="working"
                                color="info" variant="text">
                                Cancel
                            </v-btn>
                        </div>
                    </v-col>
                    <v-col cols="6" class="text-right flex">
                        <v-btn rounded="sm"
                            :prepend-icon="character.reinforcements[selected].paused ? 'mdi-play' : 'mdi-pause'"
                            @click.stop="togglePause(selected)"
                            :disabled="working"
                            :color="character.reinforcements[selected].paused ? 'success' : 'warning'" variant="text">
                            {{ character.reinforcements[selected].paused ? 'Resume' : 'Pause' }}
                        </v-btn>
                        <v-btn rounded="sm" prepend-icon="mdi-refresh"
                            @click.stop="run(selected)"
                            :disabled="working"
                            color="primary" variant="text">
                            Refresh State
                        </v-btn>
                        <v-tooltip
                            text="Removes all previously generated reinforcements for this state and then regenerates it">
                            <template v-slot:activator="{ props }">
                                <v-btn
                                    v-if="resetConfirm === true"
                                    v-bind="props" rounded="sm"
                                    prepend-icon="mdi-backup-restore"
                                    @click.stop="run(selected, true)"
                                    :disabled="working"
                                    color="warning" variant="text">
                                    Confirm Reset State
                                </v-btn>
                                <v-btn v-else v-bind="props" rounded="sm"
                                    prepend-icon="mdi-backup-restore"
                                    @click.stop="resetConfirm = true"
                                    :disabled="working"
                                    color="warning" variant="text">
                                    Reset State
                                </v-btn>
                            </template>
                        </v-tooltip>
                    </v-col>
                </v-row>
            </div>
        </v-col>
    </v-row>
    <WorldStateManagerCharacterPrivateViewers
        v-if="character"
        v-model="character.states_private_viewers"
        section="states"
        :character-name="character.name"
        :character-names="character.available_character_names"
        @require-scene-save="$emit('require-scene-save')"
    />
</template>
<script>

import WorldStateManagerTemplateApplicator from './WorldStateManagerTemplateApplicator.vue';
import WorldStateManagerCharacterPrivateViewers from './WorldStateManagerCharacterPrivateViewers.vue';

export default {
    name: "WorldStateManagerCharacterReinforcements",
    components: {
        WorldStateManagerTemplateApplicator,
        WorldStateManagerCharacterPrivateViewers,
    },
    props: {
        immutableCharacter: Object,
        templates: Object,
    },
    data() {
        return {
            selected: null,
            newQuestion: null,
            newValue: null,
            removeConfirm: false,
            resetConfirm: false,
            search: null,
            dirty: false,
            busy: false,
            updateTimeout: null,
            character: null,
            showTemplates: false,
            groupsOpen: ["states"],
            templateApplicatorCallback: null,
            source: "wsm.character_reinforcements",
            newReinforcment: {
                interval: 10,
                instructions: '',
                insert: "sequential",
                require_active: true,
                private: false,
                update_order: "after",
                priority: 0,
                paused: false,
            }
        }
    },
    inject: [
        'getWebsocket',
        'autocompleteInfoMessage',
        'autocompleteRequest',
        'registerMessageHandler',
        'insertionModes',
        'toLabel',
        'formatWorldStateTemplateString',
    ],
    emits:[
        'require-scene-save'
    ],
    watch: {
        immutableCharacter: {
            immediate: true,
            handler(value) {
                if(value && this.character && value.name !== this.character.name) {
                    this.selected = null;
                }
                if (!value) {
                    this.character = null;
                } else {
                    this.character = {
                        ...value,
                        reinforcements: Object.fromEntries(
                            Object.entries(value.reinforcements || {}).map(([name, reinforcement]) => [
                                name,
                                { private: false, update_order: "after", priority: 0, paused: false, ...reinforcement },
                            ])
                        ),
                        states_private_viewers: [...(value.states_private_viewers || [])],
                    };
                }
            }
        }
    },
    computed: {
        working() {
            return (this.busy || this.templateApplicatorCallback !== null)
        },
        filteredList() {
            if(!this.character) {
                return {};
            }

            if (!this.search) {
                return this.character.reinforcements;
            }

            let filtered = {};
            for (let detail in this.character.reinforcements) {
                if (detail.toLowerCase().includes(this.search.toLowerCase()) || detail === this.selected) {
                    filtered[detail] = this.character.reinforcements[detail];
                }
            }
            return filtered;
        },
    },
    methods: {

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
            }));
        },

        applyTemplatesDone() {
            this.busy = false;
        },


        validateTemplate(template) {
            if (template.template_type !== 'state_reinforcement') {
                return false;
            }

            const formattedQuery = this.formatWorldStateTemplateString(template.query, this.character.name);

            if(this.character.reinforcements[formattedQuery]) {
                return false;
            }

            let validStateTypes = ["character"];

            if(this.character.is_player) {
                validStateTypes.push("player");
            } else{
                validStateTypes.push("npc");
            }

            if (validStateTypes.includes(template.state_type)) {
                return true;
            }

            return false;
        },

        autoSelect() {
            this.selected = null
            // if there is only one detail in the filtered list, select it
            if (Object.keys(this.filteredList).length > 0) {
                this.selected = Object.keys(this.filteredList)[0];
            }
        },

        loadWithRequire(name) {
            if (this.character.reinforcements[name]) {
                this.selected = name;
            } else {
                this.add(name);
                this.update(name, true);
                this.selected = name;
            }
        },

        handleNew() {
            this.add(this.newQuestion);
            this.update(this.newQuestion, true);
            this.selected = this.newQuestion;
            this.newQuestion = null;
        },

        add(name) {
            this.character.reinforcements[name] = {...this.newReinforcment};
        },

        update(name, updateState, only_if_dirty = false) {

            if(only_if_dirty && !this.dirty) {
                return;
            }

            let interval = this.character.reinforcements[name].interval;
            let instructions = this.character.reinforcements[name].instructions;
            let insert = this.character.reinforcements[name].insert;
            let require_active = this.character.reinforcements[name].require_active !== undefined 
                ? this.character.reinforcements[name].require_active 
                : true;
            let privateState = this.character.reinforcements[name].private || false;
            let updateOrder = this.character.reinforcements[name].update_order || "after";
            let priority = parseInt(this.character.reinforcements[name].priority) || 0;
            let paused = this.character.reinforcements[name].paused || false;
            this.busy = true;
            this.getWebsocket().send(JSON.stringify({
                type: 'world_state_manager',
                action: 'set_character_detail_reinforcement',
                name: this.character.name,
                question: name,
                interval: interval,
                instructions: instructions,
                answer: this.character.reinforcements[name].answer,
                update_state: updateState,
                insert: insert,
                require_active: require_active,
                private: privateState,
                update_order: updateOrder,
                priority: priority,
                paused: paused,
            }));
        },

        togglePause(name) {
            this.character.reinforcements[name].paused = !this.character.reinforcements[name].paused;
            this.dirty = true;
            this.update(name, false);
        },

        onHold(reinforcement) {
            return reinforcement.paused || (this.character && this.character.muted);
        },

        setPrivacy(name, isPrivate) {
            this.character.reinforcements[name].private = isPrivate;
            this.dirty = true;
            this.update(name, false);
        },

        setUpdateOrder(name, updateOrder) {
            this.character.reinforcements[name].update_order = updateOrder;
            this.dirty = true;
            this.update(name, false);
        },

        countsOwnTurns(reinforcement) {
            return reinforcement.update_order === 'before' || reinforcement.update_order === 'after';
        },

        intervalLabel(reinforcement) {
            if (this.countsOwnTurns(reinforcement)) {
                return `Re-inforce / Update detail every N of ${this.character.name}'s turns`;
            }
            return "Re-inforce / Update detail every N rounds";
        },

        dueLabel(reinforcement) {
            if (reinforcement.paused) {
                return "paused";
            }
            if (this.character && this.character.muted) {
                return "on hold (muted)";
            }
            if (!this.countsOwnTurns(reinforcement)) {
                return `update in ${reinforcement.due} turns`;
            }
            // due counts the character's remaining turns, the update happens on the last one
            const turns = Math.max(reinforcement.due, 1);
            return `update ${reinforcement.update_order} ${turns === 1 ? 'next turn' : `turn ${turns}`}`;
        },

        remove(name) {
            this.getWebsocket().send(JSON.stringify({
                type: 'world_state_manager',
                action: 'delete_character_detail_reinforcement',
                name: this.character.name,
                question: name,
            }));

            if (this.character.reinforcements[name])
                delete this.character.reinforcements[name];

            this.removeConfirm = false;

            // select first detail
            if (this.selected == name)
                this.selected = Object.keys(this.character.reinforcements)[0];
        },

        run(name, reset) {
            this.busy = true;

            this.getWebsocket().send(JSON.stringify({
                type: 'world_state_manager',
                action: 'run_character_detail_reinforcement',
                name: this.character.name,
                question: name,
                reset: reset || false,
            }));

            this.resetConfirm = false;
        },

        handleMessage(message) {

            if (message.type !== 'world_state_manager') {
                return;
            } 
            else if (message.action === 'character_detail_reinforcement_set') {
                this.dirty = false;
                this.busy = false;
                this.$emit('require-scene-save');
            }
            else if (message.action === 'character_detail_reinforcement_run') {
                this.dirty = false;
                this.busy = false;
                this.$emit('require-scene-save');
            }
            else if (message.action === 'character_detail_reinforcement_deleted') {
                this.$emit('require-scene-save');
            }
            else if (message.action === 'template_applied' && message.source === this.source){
                
                if(this.templateApplicatorCallback && message.status === 'done') {
                    this.templateApplicatorCallback();
                    this.templateApplicatorCallback = null;
                }

                if(message.result && message.result.character === this.character.name){
                    this.character.reinforcements[message.result.question] = message.result;
                    this.selected = message.result.question;
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
    created() {
        this.registerMessageHandler(this.handleMessage);
    }
}

</script>
