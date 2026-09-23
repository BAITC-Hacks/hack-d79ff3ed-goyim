'use strict';
const $ = id => document.getElementById(id);
const form = $('search-form');
const money = n => new Intl.NumberFormat('ru-RU').format(n) + ' ₸';
const matchPercent = relevance => new Intl.NumberFormat('ru-RU', {style:'percent', maximumFractionDigits:1}).format(Math.max(0, Math.min(1, relevance)));
const dateText = value => new Date(value + 'T12:00:00Z').toLocaleDateString('ru-RU', {day:'numeric',month:'long',year:'numeric',timeZone:'UTC'});
let previous = null;
let metadata = null;
let busy = false;
let requestSequence = 0;
let editRevision = 0;
let parsing = false;
let recoveryButtons = [];
form.addEventListener('input', () => { editRevision += 1; });
form.addEventListener('change', () => { editRevision += 1; });
$('event-request').addEventListener('input', () => { editRevision += 1; });

function el(tag, cls, text) {
  const node = document.createElement(tag);
  if (cls) node.className = cls;
  if (text !== undefined) node.textContent = text;
  return node;
}
function fill(id, values, selected, preserve = false) {
  if (!preserve) $(id).replaceChildren();
  if (!preserve) { const empty = el('option', '', 'Выберите…'); empty.value = ''; $(id).append(empty); }
  values.forEach(v => { const option = el('option', '', v); option.value = v; $(id).append(option); });
  $(id).value = selected;
}
function readQuery() {
  return {city:$('city').value, date:$('date').value, category:$('category').value,
    event_format:$('event_format').value, budget:Number($('budget').value),
    language:$('language').value || null, hours:$('hours').value ? Number($('hours').value) : null,
    brief:$('brief').value.trim()};
}
function setQuery(q) {
  editRevision += 1;
  if (!Object.hasOwn(q, 'attendance_mode')) { $('attendance_mode').value = ''; $('attendance-field').hidden = true; }
  for (const [key, value] of Object.entries(q)) if ($(key)) $(key).value = value ?? '';
}
function comparable(q) { const {date, ...rest} = q; return JSON.stringify(rest); }

function recoveryButton(query, change, label, accessibleLabel = label) {
  const button = el('button', 'secondary', label);
  button.type = 'button';
  button.disabled = busy;
  button.setAttribute('aria-label', accessibleLabel);
  button.addEventListener('click', async () => {
    if (busy) return;
    const current = readQuery();
    if (Object.entries(query).some(([key, value]) => current[key] !== value)) {
      $('form-error').textContent = 'Параметры изменились. Выполните подбор заново, чтобы обновить подсказки.';
      $('form-error').hidden = false;
      return;
    }
    setQuery({...query, ...change, attendance_mode:$('attendance_mode').value});
    button.textContent = 'Проверяем условия…';
    try { await run(true); } finally { button.textContent = label; }
  });
  recoveryButtons.push(button);
  return button;
}

