// Character groups (talemate.groups): characters speaking and acting through
// one turn together.

// The scene's groups, with who takes part in them now
// ({id, color, members, present, speakers, room, takes_turns, ...}).
export function sceneGroups(sceneData) {
    return (sceneData && sceneData.character_groups) || [];
}

// 'Frieren', 'Frieren and Fern', 'Frieren, Fern, and Stark'
export function groupLabel(names) {
    names = names || [];
    if (names.length <= 2) {
        return names.join(' and ');
    }
    return names.slice(0, -1).join(', ') + ', and ' + names[names.length - 1];
}

// The groups a character is in.
export function groupsOf(sceneData, name) {
    return sceneGroups(sceneData).filter(group => group.members.includes(name));
}

// Groups for picking who a private part is for: a shortcut to their members
// that are in the list (at least two of them).
export function pickableGroups(sceneData, characters) {
    const names = (characters || []).map(c => c.name);
    return sceneGroups(sceneData)
        .map(group => ({
            id: group.id,
            color: group.color,
            members: group.members.filter(name => names.includes(name)),
        }))
        .filter(group => group.members.length >= 2);
}
