<template>
    <v-divider class="mt-5 mb-4"></v-divider>
    <v-select
        :model-value="modelValue"
        :items="otherCharacterNames"
        :label="label"
        multiple
        chips
        closable-chips
        clearable
        prepend-inner-icon="mdi-account-key"
        messages="Selected characters receive this character's private information in their prompts."
        @update:model-value="update"
    ></v-select>
</template>

<script>
export default {
    name: 'WorldStateManagerCharacterPrivateViewers',
    props: {
        characterName: String,
        characterNames: {
            type: Array,
            default: () => [],
        },
        modelValue: {
            type: Array,
            default: () => [],
        },
        section: String,
    },
    emits: [
        'update:modelValue',
        'require-scene-save',
    ],
    inject: [
        'getWebsocket',
    ],
    computed: {
        otherCharacterNames() {
            return this.characterNames.filter(name => name !== this.characterName);
        },
        label() {
            const labels = {
                description: 'Private description visible to',
                attributes: 'Private attributes visible to',
                states: 'Private states visible to',
            };
            return labels[this.section] || 'Private information visible to';
        },
    },
    methods: {
        update(viewers) {
            const normalizedViewers = viewers || [];
            this.$emit('update:modelValue', normalizedViewers);
            this.getWebsocket().send(JSON.stringify({
                type: 'world_state_manager',
                action: 'update_character_private_viewers',
                name: this.characterName,
                section: this.section,
                viewers: normalizedViewers,
            }));
            this.$emit('require-scene-save');
        },
    },
}
</script>
