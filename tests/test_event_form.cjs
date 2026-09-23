const test = require('node:test');
const assert = require('node:assert/strict');
const {toFilters} = require('../web/event-parser.js');
const options = {cities:['Алматы','Астана'],categories:['Фотограф','Ведущий'],formats:['свадьба','корпоратив','той','день рождения']};

test('fills editable filters for the requested example and keeps attendance separate', () => {
  const fields = {city:'Астана',date:'2026-10-05',format:'офлайн',category:'фотограф',budget:200000};
  assert.deepEqual(toFilters(fields, 'Нужен фотограф для офлайн-свадьбы в Астане 5 октября, бюджет до 200 000 ₸.', options), {
    city:'Астана',date:'2026-10-05',event_format:'свадьба',category:'Фотограф',budget:200000,attendance_mode:'офлайн',
  });
});
test('null fields clear stale form defaults instead of inventing data', () => {
  const fields = {city:null,date:null,format:null,category:'Фотограф',budget:null};
  assert.deepEqual(toFilters(fields, 'Нужен фотограф', options), {city:'',date:'',event_format:'',category:'Фотограф',budget:'',attendance_mode:''});
});
test('existing event types pass through; ambiguous types stay empty', () => {
  const fields = {city:null,date:null,format:'корпоратив',category:null,budget:null};
  assert.equal(toFilters(fields, 'На корпоратив', options).event_format, 'корпоратив');
  assert.equal(toFilters({...fields,format:'гибрид'}, 'Свадьба или корпоратив, гибрид', options).event_format, '');
  assert.equal(toFilters({...fields,format:'онлайн'}, 'Онлайн, нужен фотограф', options).event_format, '');
});
