// Athena-MCP Dashboard Frontend
const API_BASE = 'http://127.0.0.1:20129';

// State
let providersData = [];
let combosData = [];
let usageData = {};

// DOM Elements
const toast = document.getElementById('toast');

// Utility
function showToast(message, type = 'info') {
  const colors = {
    info: 'border-indigo-500 text-indigo-300',
    success: 'border-green-500 text-green-300',
    error: 'border-red-500 text-red-300',
    warning: 'border-yellow-500 text-yellow-300',
  };
  toast.className = `fixed bottom-6 right-6 glass rounded-xl px-4 py-3 text-sm transform transition-all duration-300 z-50 max-w-sm border-l-2 ${colors[type] || colors.info}`;
  toast.innerHTML = message;
  toast.style.transform = 'translateY(0)';
  toast.style.opacity = '1';
  setTimeout(() => {
    toast.style.transform = 'translateY(20px)';
    toast.style.opacity = '0';
  }, 4000);
}

function switchTab(tabName) {
  document.querySelectorAll('.tab-content').forEach(el => el.classList.add('hidden'));
  document.querySelectorAll('.tab-btn').forEach(el => el.classList.remove('tab-active'));
  document.getElementById(`content-${tabName}`).classList.remove('hidden');
  document.getElementById(`tab-${tabName}`).classList.add('tab-active');

  if (tabName === 'providers') loadProviders();
  if (tabName === 'combos') loadCombos();
  if (tabName === 'models') loadRatings();
  if (tabName === 'usage') loadUsage();
  if (tabName === 'create') loadCreateForm();
}

// Ratings — Melhores por Função
async function loadRatings() {
  const container = document.getElementById('ratings-content');
  const data = await apiGet('/api/v1/ratings');
  if (!data) {
    container.innerHTML = '<p class="text-red-400">Erro ao carregar rankings.</p>';
    return;
  }

  const roleIcons = { frontend: '🎨', backend: '⚙️', raciocinio: '🧠', rapidez: '⚡' };
  const medals = ['🥇', '🥈', '🥉'];

  const updated = data.meta && data.meta.updated_at ? data.meta.updated_at.slice(0, 10) : '?';
  const stale = data.meta && data.meta.stale ? ' · <span class="text-yellow-400">atualização pendente</span>' : '';
  document.getElementById('ratings-updated').innerHTML = `Atualizado em ${updated}${stale}`;

  container.innerHTML = `<div class="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-4 gap-4">` +
    Object.entries(data.roles).map(([role, label]) => {
      const entries = (data.best_per_role[role] || []).slice(0, 5);
      const rows = entries.map((e, i) => `
        <div class="flex items-start gap-2 py-1.5 ${i > 0 ? 'border-t border-white/5' : ''}">
          <span class="text-sm w-6">${medals[i] || `<span class="text-gray-500 text-xs">${i + 1}.</span>`}</span>
          <div class="flex-1 min-w-0">
            <div class="flex items-center gap-2">
              <span class="text-sm font-medium text-white truncate">${e.name}</span>
              <span class="text-xs text-indigo-300 font-mono">${e.score}/10</span>
              ${e.installed ? '<span class="text-green-400 text-xs" title="Instalado na sua máquina">●</span>' : ''}
            </div>
            <p class="text-xs text-gray-500 truncate" title="${e.note}">${e.maker}${e.note ? ' · ' + e.note : ''}</p>
          </div>
        </div>`).join('');
      return `
        <div class="glass rounded-xl p-4">
          <h3 class="font-semibold text-white mb-2">${roleIcons[role] || '🏷️'} ${label}</h3>
          ${rows}
        </div>`;
    }).join('') + `</div>`;
}

async function apiGet(path) {
  try {
    const res = await fetch(`${API_BASE}${path}`);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    return await res.json();
  } catch (err) {
    console.error('API Error:', err);
    showToast(`Erro na API: ${err.message}`, 'error');
    return null;
  }
}

