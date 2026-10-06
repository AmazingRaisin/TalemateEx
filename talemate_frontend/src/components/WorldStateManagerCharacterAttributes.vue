<template>
    <v-row floating color="grey-darken-5">
        <v-col cols="3">
        </v-col>
        <v-col cols="3"></v-col>
        <v-col cols="2"></v-col>
        <v-col cols="4">
            <v-text-field v-model="newName"
                label="New attribute" append-inner-icon="mdi-plus"
                class="mr-1 mb-1 mt-1" variant="underlined" density="compact"
                @keyup.enter="handleNew"
                hint="Attribute name"></v-text-field>

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
                            :template-types="['character_attribute']"
                            @apply-selected="applyTemplates"
                            @done="applyTemplatesDone"/>
                    </v-list-item>
                </v-list-group>
            </v-list>
            <v-text-field v-model="search" v-if="Object.keys(this.character.base_attributes).length > 10"
                label="Filter" append-inner-icon="mdi-magnify"
                clearable density="compact" variant="underlined"
                clear-icon="mdi-close"
                class="ml-1 mb-1 mt-1"
                @update:modelValue="autoSelect"></v-text-field>
            <v-list :disabled="busy" density="compact" color="primary">
                <v-list-item
                    v-for="(value, attribute) in filteredList"
                    :key="attribute"
                    :active="selected === attribute"
                    @click="selected = attribute"
                >
                    <v-list-item-title>
                        {{ attribute }}
                        <v-icon v-if="character.private_attributes?.includes(attribute)" color="warning" size="small">mdi-lock</v-icon>
                        <v-icon v-if="character.self_attributes?.includes(attribute)" color="primary" size="small">mdi-account-eye</v-icon>
                        <v-icon v-if="character.shared_attributes?.includes(attribute)" color="highlight6" size="small">mdi-earth</v-icon>
                    </v-list-item-title>
                </v-list-item>
            </v-list>

        </v-col>
        <v-col cols="8">
            <v-card v-if="selected !== null && character !== null && character.base_attributes[selected] !== undefined">
                <v-card-text>
                    <div class="d-flex flex-wrap align-center mb-3 privacy-toggles">
                        <v-btn-toggle
                            :model-value="selectedIsPrivate"
                            mandatory
                            density="compact"
                            color="primary"
                            class="mr-2"
                            @update:model-value="setAttributePrivate(selected, $event)"
                        >
                            <v-btn :value="false" prepend-icon="mdi-earth">Public</v-btn>
                            <v-btn :value="true" prepend-icon="mdi-lock">Private</v-btn>
                        </v-btn-toggle>
                        <!-- what only the character knows of itself (either way) -->
                        <v-btn-toggle
                            :model-value="selectedIsSelf ? true : null"
                            density="compact"
                            color="primary"
                            class="self-toggle"
                            @update:model-value="setAttributeSelf(selected, !!$event)"
                        >
                            <v-btn :value="true" prepend-icon="mdi-account-eye">Self</v-btn>
                        </v-btn-toggle>
                    </div>

                    <ContextualGenerate 
                        ref="contextualGenerate"
                        uid="wsm.character_attribute"
                        :context="'character attribute:'+selected" 

                        :original="character.base_attributes[selected]"
                        :character="character.name"
                        :templates="templates"
                        :generationOptions="generationOptions"
                        :specifyLength="true"

                        @generate="content => setAndUpdate(selected, content)"
                    />

                    <v-textarea ref="attribute" rows="5" auto-grow
                        :label="selected"
                        :color="dirty ? 'dirty' : ''"

                        :disabled="busy"
                        :loading="busy"
                        
                        :hint="autocompleteInfoMessage(busy)"
                        @keyup.ctrl.enter.stop="sendAutocompleteRequest"

                        @update:modelValue="dirty = true"
                        @blur="update(selected, true)"

                        v-model="character.base_attributes[selected]">
                    </v-textarea>

                    <v-textarea v-if="selectedIsPrivate"
                        rows="3" auto-grow
                        :label="`Public ${selected}`"
                        :color="dirty ? 'dirty' : ''"
                        :disabled="busy"
                        messages="Shown in prompts for other characters."
                        @update:modelValue="dirty = true"
                        @blur="update(selected, true)"
                        v-model="character.public_attributes[selected]">
                    </v-textarea>

                    <v-textarea v-if="selectedIsSelf"
                        rows="3" auto-grow
                        class="self-value"
                        :label="`Self ${selected}`"
                        :color="dirty ? 'dirty' : ''"
                        :disabled="busy"
                        :messages="`Only in ${character.name}'s own prompts, in place of the other value. Character progression updates this one.`"
                        @update:modelValue="dirty = true"
                        @blur="update(selected, true)"
                        v-model="character.self_attribute_values[selected]">
                    </v-textarea>
                </v-card-text>
                <v-card-actions>
                    <ConfirmActionInline
                        action-label="Remove attribute"
                        confirm-label="Confirm removal"
                        @confirm="remove(selected)"
                    />
                    <v-btn v-if="!selectedIsShared && character.shared" color="highlight6" prepend-icon="mdi-earth" @click="setShared(selected, true)">
                        <v-tooltip activator="parent">
                            Add this attribute to the shared world context.
                        </v-tooltip>
                        Share with world</v-btn>
                    <v-btn v-else-if="selectedIsShared && character.shared" color="highlight6" prepend-icon="mdi-earth-off" @click="setShared(selected, false)">
                        <v-tooltip activator="parent">
                            Remove this attribute from the shared world context.
                        </v-tooltip>
                        Unshare from world</v-btn>
                </v-card-actions>
            </v-card>
        </v-col>
    </v-row>
    <WorldStateManagerCharacterPrivateViewers
        v-if="character"
        v-model="character.attributes_private_viewers"
        section="attributes"
        :character-name="character.name"
        :character-names="character.available_character_names"
        @require-scene-save="$emit('require-scene-save')"
    />
    <SpiceAppliedNotification :uids="['wsm.character_attribute']"></SpiceAppliedNotification>