async function parseEvent() {
  if (parsing || busy || !metadata) return;
  const text = $('event-request').value.trim();
  const showMessage = (message, error = false) => {
    $('parser-message').textContent = message;
    $('parser-message').className = error ? 'error' : 'hint';
    $('parser-message').hidden = false;
  };
  if (text.length < 5) { showMessage('Опишите мероприятие: укажите хотя бы один конкретный параметр.', true); return; }
  const revision = editRevision;
  const searchSequence = requestSequence;
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 7000);
  parsing = true;
  $('parse-event').disabled = true;
  $('parse-event').textContent = 'Распознаём параметры…';
  $('ai-parser').setAttribute('aria-busy', 'true');
  showMessage('Обрабатываем описание. Ручные фильтры доступны.');
  try {
    const response = await fetch('/api/parse-event', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({text}), signal:controller.signal});
    let result;
    try { result = await response.json(); } catch { throw new Error('AI-помощник сейчас недоступен. Заполните параметры вручную.'); }
    clearTimeout(timer);
    if (!response.ok) throw new Error(result.error || 'AI-помощник сейчас недоступен. Заполните параметры вручную.');
    if (editRevision !== revision || requestSequence !== searchSequence) {
      showMessage('Вы изменили описание или параметры во время обработки. Ответ AI не применён; при необходимости повторите заполнение.');
      return;
    }
    const values = EventRequestForm.toFilters(result.fields, text, metadata);
    setQuery(values);
    $('attendance-field').hidden = !values.attendance_mode;
    const labels = {city:'город', date:'дату', event_format:'тип мероприятия', category:'категорию', budget:'бюджет'};
    const missing = Object.entries(labels).filter(([key]) => values[key] === '').map(([,label]) => label);
    const modeNotice = values.attendance_mode ? ' Формат участия показан отдельно и не влияет на подбор.' : '';
    if (missing.length) {
      showMessage('Дополните: ' + missing.join(', ') + '. Затем нажмите «Подобрать подрядчиков».' + modeNotice);
      return;
    }
    if (!form.reportValidity()) {
      showMessage('Проверьте отмеченные поля и нажмите «Подобрать подрядчиков».' + modeNotice, true);
      return;
    }
    $('parse-event').textContent = 'Подбираем подрядчиков…';
    showMessage('Параметры заполнены. Выполняем подбор…' + modeNotice);
    await run(true);
    showMessage('Параметры заполнены. Их можно изменить и повторить подбор.' + modeNotice);
  } catch (error) {
    const fallback = 'AI-помощник сейчас недоступен. Заполните параметры вручную.';
    showMessage(error.name === 'AbortError' || error instanceof TypeError ? fallback : error.message, true);
  } finally {
    clearTimeout(timer);
    parsing = false;
    $('parse-event').disabled = false;
    $('parse-event').textContent = 'Заполнить параметры с AI';
    $('ai-parser').setAttribute('aria-busy', 'false');
  }
}
$('parse-event').addEventListener('click', parseEvent);

function renderCard(c) {
  const card = el('article', 'card');
  card.dataset.id = c.id;
  const top = el('div','card-top');
  top.append(el('span','rank',String(c.rank).padStart(2,'0')));
  const identity = el('div','identity');
  identity.append(el('h3','',c.anon_name),el('div','meta',c.categories.join(' · ') + ' / ' + c.city));
  const price = el('div','price');
  price.append(el('span','from','от '),document.createTextNode(money(c.price_from_kzt)),el('small','','за мероприятие'));
  top.append(identity,price);
  const match = el('div','match-summary');
  match.append(el('strong','match-score','Соответствие запросу: ' + matchPercent(c.relevance)),
    el('span','match-note','По тексту профиля; обязательные фильтры пройдены.'));
  const explanation = el('div','explanation');
  explanation.append(el('div','explanation-label','ПОЧЕМУ ПОДХОДИТ'),el('p','',c.explanation));
  const tags = el('div','card-tags');
  tags.append(el('span','tag',c.languages.join(' / ')));
  tags.append(el('span','tag',c.max_hours === null ? 'Без привязки к часам' : 'До ' + c.max_hours + ' ч на площадке'));
  if (c.synthetic) tags.append(el('span','tag synthetic','Синтетический профиль'));
  if (c.price_imputed) tags.append(el('span','tag','Цена проставлена в датасете'));
  if (c.city_imputed) tags.append(el('span','tag','Город проставлен в датасете'));
  const details = el('details','card-details');
  details.append(el('summary','','Описание и данные профиля'),el('p','',c.description));
  const facts = el('dl','');
  for (const [key,value] of [['ID профиля',c.id],['Запас до бюджета от цены «от»',money(c.budget_margin)],['Источник',c.synthetic?'Синтетический профиль организаторов':'Анонимизированный профиль каталога']]) {
    facts.append(el('dt','',key),el('dd','',value));
  }
  details.append(facts,el('p','hint','Начальная цена не является окончательной сметой. Отсутствие занятой даты в каталоге требует подтверждения у подрядчика.'));
  card.append(top,match,explanation,tags,details);
  return card;
}

