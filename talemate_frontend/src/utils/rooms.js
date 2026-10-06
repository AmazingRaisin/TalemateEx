// Rooms (talemate.rooms): places characters can be in.

export const MAIN_ROOM_ID = 'main';

export function sceneRooms(sceneData) {
    return (sceneData && sceneData.rooms) || [];
}

export function activeRooms(sceneData) {
    return sceneRooms(sceneData).filter(room => !room.deleted);
}

export function roomById(sceneData, roomId) {
    return sceneRooms(sceneData).find(room => room.id === roomId) || null;
}

export function roomName(sceneData, roomId) {
    const room = roomById(sceneData, roomId);
    return room ? room.name : (roomId || 'Main Room');
}

// Chat badge text for a room, none for the main room.
export function roomBadge(sceneData, roomId) {
    if (!roomId || roomId === MAIN_ROOM_ID) {
        return null;
    }
    const room = roomById(sceneData, roomId);
    if (!room) {
        return null;
    }
    return (room.label || '').trim() || room.name;
}

// Chat tint for a room (css color, may carry alpha), none if not set.
export function roomTint(sceneData, roomId) {
    if (!roomId) {
        return null;
    }
    const room = roomById(sceneData, roomId);
    if (!room || !room.color) {
        return null;
    }
    return room.color;
}

// The room an active character is in.
export function characterRoom(sceneData, name) {
    const characters = (sceneData && sceneData.characters) || [];
    const character = characters.find(c => c.name === name);
    const roomId = (character && character.room) || MAIN_ROOM_ID;
    const room = roomById(sceneData, roomId);
    return room && !room.deleted ? roomId : MAIN_ROOM_ID;
}

// Whether the scene uses more than the main room.
export function roomsShown(sceneData) {
    if (activeRooms(sceneData).length > 1) {
        return true;
    }
    const characters = (sceneData && sceneData.characters) || [];
    return characters.some(c => characterRoom(sceneData, c.name) !== MAIN_ROOM_ID);
}
