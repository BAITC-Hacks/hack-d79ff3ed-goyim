const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const {setImmediate: nextTurn} = require('node:timers/promises');

const web = path.join(__dirname, '../web');
const metadata = {
  cities:['Алматы','Астана'], categories:['Ведущий','Фотограф'], formats:['корпоратив','свадьба'],
  languages:['Русский'], calendar_start:'2026-09-23', calendar_end:'2026-12-31', total:66, synthetic:13,
};
const fields = {city:'Астана',date:'2026-10-05',format:'офлайн',category:'фотограф',budget:200000};
const description = 'Нужен фотограф для офлайн-свадьбы в Астане 5 октября, бюджет до 200 000 ₸.';
const unavailable = 'AI-помощник сейчас недоступен. Заполните параметры вручную.';

// Minimal DOM for exercising the real app event handlers without extra packages.
class Element {
  constructor(text = '') { this.textContent = text; this.children = []; this.dataset = {}; this.listeners = {}; this.value = ''; }
  set value(value) { this._value = String(value); }
  get value() { return this._value; }
  append(...children) { this.children.push(...children); }
  replaceChildren(...children) { this.children = children; }
  get childElementCount() { return this.children.length; }
  setAttribute(name, value) { this[name] = value; }
  addEventListener(name, listener) { this.listeners[name] = listener; }
  scrollIntoView() {}
  focus() { this.focused = true; }
}

function recommendation(query) {
  return {query, status:'matched', message:'Найден один вариант.', elapsed_ms:1,
    cards:[{id:'sample',rank:1,anon_name:'Тестовый профиль',categories:[query.category],city:query.city,
      price_from_kzt:100000,languages:['Русский'],max_hours:null,budget_margin:query.budget-100000,
      explanation:'Тестовые данные.',description:'Тестовое описание.',relevance:0.2543,
      parameter_match:1,matched_parameters:7,total_parameters:7}],
    counts:{in_category:1,eligible:1},rejections:[],alternative_dates:[],
    explanation_mode:'local',ai_notice:null,busy_profile_ids:[]};
}

