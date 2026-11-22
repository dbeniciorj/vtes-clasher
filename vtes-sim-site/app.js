// ============================================================
// VTES Simulator & Calendar - Frontend
// Configure o endpoint do N8N / Ollama abaixo
// ============================================================

const CONFIG = {
  // Altere para o teu webhook N8N ou API que fala com o Ollama/Bert
  SIMULATION_ENDPOINT: 'https://seu-n8n.example.com/webhook/vtes-sim',
  // Ou deixe vazio para usar modo mock local
  USE_MOCK: true,
  EVENTS_JSON: 'events.json'
};

// ---------- Helpers ----------
// Os dados vêm de um site externo (VEKN): escapar sempre antes de pôr no HTML.
function escapeHtml(value) {
  return String(value ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

// Só aceita links para as fontes conhecidas (VEKN e Liga Española / vtes.es).
function safeLink(url) {
  return /^https:\/\/((www|archon)\.)?(vekn\.net|vtes\.es)\//.test(url || '') ? url : 'https://www.vekn.net/event-calendar';
}

const COUNTRY_NAMES = {
  PT: 'Portugal',
  ES: 'Espanha',
  BR: 'Brasil',
  US: 'Estados Unidos'
};

// Bandeira (flag-icons, classe "fi fi-xx") + sigla do país.
function countryBadge(code) {
  const cc = String(code || '').toUpperCase();
  const flag = /^[A-Z]{2}$/.test(cc)
    ? `<span class="fi fi-${cc.toLowerCase()}" aria-hidden="true"></span>`
    : '';
  const name = COUNTRY_NAMES[cc] || cc;
  return `<div class="event-country" title="${escapeHtml(name)}">${flag}<span>${escapeHtml(cc)}</span></div>`;
}

function currentMonthKey() {
  const now = new Date();
  return `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}`;
}

// ---------- Tabs ----------
document.querySelectorAll('.tab-btn').forEach(btn => {
  btn.addEventListener('click', () => {
    document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
    document.querySelectorAll('.tab-panel').forEach(p => p.classList.remove('active'));
    btn.classList.add('active');
    document.getElementById(btn.dataset.tab).classList.add('active');
  });
});

// ---------- Calendar ----------
let allEvents = [];
let loadError = false;

// Preenche o filtro de meses com os meses do events.json (mantém a escolha atual).
function populateMonthFilter(months) {
  const select = document.getElementById('month-filter');
  const previous = select.value;
  const fallback = months.includes(currentMonthKey()) ? currentMonthKey() : (months[0] || '');

  select.innerHTML = '<option value="">Todos os meses</option>' + months.map(m => {
    const label = new Date(m + '-15T12:00:00').toLocaleString('pt-PT', { month: 'long', year: 'numeric' });
    return `<option value="${escapeHtml(m)}">${escapeHtml(label.charAt(0).toUpperCase() + label.slice(1))}</option>`;
  }).join('');

  select.value = (previous && months.includes(previous)) ? previous : fallback;
}

async function loadEvents() {
  try {
    const res = await fetch(CONFIG.EVENTS_JSON + '?t=' + Date.now());
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();

    // Formato novo: { generated_at, months, events: [...] }; formato antigo: [...]
    const events = Array.isArray(data) ? data : (data.events || []);
    // Meses = janela da VEKN (data.months) + meses de qualquer evento (ex.: vtes.es)
    const months = [...new Set([
      ...(!Array.isArray(data) && Array.isArray(data.months) ? data.months : []),
      ...events.map(ev => (ev.date || '').slice(0, 7))
    ].filter(m => /^\d{4}-\d{2}$/.test(m)))].sort();

    allEvents = events;
    loadError = false;
    populateMonthFilter(months);

    const generatedAt = !Array.isArray(data) && data.generated_at ? new Date(data.generated_at) : null;
    document.getElementById('last-update').textContent =
      generatedAt && !isNaN(generatedAt) ? generatedAt.toLocaleString('pt-PT') : '—';
  } catch (e) {
    console.warn('Não foi possível carregar events.json.', e);
    loadError = true;
  }
  renderEvents();
}

function renderEvents() {
  const country = document.getElementById('country-filter').value;
  const month = document.getElementById('month-filter').value; // YYYY-MM ou '' (todos)

  const filtered = allEvents.filter(ev => {
    const matchCountry = country === 'all' || ev.country === country;
    const matchMonth = !month || (ev.date && ev.date.startsWith(month));
    return matchCountry && matchMonth;
  });

  filtered.sort((a, b) =>
    (a.date || '').localeCompare(b.date || '') || (a.time || '').localeCompare(b.time || ''));

  const container = document.getElementById('events-container');
  if (!filtered.length) {
    const msg = loadError
      ? 'Não foi possível carregar os eventos. Tenta novamente mais tarde.'
      : 'Nenhum evento encontrado para os filtros selecionados.';
    container.innerHTML = `<p style="color:var(--text-muted);padding:1rem 0;">${msg}</p>`;
    return;
  }

  container.innerHTML = filtered.map(ev => {
    const d = new Date(ev.date + 'T12:00:00');
    const day = d.getDate();
    const monthShort = d.toLocaleString('pt-PT', { month: 'short' });
    const meta = [ev.time, ev.type, ev.league].filter(Boolean).join(' · ');
    const place = [ev.venue, ev.location].filter(Boolean).join(' — ');
    return `
      <article class="event-card">
        <div class="event-date">
          <div class="day">${day}</div>
          <div class="month">${escapeHtml(monthShort)}</div>
        </div>
        <div class="event-info">
          <h3><a href="${escapeHtml(safeLink(ev.link))}" target="_blank" rel="noopener">${escapeHtml(ev.title)}</a></h3>
          ${meta ? `<div class="event-meta">${escapeHtml(meta)}</div>` : ''}
          <div class="event-meta">${escapeHtml(place)}</div>
        </div>
        ${countryBadge(ev.country)}
      </article>
    `;
  }).join('');
}

document.getElementById('country-filter').addEventListener('change', renderEvents);
document.getElementById('month-filter').addEventListener('change', renderEvents);
document.getElementById('refresh-events').addEventListener('click', loadEvents);

// ---------- Simulator ----------
let deckCount = 0;
const MAX_DECKS = 5;
const MIN_DECKS = 2;

function createDeckSlot(index) {
  const div = document.createElement('div');
  div.className = 'deck-slot';
  div.dataset.index = index;
  div.innerHTML = `
    <h4>
      Deck ${index + 1}
      <button type="button" class="remove-deck" title="Remover" ${index < MIN_DECKS ? 'style="visibility:hidden"' : ''}>✕</button>
    </h4>
    <div class="input-group">
      <label>Nome / Identificador (opcional)</label>
      <input type="text" name="deck-name-${index}" placeholder="Ex: Tzimise nac">
    </div>
    <div class="input-group">
      <label>Link do deck (vdb.im ou vtesdecks.com)</label>
      <input type="url" name="deck-url-${index}" placeholder="https://vdb.im/decks/13317">
    </div>
    <div class="input-group">
      <label>Ou cola o texto / TWD do deck</label>
      <textarea name="deck-text-${index}" placeholder="Deck Name: ...&#10;Crypt (12 cards)...&#10;Library (90 cards)..."></textarea>
    </div>
    <div class="input-group">
      <label>Ou faz upload de ficheiro .txt</label>
      <input type="file" name="deck-file-${index}" accept=".txt,.text,text/plain">
    </div>
  `;

  div.querySelector('.remove-deck').addEventListener('click', () => {
    if (deckCount <= MIN_DECKS) return;
    div.remove();
    deckCount--;
    // re-number titles
    document.querySelectorAll('.deck-slot').forEach((slot, i) => {
      slot.querySelector('h4').childNodes[0].textContent = `Deck ${i + 1} `;
    });
  });

  // file reader
  div.querySelector('input[type="file"]').addEventListener('change', async (e) => {
    const file = e.target.files[0];
    if (!file) return;
    const text = await file.text();
    div.querySelector('textarea').value = text;
  });

  return div;
}

function initDeckSlots() {
  const container = document.getElementById('deck-inputs');
  container.innerHTML = '';
  deckCount = 0;
  for (let i = 0; i < MIN_DECKS; i++) {
    container.appendChild(createDeckSlot(i));
    deckCount++;
  }
}

document.getElementById('add-deck').addEventListener('click', () => {
  if (deckCount >= MAX_DECKS) return;
  document.getElementById('deck-inputs').appendChild(createDeckSlot(deckCount));
  deckCount++;
});

// Collect decks from form
async function collectDecks() {
  const slots = document.querySelectorAll('.deck-slot');
  const decks = [];

  for (const slot of slots) {
    const idx = slot.dataset.index;
    const name = slot.querySelector(`[name="deck-name-${idx}"]`).value.trim();
    const url = slot.querySelector(`[name="deck-url-${idx}"]`).value.trim();
    const text = slot.querySelector(`[name="deck-text-${idx}"]`).value.trim();

    if (!url && !text) continue;

    decks.push({
      name: name || `Deck ${parseInt(idx) + 1}`,
      url: url || null,
      text: text || null
    });
  }
  return decks;
}

// Mock simulation (enquanto não há backend)
function mockSimulation(decks) {
  const names = decks.map(d => d.name).join(' vs ');
  return `
=== SIMULAÇÃO MOCK (configure o endpoint real) ===

Mesas / Combates: ${names}

Resultado simulado (Bert / Ollama ainda não ligado):

1º lugar: ${decks[0]?.name || 'Deck A'}  —  3 VP + 1.5 GW
2º lugar: ${decks[1]?.name || 'Deck B'}  —  1 VP
${decks.length > 2 ? `3º lugar: ${decks[2].name}  —  0.5 VP` : ''}

Notas do simulador:
- Os decks foram recebidos corretamente.
- Em produção o N8N envia o payload para o Ollama (Bert) e devolve o relatório completo.
- Tempo estimado real: 30-90s dependendo do modelo e número de mesas.

Payload enviado (exemplo):
${JSON.stringify({ decks, timestamp: new Date().toISOString() }, null, 2)}
`.trim();
}

document.getElementById('sim-form').addEventListener('submit', async (e) => {
  e.preventDefault();

  const decks = await collectDecks();
  if (decks.length < MIN_DECKS) {
    alert(`Precisas de pelo menos ${MIN_DECKS} decks para simular.`);
    return;
  }

  const statusEl = document.getElementById('sim-status');
  const resultEl = document.getElementById('sim-result');
  const contentEl = document.getElementById('result-content');

  statusEl.classList.remove('hidden');
  resultEl.classList.add('hidden');

  try {
    let resultText;

    if (CONFIG.USE_MOCK || !CONFIG.SIMULATION_ENDPOINT.includes('http')) {
      // simula latência
      await new Promise(r => setTimeout(r, 1800));
      resultText = mockSimulation(decks);
    } else {
      const res = await fetch(CONFIG.SIMULATION_ENDPOINT, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ decks, source: 'vtes-sim-frontend' })
      });

      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      resultText = data.result || data.message || JSON.stringify(data, null, 2);
    }

    contentEl.textContent = resultText;
    resultEl.classList.remove('hidden');
  } catch (err) {
    contentEl.textContent = `Erro na simulação:\n${err.message}\n\nVerifica o endpoint em app.js ou usa o modo mock.`;
    resultEl.classList.remove('hidden');
  } finally {
    statusEl.classList.add('hidden');
  }
});

// ---------- Init ----------
initDeckSlots();
loadEvents();

// Pre-fill example decks for demo (opcional)
window.addEventListener('DOMContentLoaded', () => {
  // Pode remover isto depois
  const firstText = document.querySelector('[name="deck-text-0"]');
  if (firstText && !firstText.value) {
    // deixa vazio por default; o utilizador cola ou faz upload
  }
});