function render(result) {
  const q = result.query;
  $('result-kicker').textContent = result.status === 'matched' ? 'ПОДБОРКА ДЛЯ ВАС' : 'РЕЗУЛЬТАТ ПОДБОРА';
  $('results-title').textContent = result.status === 'matched' ? (result.cards.length === 3 ? 'Ваши три варианта' : 'Подходящих вариантов: ' + result.cards.length) : (result.status === 'no_category' ? 'Такой категории пока нет' : 'Условия не совпали');
  $('result-time').textContent = result.elapsed_ms < 1000 ? Math.round(result.elapsed_ms) + ' мс' : (result.elapsed_ms / 1000).toFixed(1) + ' с';
  $('result-message').textContent = result.message;
  $('query-summary').replaceChildren(...[q.city,dateText(q.date),q.event_format,q.category,'до ' + money(q.budget)].map(t=>el('span','',t)));
  if (q.language) $('query-summary').append(el('span','',q.language));
  if (q.hours) $('query-summary').append(el('span','',q.hours+' ч'));
  if (q.brief) $('query-summary').append(el('span','','Пожелания: '+q.brief));
  $('cards').replaceChildren(...result.cards.map(renderCard));
  $('empty-state').hidden = result.cards.length > 0;
  $('empty-title').textContent = result.status === 'no_category' ? 'Попробуйте другой город' : 'По вашим условиям подрядчиков не найдено';
  $('empty-text').textContent = result.status === 'no_category' ? 'В выбранном городе в исходном каталоге нет этой категории. Смена даты или бюджета её не добавит.' : 'Ниже показаны причины. Можно изменить дату, бюджет или другие параметры и повторить подбор.';
  $('audit').hidden = result.status === 'no_category';
  $('audit-count').textContent = result.counts.in_category + ' в городе и категории · ' + result.counts.eligible + ' подходят';
  $('audit-reasons').replaceChildren(...result.rejections.map(r => { const n=el('span','audit-reason');n.append(el('b','',r.count),document.createTextNode(r.label));return n;}));
  $('audit-note').textContent = result.rejections.length ? 'У одного профиля может быть несколько причин отказа. Для выдачи должны выполняться все условия.' : 'Все профили в выбранном городе и категории проходят условия.';
  $('alternatives').replaceChildren();
  recoveryButtons = [];
  if (result.status !== 'no_category' && result.suggested_min_budget > q.budget) {
    const minimum = result.suggested_min_budget;
    const option = el('div', 'recovery-option');
    option.append(el('p','','Увеличьте бюджет минимум на '+money(minimum-q.budget)+' — до '+money(minimum)+'.'),
      recoveryButton(q, {budget:minimum}, 'Искать с бюджетом '+money(minimum)));
    $('alternatives').append(option);
  }
  if (result.status !== 'no_category' && result.alternative_dates.length) {
    for (const alt of result.alternative_dates) {
      const count = alt.count;
      const noun = {one:'подрядчик',few:'подрядчика',many:'подрядчиков',other:'подрядчика'}[new Intl.PluralRules('ru').select(count)];
      const option = el('div', 'recovery-option');
      option.append(el('p','','На '+dateText(alt.date)+(count === 1 ? ' подходит ' : ' подходят ')+count+' '+noun+'.'),
        recoveryButton(q, {date:alt.date}, 'Проверить эту дату', 'Проверить дату '+dateText(alt.date)));
      $('alternatives').append(option);
    }
  }
  $('alternatives').hidden = !$('alternatives').childElementCount;
  if (!$('alternatives').hidden) $('alternatives').append(el('p','hint','Изменится только выбранный параметр. Остальные условия поиска сохраняются.'));
  else if (result.status === 'no_matches') $('empty-text').textContent = 'Проверенных альтернатив по бюджету или соседним датам нет. Посмотрите причины отказа ниже и измените параметры вручную.';
  $('date-change').hidden = true;
  if (previous && comparable(previous.query) === comparable(q) && previous.query.date !== q.date) {
    const nowIds = new Set(result.cards.map(c=>c.id));
    const oldIds = new Set(previous.cards.map(c=>c.id));
    const removed = previous.cards.filter(c=>!nowIds.has(c.id));
    const added = result.cards.filter(c=>!oldIds.has(c.id));
    const lines = ['Дата изменена: '+dateText(previous.query.date)+' → '+dateText(q.date)+'. Подходящих профилей: '+previous.counts.eligible+' → '+result.counts.eligible+'.'];
    const becameBusy = removed.filter(c=>result.busy_profile_ids.includes(c.id));
    const displaced = removed.filter(c=>!result.busy_profile_ids.includes(c.id));
    if (becameBusy.length) lines.push('Заняты на новую дату: '+becameBusy.map(c=>c.anon_name).join(', ')+'.');
    if (displaced.length) lines.push('Проходят условия, но уступили место в первых трёх: '+displaced.map(c=>c.anon_name).join(', ')+'.');
    if (added.length) lines.push('В новой подборке: '+added.map(c=>c.anon_name).join(', ')+'.');
    if (!removed.length && !added.length) lines.push(result.cards.length ? 'Показанные варианты не изменились: на обе даты они проходят проверку занятости.' : 'На обе даты подходящих вариантов нет.');
    $('date-change').textContent=lines.join(' ');$('date-change').hidden=false;
  }
  $('result-actions').hidden=false;
  $('next-date').disabled=q.date>=metadata.calendar_end;
  $('mode-label').textContent=result.explanation_mode.startsWith('ai')?'Фрагменты выбраны AI':'Объяснения по данным каталога';
  $('ai-notice').textContent=result.ai_notice || '';$('ai-notice').hidden=!result.ai_notice;
  previous=result;
}