async function app(parseResponse = async () => ({ok:true,json:async () => ({fields})}), recommendResponse = async query => ({ok:true,json:async () => recommendation(query)})) {
  const elements = Object.fromEntries([...fs.readFileSync(path.join(web, 'index.html'), 'utf8').matchAll(/id="([^"]+)"/g)].map(([,id]) => [id,new Element()]));
  elements.date.value = '2026-10-15';
  elements.budget.value = '1000000';
  elements['event-request'].value = description;
  elements['search-form'].reportValidity = () => ['city','date','category','event_format','budget'].every(key => elements[key].value !== '') && Number(elements.budget.value) > 0;
  const calls = [];
  const resultsPanel = new Element();
  const context = vm.createContext({
    console, AbortController, setTimeout, clearTimeout,
    document:{getElementById:id => elements[id],createElement:() => new Element(),createTextNode:text => new Element(text),
      querySelector:() => resultsPanel,querySelectorAll:() => []},
    window:{innerWidth:1200},
    fetch:async (url, options) => {
      if (url === '/api/catalog') return {ok:true,json:async () => metadata};
      const body = JSON.parse(options.body);
      calls.push({url,body});
      if (url === '/api/parse-event') return parseResponse();
      return recommendResponse(body);
    },
  });
  vm.runInContext(fs.readFileSync(path.join(web, 'event-parser.js'), 'utf8'), context);
  vm.runInContext(fs.readFileSync(path.join(web, 'app.js'), 'utf8'), context);
  await nextTurn(); // Wait for the existing initial demo search.
  calls.length = 0;
  return {elements,calls,context,clickAI:() => elements['parse-event'].listeners.click()};
}

test('one AI click fills the actual form and sends exactly one search with the extracted parameters', async () => {
  const {elements,calls,clickAI} = await app();
  await clickAI();
  assert.deepEqual(calls.map(call => call.url), ['/api/parse-event','/api/recommend']);
  assert.deepEqual(calls[1].body, {city:'Астана',date:'2026-10-05',category:'Фотограф',event_format:'свадьба',budget:200000,language:null,hours:null,brief:''});
  assert.equal(elements.attendance_mode.value, 'офлайн');
  assert.equal(elements['parse-event'].disabled, false);
  assert.equal(elements['ai-parser']['aria-busy'], 'false');
});

test('budget recovery shows the actual difference and sends one request preserving all other filters', async () => {
  let release;
  let hold = false;
  const {elements,calls,context} = await app(undefined, async query => {
    if (hold) await new Promise(resolve => { release = resolve; });
    return {ok:true,json:async () => ({...recommendation(query),status:'no_matches',cards:[],suggested_min_budget:1030000})};
  });
  const option = elements.alternatives.children[0];
  assert.match(option.children[0].textContent, /30\s000 ₸ — до 1\s030\s000 ₸/);
  const button = option.children[1];
  const before = JSON.parse(vm.runInContext('JSON.stringify(readQuery())', context));
  elements.attendance_mode.value = 'офлайн';
  hold = true;
  const pending = button.listeners.click();
  await button.listeners.click();
  assert.equal(button.disabled, true);
  assert.equal(button.textContent, 'Проверяем условия…');
  assert.equal(calls.length, 1);
  assert.deepEqual(calls[0].body, {...before,budget:1030000});
  assert.equal(elements.attendance_mode.value, 'офлайн');
  release();
  await pending;
});

test('date recovery preserves optional filters and uses the selected date', async () => {
  const {elements,calls,context} = await app();
  vm.runInContext(`setQuery({...readQuery(),language:'Русский',hours:6,brief:'Спокойная подача'});
    render({...previous,query:readQuery(),status:'no_matches',cards:[],alternative_dates:[{date:'2026-10-16',count:2}]});`, context);
  const before = JSON.parse(vm.runInContext('JSON.stringify(readQuery())', context));
  const option = elements.alternatives.children[0];
  assert.match(option.children[0].textContent, /подходят 2 подрядчика/);
  await option.children[1].listeners.click();
  assert.deepEqual(calls[0].body, {...before,date:'2026-10-16'});
});

test('empty recovery and absent category remain distinct and never invent alternatives', async () => {
  const {elements,context} = await app();
  vm.runInContext(`render({...previous,status:'no_matches',cards:[],suggested_min_budget:null,alternative_dates:[]})`, context);
  assert.equal(elements.alternatives.hidden, true);
  assert.equal(elements['empty-title'].textContent, 'По вашим условиям подрядчиков не найдено');
  assert.match(elements['empty-text'].textContent, /Проверенных альтернатив/);
  vm.runInContext(`render({...previous,status:'no_category'})`, context);
  assert.equal(elements.alternatives.hidden, true);
  assert.match(elements['empty-text'].textContent, /нет этой категории/);
});

test('recovery does not overwrite filters edited after the result', async () => {
  const {elements,calls,context} = await app();
  vm.runInContext(`render({...previous,status:'no_matches',cards:[],suggested_min_budget:1030000})`, context);
  elements.city.value = 'Астана';
  await elements.alternatives.children[0].children[1].listeners.click();
  assert.equal(calls.length, 0);
  assert.equal(elements.city.value, 'Астана');
  assert.match(elements['form-error'].textContent, /Параметры изменились/);
});

test('partial extraction asks for missing fields without searching with old defaults', async () => {
  const {elements,calls,clickAI} = await app(async () => ({ok:true,json:async () => ({fields:{city:null,date:null,format:null,category:'фотограф',budget:null}})}));
  await clickAI();
  assert.deepEqual(calls.map(call => call.url), ['/api/parse-event']);
  assert.equal(elements.city.value, '');
  assert.equal(elements.budget.value, '');
  assert.match(elements['parser-message'].textContent, /Дополните: город, дату, тип мероприятия, бюджет/);
});

test('invalid remaining form fields prevent automatic search', async () => {
  const {elements,calls,clickAI} = await app();
  elements['search-form'].reportValidity = () => false;
  await clickAI();
  assert.deepEqual(calls.map(call => call.url), ['/api/parse-event']);
  assert.match(elements['parser-message'].textContent, /Проверьте отмеченные поля/);
});

test('API failure preserves the form and manual search remains available', async () => {
  const {elements,calls,clickAI} = await app(async () => ({ok:false,json:async () => ({error:unavailable})}));
  await clickAI();
  assert.deepEqual(calls.map(call => call.url), ['/api/parse-event']);
  assert.equal(elements.city.value, 'Алматы');
  assert.equal(elements['parser-message'].textContent, unavailable);
  elements['search-form'].listeners.submit({preventDefault(){}});
  await nextTurn();
  assert.equal(calls[1].url, '/api/recommend');
  assert.equal(calls[1].body.city, 'Алматы');
});

test('malformed API response never starts a search', async () => {
  const {elements,calls,clickAI} = await app(async () => ({ok:true,json:async () => {throw new SyntaxError('Invalid JSON');}}));
  await clickAI();
  assert.deepEqual(calls.map(call => call.url), ['/api/parse-event']);
  assert.equal(elements['parser-message'].textContent, unavailable);
});

test('editing during AI processing prevents stale fill and automatic search', async () => {
  let finish;
  const response = new Promise(resolve => { finish = resolve; });
  const {elements,calls,clickAI} = await app(() => response);
  const pending = clickAI();
  elements.budget.value = '300000';
  elements['search-form'].listeners.input();
  finish({ok:true,json:async () => ({fields})});
  await pending;
  assert.deepEqual(calls.map(call => call.url), ['/api/parse-event']);
  assert.equal(elements.budget.value, '300000');
  assert.match(elements['parser-message'].textContent, /Ответ AI не применён/);
});

test('repeated AI clicks while processing do not create duplicate searches', async () => {
  let finish;
  const response = new Promise(resolve => { finish = resolve; });
  const {calls,clickAI} = await app(() => response);
  const first = clickAI();
  await clickAI();
  finish({ok:true,json:async () => ({fields})});
  await first;
  assert.deepEqual(calls.map(call => call.url), ['/api/parse-event','/api/recommend']);
});

test('manual edits after AI results are used for the next manual search', async () => {
  const {elements,calls,clickAI} = await app();
  await clickAI();
  elements.budget.value = '350000';
  elements['search-form'].listeners.input();
  elements['search-form'].listeners.submit({preventDefault(){}});
  await nextTurn();
  assert.equal(calls.at(-1).url, '/api/recommend');
  assert.equal(calls.at(-1).body.budget, 350000);
});

test('cards show verified parameter match instead of lexical relevance', async () => {
  const {elements,context} = await app();
  const card = elements.cards.children[0];
  const match = card.children.find(child => child.className === 'match-summary');
  assert.match(match.children[0].textContent, /Соответствие параметрам: 100\s*%/);
  assert.match(match.children[1].textContent, /7 из 7 проверяемых условий выполнены/);
  assert.match(vm.runInContext('matchPercent(0)', context), /^0\s*%$/);
  assert.match(vm.runInContext('matchPercent(1)', context), /^100\s*%$/);
  assert.match(vm.runInContext('matchPercent(0.99999)', context), /^100\s*%$/);
});

async function comparisonApp() {
  const state = await app(undefined, async query => {
    const result = recommendation(query);
    result.cards = [0,1,2].map(index => ({...result.cards[0],id:'sample-'+index,anon_name:'Профиль '+index,rank:index+1,
      event_formats:['корпоратив','свадьба'],experience_excerpt:index===0 ? 'Опыт работы — 12 лет.' : null}));
    return {ok:true,json:async () => result};
  });
  state.select = index => {
    const card = state.elements.cards.children[index];
    const checkbox = card.children.find(node => node.className === 'compare-toggle').children[0];
    checkbox.checked = !checkbox.checked;
    checkbox.listeners.change();
    return checkbox;
  };
  return state;
}

test('comparison accepts two or three unique IDs and renders actual values without invented experience or rating', async () => {
  const {elements,select} = await comparisonApp();
  assert.equal(elements['compare-selected'].disabled, true);
  const first = select(0);
  first.listeners.change(); // Same ID cannot be added twice.
  assert.match(elements['compare-count'].textContent, /1 из 3/);
  assert.equal(elements['compare-selected'].disabled, true);
  select(1);
  assert.equal(elements['compare-selected'].disabled, false);
  elements['compare-selected'].listeners.click();
  assert.equal(elements['comparison-panel'].hidden, false);
  assert.equal(elements['comparison-title'].focused, true);
  let [,head,body] = elements['comparison-table'].children;
  assert.equal(head.children[0].children.length, 3);
  assert.equal(body.children[0].children[1].textContent, 'от 100 000 ₸');
  assert.equal(body.children[1].children[0].textContent, 'Соответствие параметрам');
  assert.equal(body.children[2].children[1].textContent, 'корпоратив, свадьба');
  assert.equal(body.children[5].children[1].textContent, '«Опыт работы — 12 лет.»');
  assert.equal(body.children[5].children[2].textContent, 'Не указано');
  assert.equal(body.children.some(row => /Рейтинг|звёзд/.test(row.children[0].textContent)), false);
  select(2);
  assert.equal(elements['comparison-table'].children[1].children[0].children.length, 4);
  select(1);
  assert.equal(elements['comparison-table'].children[1].children[0].children.length, 3);
  select(0);
  assert.equal(elements['comparison-panel'].hidden, true);
  assert.equal(elements['compare-selected'].disabled, true);
});

test('clear comparison unchecks every card and empties the table', async () => {
  const {elements,select} = await comparisonApp();
  const inputs = [select(0),select(1)];
  elements['compare-selected'].listeners.click();
  elements['clear-comparison'].listeners.click();
  assert.equal(elements['comparison-panel'].hidden, true);
  assert.equal(elements['comparison-table'].children.length, 0);
  assert.equal(inputs.every(input => !input.checked), true);
  assert.match(elements['compare-count'].textContent, /0 из 3/);
});

test('manual and AI searches clear old comparison even when profile IDs recur', async () => {
  const {elements,select,clickAI} = await comparisonApp();
  select(0); select(1);
  elements['compare-selected'].listeners.click();
  elements['search-form'].listeners.submit({preventDefault(){}});
  await nextTurn();
  assert.match(elements['compare-count'].textContent, /0 из 3/);
  assert.equal(elements['comparison-panel'].hidden, true);
  select(0); select(2);
  elements['compare-selected'].listeners.click();
  await clickAI();
  assert.match(elements['compare-count'].textContent, /0 из 3/);
  assert.equal(elements['comparison-table'].children.length, 0);
  assert.equal(elements['comparison-panel'].hidden, true);
});
