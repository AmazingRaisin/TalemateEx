<template>
    <v-menu v-model="menuOpen">
        <template v-slot:activator="{ props }">
            <v-btn class="hotkey mx-1" v-bind="props" :disabled="disabled" color="primary" icon variant="text" title="Control Scene">
                <v-icon>mdi-movie-open-cog</v-icon>
            </v-btn>
        </template>
        <v-list density="compact">
            <v-list-subheader>Control Scene</v-list-subheader>
            <v-list-item density="compact" prepend-icon="mdi-door-open" @click="openMoveCharacters">
                <v-list-item-title>Move Characters</v-list-item-title>
                <v-list-item-subtitle>Send characters to another room</v-list-item-subtitle>
            </v-list-item>
            <v-list-item density="compact" prepend-icon="mdi-account-group" @click="openGroupCharacters">
                <v-list-item-title>Group Characters</v-list-item-title>
                <v-list-item-subtitle>Characters taking one turn together</v-list-item-subtitle>
            </v-list-item>
            <v-list-item density="compact" prepend-icon="mdi-update" @click="openAdvanceScene">
                <v-list-item-title>Advance Scene</v-list-item-title>
                <v-list-item-subtitle>Rewrite scene info for where the story is now</v-list-item-subtitle>
            </v-list-item>
            <v-menu v-if="rooms.length > 1" submenu open-on-hover location="end">
                <template v-slot:activator="{ props }">
                    <v-list-item v-bind="props" density="compact" prepend-icon="mdi-bullhorn-variant-outline" append-icon="mdi-menu-right">
                        <v-list-item-title>Room Narrator Focus</v-list-item-title>
                        <v-list-item-subtitle>{{ narratorFocusLabel }}</v-list-item-subtitle>
                    </v-list-item>
                </template>
                <v-list density="compact" class="focus-list">
                    <v-list-item density="compact" @click="setNarratorRoom(null)">
                        <template v-slot:prepend>
                            <v-icon size="small">{{ !narratorRoom ? 'mdi-radiobox-marked' : 'mdi-radiobox-blank' }}</v-icon>
                        </template>
                        <v-list-item-title>Follow player character</v-list-item-title>
                        <v-list-item-subtitle>Currently {{ roomName(playerRoom) }}</v-list-item-subtitle>
                    </v-list-item>
                    <v-divider></v-divider>
                    <v-list-item v-for="room in rooms" :key="'narrator-' + room.id" density="compact" @click="setNarratorRoom(room.id)">
                        <template v-slot:prepend>
                            <v-icon size="small">{{ narratorRoom === room.id ? 'mdi-radiobox-marked' : 'mdi-radiobox-blank' }}</v-icon>
                        </template>
                        <v-list-item-title>{{ room.name }}</v-list-item-title>
                    </v-list-item>
                </v-list>
            </v-menu>
        </v-list>
    </v-menu>

    <SceneGroupCharacters v-model="groupDialog" :scene="scene" :locked="disabled" />

    <SceneAdvance v-model="advanceDialog" :busy="disabled" />

    <v-dialog v-model="moveDialog" max-width="760">
        <v-card>
            <v-card-title>
                <v-icon class="mr-2" size="small" color="primary">mdi-door-open</v-icon>
                Move Characters
            </v-card-title>
            <v-card-text class="pb-0">
                <v-row>
                    <v-col cols="12" sm="7">
                        <div class="text-caption text-muted mb-1">Characters</div>
                        <v-list density="compact" class="move-list" select-strategy="leaf">
                            <v-list-item
                                v-for="character in characters"
                                :key="character.name"
                                :active="selectedCharacters.includes(character.name)"
                                color="primary"
                                @click="toggleCharacter(character.name)"
                            >
                                <template v-slot:prepend>
                                    <v-checkbox-btn :model-value="selectedCharacters.includes(character.name)" density="compact" @click.stop="toggleCharacter(character.name)"></v-checkbox-btn>
                                </template>
                                <v-list-item-title>
                                    {{ character.name }}
                                    <span v-if="character.is_player" class="text-muted text-caption">(you)</span>
                                    <v-tooltip v-if="character.location_shown_always" location="top" text="Location shown always: moves are always announced">
                                        <template v-slot:activator="{ props }">
                                            <v-icon v-bind="props" size="small" color="highlight4" class="ml-1">mdi-broadcast</v-icon>
                                        </template>
                                    </v-tooltip>
                                </v-list-item-title>
                                <template v-slot:append>
                                    <span class="text-caption text-muted">{{ roomName(character.room) }}</span>
                                </template>
                            </v-list-item>
                        </v-list>
                    </v-col>
                    <v-col cols="12" sm="5">
                        <div class="text-caption text-muted mb-1">Move to</div>
                        <v-list density="compact" class="move-list">
                            <v-list-item
                                v-for="room in rooms"
                                :key="room.id"
                                :active="targetRoom === room.id"
                                color="primary"
                                @click="targetRoom = room.id"
                            >
                                <template v-slot:prepend>
                                    <v-icon size="small">{{ targetRoom === room.id ? 'mdi-radiobox-marked' : 'mdi-radiobox-blank' }}</v-icon>
                                </template>
                                <v-list-item-title>{{ room.name }}</v-list-item-title>
                            </v-list-item>
                        </v-list>
                    </v-col>
                </v-row>
                <div v-if="rooms.length < 2" class="text-caption text-muted mt-2">
                    Add rooms in the World Editor under Scene &gt; Rooms.
                </div>
            </v-card-text>
            <v-card-actions class="flex-wrap">
                <v-btn variant="text" color="cancel" prepend-icon="mdi-cancel" @click="moveDialog = false">Cancel</v-btn>
                <v-spacer></v-spacer>
                <v-checkbox v-model="announceDestination" density="compact" hide-details label="Announce destination" class="mr-2"></v-checkbox>
                <v-checkbox v-model="announceArrival" density="compact" hide-details label="Announce arrival" class="mr-2"></v-checkbox>
                <v-btn variant="text" color="primary" prepend-icon="mdi-door-open" :disabled="!canMove" @click="submit">Move</v-btn>
            </v-card-actions>
        </v-card>
    </v-dialog>