async function run(scroll = false) {
  if (busy || !metadata || !form.reportValidity()) return;
  busy=true;
  recoveryButtons.forEach(button => { button.disabled = true; });
  const sequence=++requestSequence;
  const revision=editRevision;
  $('form-error').hidden=true;
  $('submit').disabled=true;$('submit').textContent='Проверяем условия…';
  document.querySelector('.results-panel').setAttribute('aria-busy','true');
  const controller=new AbortController();
  const timer=setTimeout(()=>controller.abort(),9000);
  try {
    const response=await fetch('/api/recommend',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(readQuery()),signal:controller.signal});
    const result=await response.json();
    if (!response.ok) throw new Error(result.error || 'Не удалось выполнить подбор.');
    if(sequence===requestSequence) {
      render(result);
      if(editRevision!==revision) {$('form-error').textContent='Параметры изменились во время подбора. Нажмите «Подобрать», чтобы обновить результат.';$('form-error').hidden=false;}
      if(scroll && window.innerWidth<=720) document.querySelector('.results-panel').scrollIntoView({behavior:'smooth'});
    }
  } catch(error) {
    $('form-error').textContent=error.name==='AbortError'?'Сервер не ответил за 9 секунд. Повторите запрос.':error.message;
    $('form-error').hidden=false;
  } finally {
    clearTimeout(timer);busy=false;$('submit').disabled=false;$('submit').replaceChildren(document.createTextNode('Подобрать подрядчиков '),el('span','','↗'));
    recoveryButtons.forEach(button => { button.disabled = false; });
    document.querySelector('.results-panel').setAttribute('aria-busy','false');
  }
}
form.addEventListener('submit',event=>{event.preventDefault();run(true);});
$('next-date').addEventListener('click',()=>{
  if(!previous||busy)return;
  setQuery(previous.query);
  const next=new Date(previous.query.date+'T12:00:00Z');next.setUTCDate(next.getUTCDate()+1);
  $('date').value=next.toISOString().slice(0,10);run();
});
const base={city:'Алматы',date:'2026-10-15',category:'Ведущий',event_format:'корпоратив',budget:1000000,language:null,hours:null,brief:''};
const demos={hosts:base,rare:{...base,category:'Флорист',event_format:'свадьба',budget:500000},budget:{...base,budget:10000},absent:{...base,city:'Астана',category:'Декоратор',budget:3000000},venue:{...base,date:'2026-11-14',category:'Банкетный зал',event_format:'свадьба',budget:6000000}};
document.querySelectorAll('[data-demo]').forEach(button=>button.addEventListener('click',()=>{if(busy||!metadata)return;setQuery(demos[button.dataset.demo]);run(true);}));
async function init(){
  try {
    const response=await fetch('/api/catalog');
    if(!response.ok)throw new Error('Не удалось загрузить каталог. Перезапустите сервер и обновите страницу.');
    metadata=await response.json();
    fill('city',metadata.cities,'Алматы');fill('category',metadata.categories,'Ведущий');fill('event_format',metadata.formats,'корпоратив');fill('language',metadata.languages,'',true);
    $('dataset-label').textContent=metadata.total+' профилей · '+metadata.categories.length+' категорий · '+metadata.synthetic+' синтетических';
    $('date').min=metadata.calendar_start;$('date').max=metadata.calendar_end;
    $('submit').disabled=false;$('parse-event').disabled=false;await run();
  }catch(error){$('results-title').textContent='Каталог не загружен';$('result-message').textContent=error.message;}
}
init();