async function apiPost(path, body) {
  try {
    const res = await fetch(`${API_BASE}${path}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
      body: new URLSearchParams(body),
    });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    return await res.text();
  } catch (err) {
    console.error('API Error:', err);
    showToast(`Erro: ${err.message}`, 'error');
    return null;
  }
}

async function apiDelete(path) {
  try {
    const res = await fetch(`${API_BASE}${path}`, { method: 'DELETE' });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    return await res.text();
  } catch (err) {
    console.error('API Error:', err);
    showToast(`Erro: ${err.message}`, 'error');
    return null;
  }
}

// Providers
async function loadProviders() {
  const data = await apiGet('/api/v1/providers');
  if (!data) return;
  providersData = data.providers || [];

  // Update stats
  const active = providersData.filter(p => p.available).length;
  document.getElementById('stat-clis').textContent = `${active}/${providersData.length}`;

  // Render grid
  const grid = document.getElementById('providers-grid');
  if (!providersData.length) {
    grid.innerHTML = '<div class="glass rounded-xl p-8 text-center text-gray-400">Nenhum provider encontrado</div>';
    return;
  }

  const icons = {
    claude: '🤖', agent: '🎯', agy: '💎', codex: '⚡', openclaude: '🔓',
    kimi: '🌙', ollama: '🦙', aider: '🤝', opencode: '📟',
    goose: '🪿', crush: '🍭', qwen: '🐉', cagent: '🐳', mods: '💄',
    aichat: '💬', llm: '📜', sgpt: '🐚', copilot: '🚁', plandex: '🗺️',
    auggie: '🧠', codebuddy: '👥', windsurf: '🏄',
  };

  grid.innerHTML = providersData.map(p => {
    const status = p.available ? 'online' : 'offline';
    const statusColor = p.available ? 'text-green-400' : 'text-red-400';
    const statusText = p.available ? 'Online' : 'Offline';
    const icon = icons[p.id] || '🔧';

    return `
      <div class="glass rounded-xl p-5 provider-card ${status} card-hover">
        <div class="flex items-start justify-between mb-3">
          <div class="flex items-center gap-3">
            <span class="text-2xl">${icon}</span>
            <div>
              <h3 class="font-semibold text-white">${p.name}</h3>
              <p class="text-xs text-gray-400">${p.id}</p>
            </div>
          </div>
          <span class="text-xs ${statusColor} font-medium">${statusText}</span>
        </div>
        <p class="text-sm text-gray-400 mb-3">${p.description}</p>
        ${p.available ? `
          <div class="text-xs text-gray-500 space-y-1">
            <p><i class="fas fa-folder mr-1"></i>${p.path}</p>
            <p><i class="fas fa-star mr-1"></i>Default: <span class="text-indigo-300">${p.recommended_default_model || 'auto'}</span></p>
            <p><i class="fas fa-user-tag mr-1"></i>Role: <span class="text-purple-300">${p.role_name || '—'}</span></p>
          </div>
          <div class="mt-3 flex gap-2">
            <button onclick="viewModels('${p.id}')" class="text-xs px-3 py-1.5 rounded-lg bg-indigo-600/20 hover:bg-indigo-600/30 text-indigo-300 transition-all">
              <i class="fas fa-brain mr-1"></i>Ver Modelos
            </button>
          </div>
        ` : `
          <div class="text-xs text-red-400/70">
            <i class="fas fa-exclamation-triangle mr-1"></i>CLI não encontrada no PATH
          </div>
        `}
      </div>
    `;
  }).join('');
}

// Models
function viewModels(providerId) {
  switchTab('models');
  loadModels(providerId);
}

async function loadModels(providerId) {
  const container = document.getElementById('models-content');
  container.innerHTML = '<div class="animate-spin w-6 h-6 border-2 border-indigo-500 border-t-transparent rounded-full"></div>';

  const html = await apiGet(`/hx/models/${providerId}`);
  if (html) {
    container.innerHTML = `<div class="glass rounded-xl p-6">${html}</div>`;
  }
}

// Combos
async function loadCombos() {
  const data = await apiGet('/api/v1/combos');
  if (!data) return;
  combosData = data.combos || [];

  document.getElementById('stat-combos').textContent = combosData.length;

  const list = document.getElementById('combos-list');
  if (!combosData.length) {
    list.innerHTML = `
      <div class="glass rounded-xl p-8 text-center">
        <p class="text-gray-400 mb-4">Nenhum combo configurado</p>
        <button onclick="switchTab('create')" class="px-4 py-2 bg-indigo-600 hover:bg-indigo-700 rounded-lg text-white text-sm">
          <i class="fas fa-plus mr-2"></i>Criar Primeiro Combo
        </button>
      </div>`;
    return;
  }

  list.innerHTML = combosData.map(c => {
    const chainSteps = c.chain.map((s, i) => `
      <span class="inline-flex items-center px-2.5 py-1 rounded-md bg-indigo-600/30 text-indigo-300 text-xs font-medium">
        ${i + 1}. ${s.provider_id}
        ${s.model ? `<span class="ml-1 text-indigo-400">(${s.model})</span>` : ''}
      </span>
      ${i < c.chain.length - 1 ? '<i class="fas fa-arrow-right chain-arrow"></i>' : ''}
    `).join('');

    return `
      <div class="glass rounded-xl p-5 card-hover">
        <div class="flex items-start justify-between mb-3">
          <div>
            <div class="flex items-center gap-2">
              <h3 class="font-semibold text-white">${c.name}</h3>
              ${c.enabled
                ? '<span class="px-2 py-0.5 rounded-full bg-green-500/20 text-green-400 text-xs">Ativo</span>'
                : '<span class="px-2 py-0.5 rounded-full bg-red-500/20 text-red-400 text-xs">Inativo</span>'}
            </div>
            <p class="text-xs text-gray-400 mt-1">ID: ${c.id}</p>
            ${c.description ? `<p class="text-sm text-gray-500 mt-1">${c.description}</p>` : ''}
          </div>
          <div class="flex gap-2">
            <button onclick="testCombo('${c.id}')" class="text-xs px-3 py-1.5 rounded-lg bg-blue-600/20 hover:bg-blue-600/30 text-blue-300 transition-all">
              <i class="fas fa-vial mr-1"></i>Testar
            </button>
            <button onclick="deleteCombo('${c.id}')" class="text-xs px-3 py-1.5 rounded-lg bg-red-600/20 hover:bg-red-600/30 text-red-300 transition-all">
              <i class="fas fa-trash mr-1"></i>Excluir
            </button>
          </div>
        </div>
        <div class="flex flex-wrap items-center gap-2 mt-3">
          ${chainSteps}
        </div>
        <div id="test-result-${c.id}" class="mt-3"></div>
      </div>
    `;
  }).join('');
}

async function testCombo(comboId) {
  const resultEl = document.getElementById(`test-result-${comboId}`);
  resultEl.innerHTML = '<div class="text-xs text-indigo-400"><i class="fas fa-spinner fa-spin mr-1"></i>Testando...</div>';

  try {
    const res = await fetch(`${API_BASE}/hx/combos/${comboId}/test`, { method: 'POST' });
    const html = await res.text();
    resultEl.innerHTML = html;
  } catch (err) {
    resultEl.innerHTML = `<div class="text-xs text-red-400">Erro: ${err.message}</div>`;
  }
}

async function deleteCombo(comboId) {
  if (!confirm(`Excluir combo '${comboId}'?`)) return;
  await apiDelete(`/hx/combos/${comboId}`);
  showToast('Combo excluído!', 'success');
  loadCombos();
}

// Create Combo Form
async function loadCreateForm() {
  // Populate provider selects
  if (!providersData.length) {
    const data = await apiGet('/api/v1/providers');
    if (data) providersData = data.providers || [];
  }

  const available = providersData.filter(p => p.available);
  const options = available.map(p => `<option value="${p.id}">${p.name}</option>`).join('');

  [1, 2, 3].forEach(i => {
    const select = document.getElementById(`provider-${i}`);
    const currentValue = select.value;
    select.innerHTML = i === 1
      ? `<option value="">Selecione...</option>${options}`
      : `<option value="">Nenhum</option>${options}`;
    if (currentValue) select.value = currentValue;
  });
}

document.getElementById('combo-form')?.addEventListener('submit', async (e) => {
  e.preventDefault();
  const resultEl = document.getElementById('create-result');
  resultEl.classList.remove('hidden');
  resultEl.className = 'p-3 rounded-lg text-sm bg-indigo-600/20 text-indigo-300';
  resultEl.innerHTML = '<i class="fas fa-spinner fa-spin mr-2"></i>Criando combo...';

  const body = {
    combo_id: document.getElementById('combo-id').value,
    name: document.getElementById('combo-name').value,
    description: document.getElementById('combo-desc').value,
    provider_1: document.getElementById('provider-1').value,
    model_1: document.getElementById('model-1').value,
    provider_2: document.getElementById('provider-2').value,
    model_2: document.getElementById('model-2').value,
    provider_3: document.getElementById('provider-3').value,
    model_3: document.getElementById('model-3').value,
  };

  const res = await apiPost('/hx/combos', body);
  if (res !== null) {
    resultEl.className = 'p-3 rounded-lg text-sm bg-green-600/20 text-green-300';
    resultEl.innerHTML = '<i class="fas fa-check mr-2"></i>Combo criado com sucesso!';
    showToast('Combo criado!', 'success');
    setTimeout(() => {
      resetForm();
      switchTab('combos');
    }, 1500);
  } else {
    resultEl.className = 'p-3 rounded-lg text-sm bg-red-600/20 text-red-300';
    resultEl.innerHTML = '<i class="fas fa-exclamation-circle mr-2"></i>Erro ao criar combo';
  }
});

function resetForm() {
  document.getElementById('combo-form').reset();
  document.getElementById('create-result').classList.add('hidden');
}

// Usage
async function loadUsage() {
  const data = await apiGet('/api/v1/usage');
  if (!data) return;
  usageData = data.usage || {};

  const totalCalls = Object.values(usageData).reduce((sum, u) => sum + (u.calls || 0), 0);
  document.getElementById('stat-calls').textContent = totalCalls;

  const container = document.getElementById('usage-content');
  if (!Object.keys(usageData).length) {
    container.innerHTML = `
      <div class="text-center py-8">
        <i class="fas fa-chart-bar text-4xl text-gray-600 mb-3"></i>
        <p class="text-gray-400">Nenhuma chamada registrada ainda</p>
        <p class="text-sm text-gray-500 mt-1">Use um combo para começar a acumular métricas</p>
      </div>`;
    return;
  }

  const rows = Object.entries(usageData).map(([provider, u]) => `
    <div class="flex items-center justify-between py-3 border-b border-gray-700/50 last:border-0">
      <div class="flex items-center gap-3">
        <div class="w-2 h-2 rounded-full bg-indigo-500"></div>
        <span class="font-medium">${provider}</span>
      </div>
      <div class="flex gap-6 text-sm text-gray-400">
        <span><i class="fas fa-phone mr-1"></i>${u.calls} chamadas</span>
        <span><i class="fas fa-clock mr-1"></i>${u.total_duration_s?.toFixed(1) || 0}s</span>
        <span><i class="fas fa-coins mr-1"></i>${u.estimated_tokens} tokens est.</span>
      </div>
    </div>
  `).join('');

  container.innerHTML = `
    <div class="mb-4 flex items-center justify-between">
      <h3 class="font-medium text-white">Resumo por Provider</h3>
      <span class="text-sm text-gray-400">${totalCalls} chamadas totais</span>
    </div>
    ${rows}
  `;
}

// Refresh all
async function refreshAll() {
  showToast('Atualizando...', 'info');
  await Promise.all([loadProviders(), loadCombos(), loadUsage()]);
  showToast('Dados atualizados!', 'success');
}

async function refreshProviders() {
  await fetch(`${API_BASE}/hx/providers/refresh`, { method: 'POST' });
  showToast('CLIs re-detectados!', 'success');
  loadProviders();
}

// Check API status
async function checkStatus() {
  try {
    const res = await fetch(`${API_BASE}/hx/status`);
    const text = await res.text();
    const dot = document.getElementById('status-dot');
    const txt = document.getElementById('status-text');
    if (text.includes('Online')) {
      dot.className = 'w-2 h-2 rounded-full bg-green-500 pulse-dot';
      txt.className = 'text-green-400';
      txt.textContent = 'Online';
    }
  } catch {
    const dot = document.getElementById('status-dot');
    const txt = document.getElementById('status-text');
    dot.className = 'w-2 h-2 rounded-full bg-red-500';
    txt.className = 'text-red-400';
    txt.textContent = 'Offline';
  }
}

// Init
async function init() {
  await checkStatus();
  await loadProviders();
  await loadCombos();
  await loadUsage();
  setInterval(checkStatus, 30000);
}

init();