</template>

<script>
import { activeRooms, characterRoom, roomName } from '@/utils/rooms';
import SceneGroupCharacters from './SceneGroupCharacters.vue';
import SceneAdvance from './SceneAdvance.vue';

export default {
    name: 'SceneToolsControl',
    components: {
        SceneGroupCharacters,
        SceneAdvance,
    },
    props: {
        disabled: Boolean,
        scene: Object,
    },
    inject: ['getWebsocket'],
    data() {
        return {
            menuOpen: false,
            moveDialog: false,
            groupDialog: false,
            advanceDialog: false,
            selectedCharacters: [],
            targetRoom: null,
            announceDestination: true,
            announceArrival: true,
        };
    },
    computed: {
        sceneData() {
            return (this.scene && this.scene.data) || {};
        },
        rooms() {
            return activeRooms(this.sceneData);
        },
        characters() {
            return (this.sceneData.characters || []).map(character => ({
                name: character.name,
                is_player: character.is_player,
                location_shown_always: character.location_shown_always,
                room: characterRoom(this.sceneData, character.name),
            }));
        },
        canMove() {
            return !this.disabled && this.targetRoom && this.selectedCharacters.length > 0;
        },
        // the room the narrator was pointed at, null: follows the player character
        narratorRoom() {
            const roomId = this.sceneData.narrator_room;
            return this.rooms.find(room => room.id === roomId) ? roomId : null;
        },
        narratorFocusLabel() {
            return this.narratorRoom
                ? this.roomName(this.narratorRoom)
                : 'Following player character';
        },
        playerRoom() {
            const player = (this.sceneData.characters || []).find(c => c.is_player);
            return player ? characterRoom(this.sceneData, player.name) : 'main';
        },
    },
    methods: {
        roomName(roomId) {
            return roomName(this.sceneData, roomId);
        },
        setNarratorRoom(roomId) {
            this.menuOpen = false;
            this.getWebsocket().send(JSON.stringify({
                type: 'world_state_manager',
                action: 'set_narrator_room',
                room_id: roomId,
            }));
        },
        openGroupCharacters() {
            this.menuOpen = false;
            this.groupDialog = true;
        },
        openAdvanceScene() {
            this.menuOpen = false;
            this.advanceDialog = true;
        },
        openMoveCharacters() {
            this.selectedCharacters = [];
            this.targetRoom = null;
            this.announceDestination = true;
            this.announceArrival = true;
            this.moveDialog = true;
        },
        toggleCharacter(name) {
            const index = this.selectedCharacters.indexOf(name);
            if (index >= 0) {
                this.selectedCharacters.splice(index, 1);
            } else {
                this.selectedCharacters.push(name);
            }
        },
        submit() {
            if (!this.canMove) {
                return;
            }
            this.getWebsocket().send(JSON.stringify({
                type: 'world_state_manager',
                action: 'move_characters',
                characters: this.selectedCharacters,
                room_id: this.targetRoom,
                announce_destination: this.announceDestination,
                announce_arrival: this.announceArrival,
            }));
            this.moveDialog = false;
        },
    },
};
</script>

<style scoped>
.move-list {
    max-height: 320px;
    overflow-y: auto;
}
.focus-list {
    max-height: 360px;
    overflow-y: auto;
}
</style>
