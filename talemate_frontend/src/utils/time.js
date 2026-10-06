/**
 * Units of a multi-unit duration, largest first.
 */
export const DURATION_UNITS = ['years', 'months', 'weeks', 'days', 'hours', 'minutes'];

export function emptyDurationComponents() {
    return { years: 0, months: 0, weeks: 0, days: 0, hours: 0, minutes: 0 };
}

/**
 * Parse an ISO-8601 duration string into {years, months, weeks, days, hours, minutes}.
 * Days are split into weeks and days. Returns null if the string can't be parsed.
 */
export function isoDurationToComponents(iso) {
    const match = (iso || '').match(/^P(?:(\d+)Y)?(?:(\d+)M)?(?:(\d+)W)?(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?)?$/);
    if (!match) return null;

    const totalDays = parseInt(match[3] || 0) * 7 + parseInt(match[4] || 0);

    return normalizeDurationComponents({
        years: parseInt(match[1] || 0),
        months: parseInt(match[2] || 0),
        weeks: 0,
        days: totalDays,
        hours: parseInt(match[5] || 0),
        minutes: parseInt(match[6] || 0),
    });
}

/**
 * Carries minutes into hours, hours into days and days into weeks, the same
 * way the backend does when it labels a time passage. Years and months are
 * kept as entered.
 */
export function normalizeDurationComponents(components) {
    const value = (unit) => Math.max(0, parseInt(components[unit]) || 0);

    let minutes = value('minutes');
    let hours = value('hours') + Math.floor(minutes / 60);
    minutes %= 60;
    let days = value('weeks') * 7 + value('days') + Math.floor(hours / 24);
    hours %= 24;
    const weeks = Math.floor(days / 7);
    days %= 7;

    return { years: value('years'), months: value('months'), weeks, days, hours, minutes };
}

/**
 * Build an ISO-8601 duration from duration components. Weeks are folded into
 * days, since ISO-8601 doesn't allow weeks alongside other units.
 * Returns null for a zero duration.
 */
export function componentsToIsoDuration(components) {
    const c = normalizeDurationComponents(components);
    const days = c.weeks * 7 + c.days;

    let date = '';
    if (c.years) date += `${c.years}Y`;
    if (c.months) date += `${c.months}M`;
    if (days) date += `${days}D`;

    let time = '';
    if (c.hours) time += `${c.hours}H`;
    if (c.minutes) time += `${c.minutes}M`;

    if (!date && !time) return null;

    return `P${date}${time ? 'T' + time : ''}`;
}

/**
 * Human label for a time passage, e.g. "2 Years, 3 Weeks and 2 Hours later".
 * Mirrors the backend's time passage labels.
 */
export function durationComponentsToHuman(components, suffix = ' later') {
    const c = normalizeDurationComponents(components);
    const labels = { years: 'Year', months: 'Month', weeks: 'Week', days: 'Day', hours: 'Hour', minutes: 'Minute' };
    const parts = DURATION_UNITS
        .filter((unit) => c[unit])
        .map((unit) => `${c[unit]} ${labels[unit]}${c[unit] > 1 ? 's' : ''}`);

    if (!parts.length) return '';
    if (parts.length === 1) return parts[0] + suffix;

    const last = parts.pop();
    return `${parts.join(', ')} and ${last}${suffix}`;
}
