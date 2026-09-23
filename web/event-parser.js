'use strict';
// Pure adapter: AI fields -> existing controls. No request or recommendation side effects.
const EventRequestForm = (() => {
  const modes = ['офлайн', 'онлайн', 'гибрид'];
  const normalize = value => typeof value === 'string' ? value.trim().toLowerCase().replaceAll('ё', 'е') : '';
  function eventType(text, formats) {
    const patterns = [
      ['свадьба', /свадьб|свадеб/iu], ['корпоратив', /корпоратив/iu],
      ['конференция', /конференци/iu], ['юбилей', /юбиле/iu],
      ['день рождения', /д(?:ень|ня|ню|нем|не)\s+рождени/iu],
      ['той', /(?:^|[^а-яё])то(?:й|я|ю|ем|е)(?=$|[^а-яё])/iu],
    ];
    const matches = patterns.filter(([value, pattern]) => formats.includes(value) && pattern.test(text));
    return matches.length === 1 ? matches[0][0] : '';
  }
  function toFilters(fields, text, options) {
    const select = (value, allowed) => allowed.find(x => normalize(x) === normalize(value)) || '';
    const mode = modes.includes(fields.format) ? fields.format : '';
    return {
      city: select(fields.city, options.cities),
      date: fields.date ?? '',
      category: select(fields.category, options.categories),
      budget: fields.budget ?? '',
      event_format: mode ? eventType(text, options.formats) : select(fields.format, options.formats),
      attendance_mode: mode,
    };
  }
  return {toFilters};
})();
if (typeof module !== 'undefined' && module.exports) module.exports = EventRequestForm;
