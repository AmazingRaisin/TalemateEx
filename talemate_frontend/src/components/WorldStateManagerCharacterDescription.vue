<template>

    <ContextualGenerate 
        ref="contextualGenerate"
        uid="wsm.character_description"
        :context="'character detail:description'" 
        :original="character.description"
        :character="character.name"
        :generationOptions="generationOptions"
        :templates="templates"
        :specifyLength="true"
        @generate="content => setAndUpdate(content)"
    />

    <div class="d-flex flex-wrap align-center mb-3 privacy-toggles">
        <v-btn-toggle
            v-model="character.description_private"
            mandatory
            density="compact"
            color="primary"
            class="mr-2"
            @update:model-value="setPrivacy($event)"
        >
            <v-btn :value="false" prepend-icon="mdi-earth">Public</v-btn>
            <v-btn :value="true" prepend-icon="mdi-lock">Private</v-btn>
        </v-btn-toggle>
        <!-- what only the character knows of itself (either way) -->
        <v-btn-toggle
            :model-value="character.description_self ? true : null"
            density="compact"
            color="primary"
            class="self-toggle"
            @update:model-value="setSelf(!!$event)"
        >
            <v-btn :value="true" prepend-icon="mdi-account-eye">Self</v-btn>
        </v-btn-toggle>
    </div>

    <v-textarea ref="description" rows="5" auto-grow v-model="character.description"
        :color="dirty ? 'dirty' : ''"

        :disabled="busy"
        :loading="busy"
        @keyup.ctrl.enter.stop="sendAutocompleteRequest"

        @update:model-value="dirty = true"
        @blur="update(true)"
        label="Description"
        :hint="'A short description of the character. '+autocompleteInfoMessage(busy)">
    </v-textarea>

    <v-textarea v-if="character.description_private"
        rows="4" auto-grow
        v-model="character.public_description"
        :color="dirty ? 'dirty' : ''"
        :disabled="busy"
        @update:model-value="dirty = true"
        @blur="update(true)"
        label="Public Description"
        messages="Shown in prompts for other characters.">
    </v-textarea>

    <v-textarea v-if="character.description_self"
        rows="4" auto-grow
        class="self-value"
        v-model="character.self_description"
        :color="dirty ? 'dirty' : ''"
        :disabled="busy"
        @update:model-value="dirty = true"
        @blur="update(true)"
        label="Self Description"
        :messages="`Only in ${character.name}'s own prompts, in place of the other description. Character progression updates this one.`">
    </v-textarea>

    <WorldStateManagerCharacterPrivateViewers
        v-model="character.description_private_viewers"
        section="description"
        :character-name="character.name"
        :character-names="character.available_character_names"
        @require-scene-save="$emit('require-scene-save')"
    />

    <SpiceAppliedNotification :uids="['wsm.character_description']"></SpiceAppliedNotification>

</template>
<script>

import ContextualGenerate from './ContextualGenerate.vue';
import SpiceAppliedNotification from './SpiceAppliedNotification.vue';
import WorldStateManagerCharacterPrivateViewers from './WorldStateManagerCharacterPrivateViewers.vue';

export default {
    name: 'WorldStateManagerCharacterDescription',
    components: {
        ContextualGenerate,
        SpiceAppliedNotification,
        WorldStateManagerCharacterPrivateViewers,
    },
    props: {
        immutableCharacter: Object,
        templates: Object,
        generationOptions: Object,
    },
    inject: [
        'getWebsocket',
        'autocompleteInfoMessage',
        'autocompleteRequest',
        'registerMessageHandler',
        'unregisterMessageHandler',
    ],
    emits:[
        'require-scene-save'
    ],
    data() {
        return {
            character: {},
            dirty: false,
            busy: false,
            updateTimeout: null,
            spiceApplied: false,
            spiceAppliedDetail: null, 
        }
    },
    watch: {
        immutableCharacter: {
            immediate: true,
            handler(value) {
                if (!value) {
                    this.character = null;
                } else {
                    this.character = {
                        ...value,
                        description_private: value.description_private || false,
                        public_description: value.public_description || '',
                        description_self: value.description_self || false,
                        self_description: value.self_description || '',
                        description_private_viewers: [...(value.description_private_viewers || [])],
                    };
                }
            }
        }
    },
    methods: {
        update(only_if_dirty = false) {

            if(only_if_dirty && !this.dirty) {
                return;
            }

            this.getWebsocket().send(JSON.stringify({
                type: 'world_state_manager',
                action: 'update_character_description',
                name: this.character.name,
                attribute: 'description',
                value: this.character.description,
                private: this.character.description_private || false,
                public_value: this.character.public_description || '',
                self_enabled: this.character.description_self || false,
                self_value: this.character.self_description || '',
            }));
        },

        setSelf(enabled) {
            // it starts as the description (private or public, whichever it is)
            if (enabled && !this.character.self_description) {
                this.character.self_description = this.character.description || '';
            }
            this.character.description_self = enabled;
            this.dirty = true;
            this.update();
        },

        setPrivacy(isPrivate) {
            this.character.description_private = isPrivate;
            this.dirty = true;
            this.update();
        },

        setAndUpdate(value) {
            this.character.description = value;
            this.update();
        },

        sendAutocompleteRequest() {
            this.busy = true;
            this.autocompleteRequest({
                partial: this.character.description,
                context: `character detail:description`,
                character: this.character.name
            }, (completion) => {
                this.character.description += completion;
                this.busy = false;
            }, this.$refs.description);
        },

        handleMessage(message) {
            if (message.type !== 'world_state_manager') {
                return;
            }
            else if (message.action === 'character_description_updated') {
                this.dirty = false;
                this.$emit('require-scene-save');
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