</template>
<script>

import ConfirmActionInline from './ConfirmActionInline.vue';
import ContextualGenerate from './ContextualGenerate.vue';
import WorldStateManagerTemplateApplicator from './WorldStateManagerTemplateApplicator.vue';
import SpiceAppliedNotification from './SpiceAppliedNotification.vue';
import WorldStateManagerCharacterPrivateViewers from './WorldStateManagerCharacterPrivateViewers.vue';

export default {
    name: 'WorldStateManagerCharacterAttributes',
    components: {
        ContextualGenerate,
        WorldStateManagerTemplateApplicator,
        SpiceAppliedNotification,
        ConfirmActionInline,
        WorldStateManagerCharacterPrivateViewers,
    },
    props: {
        immutableCharacter: Object,
        templates: Object,
        generationOptions: Object,
    },
    data() {
        return {
            selected: null,
            newName: null,
            newValue: null,
            removeConfirm: false,
            search: null,
            dirty: false,
            busy: false,
            updateTimeout: null,
            character: null,
            groupsOpen: [],
            source: "wsm.character_attributes",
            templateApplicatorCallback: null,
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
        'require-scene-save'
    ],
    watch: {
        immutableCharacter: {
            immediate: true,
            handler(value) {
                if(value && this.character && value.name !== this.character.name) {
                    this.character = null;
                    this.selected = null;
                }
                if (!value) {
                    this.selected = null;
                    this.character = null;
                } else {
                    this.character = {
                        ...value,
                        private_attributes: [...(value.private_attributes || [])],
                        public_attributes: { ...(value.public_attributes || {}) },
                        self_attributes: [...(value.self_attributes || [])],
                        self_attribute_values: { ...(value.self_attribute_values || {}) },
                        attributes_private_viewers: [...(value.attributes_private_viewers || [])],
                    };
                }
            }
        }
    },
    computed: {
        selectedIsShared() {
            return this.character?.shared_attributes?.includes(this.selected) || false;
        },
        selectedIsPrivate() {
            return this.character?.private_attributes?.includes(this.selected) || false;
        },
        selectedIsSelf() {
            return this.character?.self_attributes?.includes(this.selected) || false;
        },
        filteredList() {
            if(!this.character) {
                return {};
            }

            if (!this.search) {
                return this.character.base_attributes;
            }

            let filtered = {};
            for (let attribute in this.character.base_attributes) {
                if (attribute.toLowerCase().includes(this.search.toLowerCase()) || attribute === this.selected) {
                    filtered[attribute] = this.character.base_attributes[attribute];
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
                generation_options: this.generationOptions,
            }));
        },

        applyTemplatesDone() {
            this.busy = false;
        },
        
        validateTemplate(template) {
            if(template.template_type !== 'character_attribute')
                return false;
            const formattedName = this.formatWorldStateTemplateString(template.attribute, this.character.name);

            if(this.character.base_attributes[formattedName]) {
                return false;
            }

            return true;
        },

        reset() {
            this.selected = null;
            this.character = null;
            this.templateApplicatorCallback = null;
            this.groupsOpen = [];
        },

        autoSelect() {
            this.selected = null;
            // if there is only one attribute in the filtered list, select it
            if (Object.keys(this.filteredList).length > 0) {
                this.selected = Object.keys(this.filteredList)[0];
            }
        },

        update(name, only_if_dirty = false) {

            if(only_if_dirty && !this.dirty) {
                return;
            }

            return this.getWebsocket().send(JSON.stringify({
                type: 'world_state_manager',
                action: 'update_character_attribute',
                name: this.character.name,
                attribute: name,
                value: this.character.base_attributes[name],
                private: this.character.private_attributes?.includes(name) || false,
                public_value: this.character.public_attributes?.[name] || '',
                self_enabled: this.character.self_attributes?.includes(name) || false,
                // kept while it is off, left alone if there is none
                self_value: this.character.self_attribute_values?.[name],
            }));
        },

        setAttributeSelf(name, enabled) {
            if (!name) {
                return;
            }
            this.character.self_attributes = [...(this.character.self_attributes || [])];
            this.character.self_attribute_values = { ...(this.character.self_attribute_values || {}) };

            if (enabled) {
                if (!this.character.self_attributes.includes(name)) {
                    this.character.self_attributes.push(name);
                }
                // it starts as the value (private or public, whichever it is)
                if (!this.character.self_attribute_values[name]) {
                    this.character.self_attribute_values[name] = this.character.base_attributes[name] || '';
                }
            } else {
                this.character.self_attributes = this.character.self_attributes.filter(attribute => attribute !== name);
            }

            this.dirty = true;
            this.update(name);
        },

        setAttributePrivate(name, isPrivate) {
            if (!name) {
                return;
            }
            this.character.private_attributes = [...(this.character.private_attributes || [])];
            this.character.public_attributes = { ...(this.character.public_attributes || {}) };

            if (isPrivate) {
                if (!this.character.private_attributes.includes(name)) {
                    this.character.private_attributes.push(name);
                }
                if (this.character.public_attributes[name] === undefined) {
                    this.character.public_attributes[name] = '';
                }
            } else {
                this.character.private_attributes = this.character.private_attributes.filter(attribute => attribute !== name);
                delete this.character.public_attributes[name];
            }

            this.dirty = true;
            this.update(name);
        },

        setShared(name, shared) {
            const payload = {
                type: 'world_state_manager',
                action: 'update_character_shared_attribute',
                name: this.character.name,
                attribute: name,
                shared: shared,
            }
            this.getWebsocket().send(JSON.stringify(payload));
        },

        setAndUpdate(name, value) {
            this.character.base_attributes[name] = value;
            this.update(name);
        },

        handleNew() {
            this.character.base_attributes[this.newName] = "";
            this.selected = this.newName;
            this.newName = null;
            // set focus to the new attribute
            this.$nextTick(() => {
                this.$refs.attribute.focus();
            });
        },

        remove(name) {
            // set value to blank
            this.character.base_attributes[name] = "";
            this.removeConfirm = false;
            // send update
            this.update(name);
            // remove attribute from list
            delete this.character.base_attributes[name];
            delete this.character.public_attributes[name];
            delete this.character.self_attribute_values[name];
            this.character.private_attributes = this.character.private_attributes.filter(attribute => attribute !== name);
            this.character.self_attributes = this.character.self_attributes.filter(attribute => attribute !== name);
            this.selected = null;
        },

        sendAutocompleteRequest() {
            this.busy = true;
            this.autocompleteRequest({
                partial: this.character.base_attributes[this.selected],
                context: `character attribute:${this.selected}`,
                character: this.character.name
            }, (completion) => {
                this.character.base_attributes[this.selected] += completion;
                this.busy = false;
            }, this.$refs.attribute);

        },
        
        handleMessage(message) {
            if (message.type !== 'world_state_manager') {
                return;
            }

            
            if (message.action === 'character_attribute_updated') {
                this.dirty = false;
                this.$emit('require-scene-save');
            }
            else if (message.action === 'operation_done') {
                this.busy = false;
            }
            else if (message.action === 'template_applied' && message.source === this.source){
                
                if(this.templateApplicatorCallback && message.status === 'done') {
                    this.templateApplicatorCallback();
                    this.templateApplicatorCallback = null;
                }

                if(message.result && message.result.character === this.character.name){
                    let attributeName = message.result.attribute;
                    this.character.base_attributes[attributeName] = message.result.value;
                    this.selected = attributeName;
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
