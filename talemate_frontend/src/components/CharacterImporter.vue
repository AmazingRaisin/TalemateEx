<template>
    <!-- the card is the dialog's own, so its content scrolls and its buttons stay -->
    <v-dialog v-model="dialog" scrollable max-width="560">
            <v-card>
                <v-card-title>
                    <span class="headline">Import Character</span>
                </v-card-title>
                <v-card-text>
                    <v-autocomplete v-model="sceneInput" :items="scenes"
                        label="Search scenes" outlined @update:search="updateSearchInput" @blur="fetchCharacters"
                        item-title="label" item-value="path" :loading="sceneSearchLoading">
                    </v-autocomplete>
                    <v-select v-model="selectedCharacter" :items="characters" label="Character" outlined></v-select>

                    <!-- what it perceived in that scene comes along (talemate.character_history) -->
                    <v-select
                        v-model="history"
                        :items="historyOptions"
                        item-title="title"
                        item-value="value"
                        label="Bring history"
                        :disabled="!historyAvailable"
                        :messages="historyAvailable ? historyHint : 'Needs character dependent history in this scene (World Editor > Scene > Settings).'"
                    >
                        <template v-slot:item="{ props, item }">
                            <v-list-item v-bind="props" :subtitle="item.raw.subtitle"></v-list-item>
                        </template>
                    </v-select>
                    <v-textarea
                        v-if="history !== 'none'"
                        v-model="historyIntro"
                        class="mt-4"
                        label="Intro"
                        rows="2"
                        auto-grow
                        messages="Optional. Comes between that scene's history and this one's, e.g. 'Weeks later, she arrives in the capital.'"
                    ></v-textarea>
                    <!-- copies of that scene's context database, for it to read (talemate.imported_context) -->
                    <template v-if="history !== 'none'">
                        <v-checkbox
                            v-model="copyContext"
                            class="mt-2"
                            density="compact"
                            label="Copy old context database"
                            messages="The history summaries it perceived there go into this scene's context database, only it can read them."
                        ></v-checkbox>
                        <v-checkbox
                            v-model="copyCharacterInfo"
                            density="compact"
                            label="Bring over old character info"
                            messages="That scene's other characters' info as well (not for characters already in this scene). It reads their public values, unless it could see their private info there."
                        ></v-checkbox>
                    </template>
                    <!-- its lore, and what it doesn't know (talemate.imported_lore) -->
                    <v-checkbox
                        v-model="importLore"
                        class="mt-2"
                        density="compact"
                        label="Import lorebooks / world entries"
                        messages="The world entries and lorebooks it could see there come along. Characters not from that scene don't know them (their lore filter)."
                    ></v-checkbox>
                    <v-checkbox
                        v-model="blockSceneKnowledge"
                        density="compact"
                        label="Block new scene knowledge"
                        messages="It only knows what it brought: this scene's own world entries and lorebooks (also ones added later) and its characters' context database entries are kept from it."
                    ></v-checkbox>
                    <v-alert v-if="error" type="error" variant="tonal" density="compact" class="mt-4">{{ error }}</v-alert>
                </v-card-text>
                <v-card-actions>
                    <v-spacer></v-spacer>
                    <v-btn color="secondary" text @click="dialog = false" :disabled="importing">Close</v-btn>
                    <v-btn color="primary" text @click="importCharacter" :disabled="importing || !selectedCharacter">Import</v-btn>
                    <v-progress-circular v-if="importing" indeterminate="disable-shrink" color="primary" size="20"></v-progress-circular>

                </v-card-actions>
            </v-card>
    </v-dialog>
</template>

<script>
export default {
    components: {
    },
    name: 'CharacterImporter',
    data() {
        return {
            sceneSearchInput: null,
            sceneSearchLoading: false,
            sceneInput: "",
            scenes: [],
            characters: [],
            dialog: false,
            selectedScene: null,
            selectedCharacter: null,
            importing: false,
            error: null,
            // 'none' / 'clean' / 'unclean' (talemate.character_history)
            history: 'none',
            historyIntro: '',
            historyAvailable: false,
            copyContext: false,
            copyCharacterInfo: false,
            importLore: false,
            blockSceneKnowledge: false,
            historyOptions: [
                { value: 'none', title: 'None', subtitle: 'Only the character, as before.' },
                { value: 'clean', title: 'Clean', subtitle: 'Its history comes along and is condensed further over time, as this scene grows.' },
                { value: 'unclean', title: 'Unclean', subtitle: 'Its history comes along as it was and is never summarized again, giving way as this scene grows.' },
            ],
        }
    },
    computed: {
        historyHint() {
            if (this.history === 'none') {
                return 'What the character perceived in that scene can come along, before this scene\'s history in its own prompts.';
            }
            return 'What the character perceived in that scene goes before this scene\'s history, in its own prompts only.';
        },
    },
    watch: {
        sceneInput(val) {
            this.selectedScene = val;
        },
        historyAvailable(available) {
            if (!available) {
                this.history = 'none';
            }
        },
    },
    emits:[
        'import-done',
    ],
    inject: ['getWebsocket', 'registerMessageHandler', 'setWaitingForInput', 'requestSceneAssets'],
    methods: {
        show() {
            this.error = null;
            this.history = 'none';
            this.historyIntro = '';
            this.copyContext = false;
            this.copyCharacterInfo = false;
            this.importLore = false;
            this.blockSceneKnowledge = false;
            this.dialog = true;
        },
        exit() {
            this.dialog = false
        },
        updateSearchInput(val) {
            this.sceneSearchInput = val;
            clearTimeout(this.searchTimeout); // Clear the previous timeout
            this.searchTimeout = setTimeout(this.fetchScenes, 300); // Start a new timeout
        },
        sendRequest(data) {
            data.type = 'character_importer';
            this.getWebsocket().send(JSON.stringify(data));
        },

        importCharacter() {
            this.importing = true;
            this.error = null;
            this.sendRequest({
                action: 'import',
                scene_path: this.selectedScene,
                character_name: this.selectedCharacter,
                history: this.historyAvailable ? this.history : 'none',
                history_intro: this.history !== 'none' ? this.historyIntro : '',
                copy_context: this.history !== 'none' && this.copyContext,
                copy_character_info: this.history !== 'none' && this.copyCharacterInfo,
                import_lore: this.importLore,
                block_scene_knowledge: this.blockSceneKnowledge,
            })
        },

        handleMessage(data) {

            if (data.type === 'scenes_list') {
                this.scenes = data.data;
                this.sceneSearchLoading = false;
                return;
            }

            if (data.type === 'character_importer') {

                if(data.action === 'list_characters') {
                    this.characters = data.characters;
                    this.historyAvailable = data.history_available === true;
                } else if(data.action === 'import_character_done') {
                    this.importing = false;
                    this.dialog = false;
                    this.$emit('import-done', data.character);
                } else if(data.action === 'import_character_failed') {
                    this.importing = false;
                    this.error = data.error;
                }

            }

        },
        fetchScenes() {
            if (!this.sceneSearchInput)
                return
            this.sceneSearchLoading = true;
            this.getWebsocket().send(JSON.stringify({ type: 'request_scenes_list', query: this.sceneSearchInput, list_images: false }));
        },
        fetchCharacters() {
            if (!this.selectedScene)
                return
            this.sendRequest({
                action: 'list_characters',
                scene_path: this.selectedScene,
            })
        },
    },
    created() {
        this.registerMessageHandler(this.handleMessage);
    },
}
</script>

<style scoped></style>
