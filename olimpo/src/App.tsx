import { useState, useEffect } from 'react';
import { getCsrfToken, getApiBaseUrl } from './bootstrap';
import {
  HealthStatus,
  ClioStatus,
  InventoryEntry,
  ConfigSnapshotStatus,
  ConfigPreviewResult,
  ConfigApplyResult,
  TaskItem,
  ExecutionItem,
  CapabilityState
} from './types';

// Security: list of keys that must NEVER be rendered in raw form
const FORBIDDEN_KEYS = new Set([
  'prompt', 'command', 'argv', 'cwd', 'stdout', 'stderr',
  'env', 'environment', 'output', 'response', 'message',
  'text', 'input', 'arguments', 'token', 'api_key', 'secret',
  'credential', 'password', 'authorization', 'secret_ref',
  'raw_url', 'log', 'logs'
]);

/**
 * Recursively redacts forbidden/sensitive keys from objects before display.
 */
export function sanitizeData(data: any): any {
  if (data === null || data === undefined) {
    return data;
  }
  if (Array.isArray(data)) {
    return data.map(sanitizeData);
  }
  if (typeof data === 'object') {
    const sanitized: Record<string, any> = {};
    for (const key of Object.keys(data)) {
      if (FORBIDDEN_KEYS.has(key.toLowerCase())) {
        sanitized[key] = '[REDACT_SAFE: SENSITIVE_CONTENT_OMITTED]';
      } else {
        sanitized[key] = sanitizeData(data[key]);
      }
    }
    return sanitized;
  }
  return data;
}

export default function App() {
  const [theme, setTheme] = useState<'light' | 'dark'>(() => {
    if (typeof window !== 'undefined' && typeof localStorage !== 'undefined') {
      try {
        const stored = localStorage.getItem('olimpo_theme');
        if (stored === 'light' || stored === 'dark') return stored;
      } catch (e) {}
    }
    if (typeof window !== 'undefined' && typeof window.matchMedia === 'function') {
      try {
        return window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
      } catch (e) {}
    }
    return 'light';
  });

  useEffect(() => {
    const root = document.documentElement;
    root.setAttribute('data-theme', theme);
    if (typeof localStorage !== 'undefined') {
      try {
        localStorage.setItem('olimpo_theme', theme);
      } catch (e) {}
    }
  }, [theme]);

  // Tab navigation state
  const [activeTab, setActiveTab] = useState<'overview' | 'tasks' | 'executions' | 'clio' | 'inventory' | 'config'>('overview');

  // API Status & CSRF token
  const apiBase = getApiBaseUrl();
  const csrfToken = getCsrfToken();

  // Core Data States
  const [health, setHealth] = useState<HealthStatus | null>(null);
  const [healthError, setHealthError] = useState<string | null>(null);
  const [healthLoading, setHealthLoading] = useState(true);

  const [tasks, setTasks] = useState<TaskItem[]>([]);
  const [selectedTask, setSelectedTask] = useState<TaskItem | null>(null);
  const [tasksLimit, setTasksLimit] = useState(50);
  const [tasksLoading, setTasksLoading] = useState(false);
  const [tasksError, setTasksError] = useState<string | null>(null);

  const [executions, setExecutions] = useState<ExecutionItem[]>([]);
  const [selectedExecution, setSelectedExecution] = useState<ExecutionItem | null>(null);
  const [executionsLimit, setExecutionsLimit] = useState(50);
  const [executionsLoading, setExecutionsLoading] = useState(false);
  const [executionsError, setExecutionsError] = useState<string | null>(null);

  const [clio, setClio] = useState<ClioStatus | null>(null);
  const [clioLoading, setClioLoading] = useState(false);
  const [clioError, setClioError] = useState<string | null>(null);
  // Clio proposal staging state
  const [stagedClioLevel, setStagedClioLevel] = useState<string | null>(null);
  const [showClioConfirm, setShowClioConfirm] = useState(false);

  const [inventory, setInventory] = useState<InventoryEntry[]>([]);
  const [inventoryLoading, setInventoryLoading] = useState(false);
  const [inventoryError, setInventoryError] = useState<string | null>(null);

  const [configStatus, setConfigStatus] = useState<ConfigSnapshotStatus | null>(null);
  const [configLoading, setConfigLoading] = useState(false);
  const [configError, setConfigError] = useState<string | null>(null);
  const [configText, setConfigText] = useState<string>('{}');
  
  // Config Preview and Apply States
  const [previewResult, setPreviewResult] = useState<ConfigPreviewResult | null>(null);
  const [previewLoading, setPreviewLoading] = useState(false);
  const [previewError, setPreviewError] = useState<string | null>(null);

  const [applyResult, setApplyResult] = useState<ConfigApplyResult | null>(null);
  const [applyLoading, setApplyLoading] = useState(false);
  const [applyError, setApplyError] = useState<string | null>(null);
  const [applySuccess, setApplySuccess] = useState<boolean>(false);

  const isConfigEditable =
    configStatus !== null &&
    configStatus.available === true &&
    !!configStatus.current_hash &&
    !!csrfToken;

  // Helper to append auth and content headers
  const getHeaders = (includeCsrf = true) => {
    const headers: Record<string, string> = {
      'Content-Type': 'application/json',
      'Origin': window.location.origin
    };
    if (includeCsrf && csrfToken) {
      headers['X-Olimpo-CSRF-Token'] = csrfToken;
    }
    return headers;
  };

  // Fetch Health Overview
  const fetchHealth = async () => {
    setHealthLoading(true);
    setHealthError(null);
    try {
      const res = await fetch(`${apiBase}/olimpo/v0/health`);
      if (!res.ok) throw new Error(`HTTP ${res.status}: ${res.statusText}`);
      const data = await res.json();
      setHealth(data);
    } catch (err: any) {
      setHealthError(err.message || 'Falha ao conectar com o backend Olimpo.');
      setHealth(null);
    } finally {
      setHealthLoading(false);
    }
  };

  // Fetch Tasks list
  const fetchTasks = async (limit: number) => {
    setTasksLoading(true);
    setTasksError(null);
    try {
      const res = await fetch(`${apiBase}/olimpo/v0/tasks?limit=${limit}`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      setTasks(sanitizeData(data.items || []));
    } catch (err: any) {
      setTasksError('Falha ao buscar tarefas. Servidor offline.');
      setTasks([]);
    } finally {
      setTasksLoading(false);
    }
  };

  // Fetch Executions list
  const fetchExecutions = async (limit: number) => {
    setExecutionsLoading(true);
    setExecutionsError(null);
    try {
      const res = await fetch(`${apiBase}/olimpo/v0/executions?limit=${limit}`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      setExecutions(sanitizeData(data.items || []));
    } catch (err: any) {
      setExecutionsError('Falha ao buscar execuções. Servidor offline.');
      setExecutions([]);
    } finally {
      setExecutionsLoading(false);
    }
  };

  // Fetch Clio Status
  const fetchClioStatus = async () => {
    setClioLoading(true);
    setClioError(null);
    try {
      const res = await fetch(`${apiBase}/olimpo/v0/clio/status`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      setClio(data);
    } catch (err: any) {
      setClioError('Falha ao buscar Clio status.');
      setClio(null);
    } finally {
      setClioLoading(false);
    }
  };

  // Fetch Inventory
  const fetchInventory = async () => {
    setInventoryLoading(true);
    setInventoryError(null);
    try {
      const res = await fetch(`${apiBase}/olimpo/v0/inventory`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      setInventory(data.items || data);
    } catch (err: any) {
      setInventoryError('Falha ao obter inventário.');
      setInventory([]);
    } finally {
      setInventoryLoading(false);
    }
  };

  // Fetch current project config
  const fetchConfig = async () => {
    setConfigLoading(true);
    setConfigError(null);
    try {
      const res = await fetch(`${apiBase}/olimpo/v0/config`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      setConfigStatus(data);
      if (data.manifest) {
        setConfigText(JSON.stringify(data.manifest, null, 2));
      }
    } catch (err: any) {
      setConfigError('Falha ao carregar configuração do projeto.');
      setConfigStatus(null);
    } finally {
      setConfigLoading(false);
    }
  };

  // Initial load
  useEffect(() => {
    fetchHealth();
  }, []);

  // Sync tab loading
  useEffect(() => {
    if (activeTab === 'tasks') {
      fetchTasks(tasksLimit);
    } else if (activeTab === 'executions') {
      fetchExecutions(executionsLimit);
    } else if (activeTab === 'clio') {
      fetchClioStatus();
      setStagedClioLevel(null);
      setShowClioConfirm(false);
    } else if (activeTab === 'inventory') {
      fetchInventory();
    } else if (activeTab === 'config') {
      fetchConfig();
      setPreviewResult(null);
      setApplyResult(null);
      setApplySuccess(false);
    }
  }, [activeTab]);

  // Execute Config Preview
  const handlePreview = async () => {
    if (!csrfToken) {
      setPreviewError('Ações de gravação desabilitadas: gravação exige o host local de mesma origem oficial.');
      return;
    }
    setPreviewLoading(true);
    setPreviewError(null);
    setPreviewResult(null);
    setApplyResult(null);

    let parsedManifest = {};
    try {
      parsedManifest = JSON.parse(configText);
    } catch (e: any) {
      setPreviewError(`JSON Inválido: ${e.message}`);
      setPreviewLoading(false);
      return;
    }

    try {
      const res = await fetch(`${apiBase}/olimpo/v0/config/preview`, {
        method: 'POST',
        headers: getHeaders(),
        body: JSON.stringify({
          expected_hash: configStatus?.current_hash || null,
          manifest: parsedManifest
        })
      });

      if (!res.ok) {
        const errData = await res.json().catch(() => ({}));
        throw new Error(errData.reason_code || `HTTP ${res.status}`);
      }

      const data = await res.json();
      setPreviewResult(data);
    } catch (err: any) {
      setPreviewError(err.message || 'Falha ao validar preview.');
    } finally {
      setPreviewLoading(false);
    }
  };

  // Execute Config Apply
  const handleApply = async () => {
    if (!csrfToken) {
      setApplyError('Ações de gravação desabilitadas: gravação exige o host local de mesma origem oficial.');
      return;
    }
    if (!previewResult) {
      setApplyError('Você deve executar a validação de Preview primeiro.');
      return;
    }
    setApplyLoading(true);
    setApplyError(null);
    setApplySuccess(false);

    let parsedManifest = {};
    try {
      parsedManifest = JSON.parse(configText);
    } catch (e: any) {
      setApplyError(`JSON Inválido: ${e.message}`);
      setApplyLoading(false);
      return;
    }

    try {
      const res = await fetch(`${apiBase}/olimpo/v0/config/apply`, {
        method: 'POST',
        headers: getHeaders(),
        body: JSON.stringify({
          expected_hash: previewResult.current_hash || configStatus?.current_hash || '',
          manifest: parsedManifest
        })
      });

      const data = await res.json();

      if (!res.ok) {
        throw new Error(data.reason_code || 'OLIMPO_CONFIG_CONFLICT');
      }

      setApplyResult(data);
      setApplySuccess(true);
      // Refresh current config status
      await fetchConfig();
    } catch (err: any) {
      if (err.message === 'OLIMPO_CONFIG_CONFLICT') {
        setApplyError('Conflito detectado (CAS Conflict): O hash esperado não coincide com o estado atual do servidor.');
      } else {
        setApplyError(err.message || 'Falha ao aplicar configuração.');
      }
    } finally {
      setApplyLoading(false);
    }
  };

  // Handle Clio Level Proposal staging
  const stageClioProposal = (level: string) => {
    setStagedClioLevel(level);
    setShowClioConfirm(true);
  };

  const cancelClioProposal = () => {
    setStagedClioLevel(null);
    setShowClioConfirm(false);
  };

  // Check capability level rendering helper
  const renderMaturity = (state: CapabilityState | undefined | null) => {
    const activeState = state || 'unavailable';
    const labels: Record<CapabilityState, string> = {
      implemented: 'Implementado',
      unavailable: 'Indisponível',
      planned: 'Planejado'
    };
    return (
      <span className={`badge badge-${activeState}`} data-testid={`maturity-badge-${activeState}`}>
        {labels[activeState]}
      </span>
    );
  };

  return (
    <div className="app-container">
      {/* Responsive shell header */}
      <header className="header" role="banner">
        <div className="header-title-group">
          <span className="header-logo">Olimpo</span>
          <span className="header-tagline">Controle Local Observável (O-1)</span>
          {health && (
            <span style={{ fontSize: '0.8rem', opacity: 0.8 }} data-testid="package-version">
              v{health.package_version}
            </span>
          )}
        </div>
        
        <div style={{ display: 'flex', alignItems: 'center', gap: '1rem' }}>
          {healthLoading ? (
            <span style={{ fontSize: '0.85rem' }}>Verificando conexão...</span>
          ) : healthError ? (
            <span className="badge badge-unavailable">Modo Offline / Sem Conexão</span>
          ) : (
            <span className="badge badge-implemented">Online</span>
          )}

          <button
            onClick={() => setTheme(theme === 'light' ? 'dark' : 'light')}
            className="theme-toggle-btn"
            aria-label={`Mudar para modo ${theme === 'light' ? 'escuro' : 'claro'}`}
          >
            {theme === 'light' ? '🌙 Escuro' : '☀️ Claro'}
          </button>
        </div>
      </header>

      {/* CSRF Token Bootstrap Warn Banner */}
      {!csrfToken && (
        <div className="alert alert-warning" style={{ margin: 0, borderRadius: 0 }} role="status" data-testid="csrf-missing-warning">
          <strong>Aviso de Segurança:</strong> Ações de gravação exigem o host local de mesma origem oficial.
        </div>
      )}

      <div className="shell-layout">
        {/* Sidebar Nav */}
        <nav className="sidebar" aria-label="Navegação Principal">
          <button
            className={`nav-item ${activeTab === 'overview' ? 'active' : ''}`}
            onClick={() => setActiveTab('overview')}
          >
            📊 Visão Geral
          </button>
          <button
            className={`nav-item ${activeTab === 'tasks' ? 'active' : ''}`}
            onClick={() => setActiveTab('tasks')}
          >
            📋 Tarefas
          </button>
          <button
            className={`nav-item ${activeTab === 'executions' ? 'active' : ''}`}
            onClick={() => setActiveTab('executions')}
          >
            ⚙️ Execuções
          </button>
          <button
            className={`nav-item ${activeTab === 'clio' ? 'active' : ''}`}
            onClick={() => setActiveTab('clio')}
          >
            🧠 Estado Clio
          </button>
          <button
            className={`nav-item ${activeTab === 'inventory' ? 'active' : ''}`}
            onClick={() => setActiveTab('inventory')}
          >
            📦 Inventário
          </button>
          <button
            className={`nav-item ${activeTab === 'config' ? 'active' : ''}`}
            onClick={() => setActiveTab('config')}
          >
            🛠️ Configuração
          </button>
        </nav>

        {/* Main Workspace */}
        <main className="main-content" id="main-content" tabIndex={-1}>
          {/* TAB 1: Overview */}
          {activeTab === 'overview' && (
            <div data-testid="tab-overview">
              <h2>Maturidade das Funcionalidades</h2>
              <p style={{ color: 'var(--text-muted)', marginBottom: '1.5rem' }}>
                Status de conformidade do Athena com as especificações locais fechadas do Olimpo.
              </p>

              {healthLoading ? (
                <div className="spinner-container">
                  <div className="loading-spinner"></div>
                  Carregando informações de maturidade...
                </div>
              ) : (
                <div className="dashboard-grid">
                  <div className="card">
                    <h3>Status do Sistema</h3>
                    <div style={{ marginTop: '1rem', display: 'flex', flexDirection: 'column', gap: '0.75rem' }}>
                      <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                        <span>Versão do Contrato:</span>
                        <strong>{health?.schema_version || 'N/A'}</strong>
                      </div>
                      <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                        <span>Status do Adapter:</span>
                        <span>{renderMaturity(health?.adapter_status as any)}</span>
                      </div>
                    </div>
                  </div>

                  <div className="card">
                    <h3>Capacidades Athena (Maturity Matrix)</h3>
                    <div style={{ marginTop: '1rem', display: 'flex', flexDirection: 'column', gap: '0.5rem' }}>
                      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                        <span>Health Endpoint:</span>
                        {renderMaturity(health?.capabilities?.health)}
                      </div>
                      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                        <span>Visualização de Tarefas:</span>
                        {renderMaturity(health?.capabilities?.tasks)}
                      </div>
                      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                        <span>Visualização de Execuções:</span>
                        {renderMaturity(health?.capabilities?.executions)}
                      </div>
                      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                        <span>Status Clio:</span>
                        {renderMaturity(health?.capabilities?.clio)}
                      </div>
                      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                        <span>Inventário local:</span>
                        {renderMaturity(health?.capabilities?.inventory)}
                      </div>
                      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                        <span>Config Preview (Validação):</span>
                        {renderMaturity(health?.capabilities?.config_preview)}
                      </div>
                      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                        <span>Config Apply (Publicação):</span>
                        {renderMaturity(health?.capabilities?.config_apply)}
                      </div>
                      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                        <span>Painel Local Frontend:</span>
                        {renderMaturity(health?.capabilities?.frontend)}
                      </div>
                    </div>
                  </div>
                </div>
              )}
            </div>
          )}

          {/* TAB 2: Tasks */}
          {activeTab === 'tasks' && (
            <div data-testid="tab-tasks">
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '1rem', flexWrap: 'wrap', gap: '1rem' }}>
                <h2>Fila de Tarefas Local</h2>
                <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
                  <label htmlFor="tasks-limit">Limite:</label>
                  <select
                    id="tasks-limit"
                    value={tasksLimit}
                    onChange={(e) => {
                      const lim = Number(e.target.value);
                      setTasksLimit(lim);
                      fetchTasks(lim);
                    }}
                    className="form-input"
                    style={{ width: 'auto', padding: '0.25rem' }}
                  >
                    <option value="5">5</option>
                    <option value="20">20</option>
                    <option value="50">50</option>
                    <option value="100">100</option>
                    <option value="256">256 (Máx)</option>
                  </select>
                  <button onClick={() => fetchTasks(tasksLimit)} className="btn btn-secondary" style={{ padding: '0.25rem 0.5rem' }}>
                    🔄 Atualizar
                  </button>
                </div>
              </div>

              {tasksLoading ? (
                <div className="spinner-container">
                  <div className="loading-spinner"></div>
                  Carregando tarefas...
                </div>
              ) : tasksError ? (
                <div className="alert alert-danger">{tasksError}</div>
              ) : tasks.length === 0 ? (
                <div className="card" style={{ textAlign: 'center', padding: '3rem' }}>
                  <p style={{ color: 'var(--text-muted)' }}>Nenhuma tarefa encontrada no snapshot local.</p>
                </div>
              ) : (
                <div style={{ display: 'grid', gridTemplateColumns: selectedTask ? '1fr 1fr' : '1fr', gap: '1.5rem' }}>
                  <div className="table-container">
                    <table className="table" aria-label="Tabela de tarefas">
                      <thead>
                        <tr>
                          <th>Identificador (Handle)</th>
                          <th>Estado (State)</th>
                          <th>Criado Em</th>
                        </tr>
                      </thead>
                      <tbody>
                        {tasks.map((t) => (
                          <tr
                            key={t.task_handle}
                            onClick={() => setSelectedTask(t)}
                            style={{ cursor: 'pointer', backgroundColor: selectedTask?.task_handle === t.task_handle ? 'var(--accent-light)' : 'transparent' }}
                            tabIndex={0}
                            onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') setSelectedTask(t); }}
                          >
                            <td><strong>{t.task_handle}</strong></td>
                            <td>
                              <span className={`badge ${t.state === 'completed' ? 'badge-implemented' : 'badge-planned'}`}>
                                {t.state || 'unknown'}
                              </span>
                            </td>
                            <td>{t.created_at ? new Date(t.created_at).toLocaleString() : 'N/A'}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>

                  {selectedTask && (
                    <div className="card" data-testid="task-detail-pane">
                      <div className="card-title">
                        <span>Detalhes da Tarefa</span>
                        <button className="btn btn-secondary" style={{ padding: '0.1rem 0.4rem' }} onClick={() => setSelectedTask(null)}>
                          Fechar
                        </button>
                      </div>
                      
                      <div style={{ display: 'flex', flexDirection: 'column', gap: '0.75rem' }}>
                        <div>
                          <strong>Handle:</strong> <code>{selectedTask.task_handle}</code>
                        </div>
                        {selectedTask.task_type && (
                          <div>
                            <strong>Tipo de Tarefa (Task Type):</strong> {selectedTask.task_type}
                          </div>
                        )}
                        {selectedTask.state && (
                          <div>
                            <strong>Estado (State):</strong> {selectedTask.state}
                          </div>
                        )}
                        {selectedTask.priority !== undefined && (
                          <div>
                            <strong>Prioridade (Priority):</strong> {selectedTask.priority}
                          </div>
                        )}
                        {selectedTask.revision !== undefined && (
                          <div>
                            <strong>Revisão (Revision):</strong> {selectedTask.revision}
                          </div>
                        )}
                        {selectedTask.created_at && (
                          <div>
                            <strong>Criado Em (Created At):</strong> {selectedTask.created_at}
                          </div>
                        )}
                        {selectedTask.updated_at && (
                          <div>
                            <strong>Atualizado Em (Updated At):</strong> {selectedTask.updated_at}
                          </div>
                        )}
                        {selectedTask.execution_id && (
                          <div>
                            <strong>ID de Execução:</strong> <code>{selectedTask.execution_id}</code>
                          </div>
                        )}
                        {selectedTask.execution_status && (
                          <div>
                            <strong>Status de Execução:</strong> {selectedTask.execution_status}
                          </div>
                        )}
                        {selectedTask.validation_status && (
                          <div>
                            <strong>Status de Validação:</strong> {selectedTask.validation_status}
                          </div>
                        )}
                        {selectedTask.delivery_status && (
                          <div>
                            <strong>Status de Entrega:</strong> {selectedTask.delivery_status}
                          </div>
                        )}
                        {selectedTask.chronos_action && (
                          <div>
                            <strong>Ação Chronos:</strong> {selectedTask.chronos_action}
                          </div>
                        )}
                        {selectedTask.attempts_used !== undefined && (
                          <div>
                            <strong>Tentativas Usadas (Attempts Used):</strong> {selectedTask.attempts_used}
                          </div>
                        )}
                        {selectedTask.reason_codes && (
                          <div>
                            <strong>Códigos de Motivo (Reason Codes):</strong> {JSON.stringify(selectedTask.reason_codes)}
                          </div>
                        )}
                        <div>
                          <strong>Propriedades Sanitizadas (Redaction-Safe):</strong>
                          <pre className="code-block" style={{ marginTop: '0.5rem', maxHeight: '300px' }}>
                            {JSON.stringify(sanitizeData(selectedTask), null, 2)}
                          </pre>
                        </div>
                      </div>
                    </div>
                  )}
                </div>
              )}
            </div>
          )}

          {/* TAB 3: Executions */}
          {activeTab === 'executions' && (
            <div data-testid="tab-executions">
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '1rem', flexWrap: 'wrap', gap: '1rem' }}>
                <h2>Execuções Ativas e Passadas</h2>
                <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
                  <label htmlFor="execs-limit">Limite:</label>
                  <select
                    id="execs-limit"
                    value={executionsLimit}
                    onChange={(e) => {
                      const lim = Number(e.target.value);
                      setExecutionsLimit(lim);
                      fetchExecutions(lim);
                    }}
                    className="form-input"
                    style={{ width: 'auto', padding: '0.25rem' }}
                  >
                    <option value="5">5</option>
                    <option value="20">20</option>
                    <option value="50">50</option>
                    <option value="100">100</option>
                    <option value="256">256 (Máx)</option>
                  </select>
                  <button onClick={() => fetchExecutions(executionsLimit)} className="btn btn-secondary" style={{ padding: '0.25rem 0.5rem' }}>
                    🔄 Atualizar
                  </button>
                </div>
              </div>

              {executionsLoading ? (
                <div className="spinner-container">
                  <div className="loading-spinner"></div>
                  Carregando execuções...
                </div>
              ) : executionsError ? (
                <div className="alert alert-danger">{executionsError}</div>
              ) : executions.length === 0 ? (
                <div className="card" style={{ textAlign: 'center', padding: '3rem' }}>
                  <p style={{ color: 'var(--text-muted)' }}>Nenhuma execução registrada no snapshot.</p>
                </div>
              ) : (
                <div style={{ display: 'grid', gridTemplateColumns: selectedExecution ? '1fr 1fr' : '1fr', gap: '1.5rem' }}>
                  <div className="table-container">
                    <table className="table" aria-label="Tabela de execuções">
                      <thead>
                        <tr>
                          <th>ID da Execução</th>
                          <th>Request ID</th>
                          <th>Ferramenta (Tool)</th>
                          <th>Estado (State)</th>
                        </tr>
                      </thead>
                      <tbody>
                        {executions.map((ex) => (
                          <tr
                            key={ex.execution_id}
                            onClick={() => setSelectedExecution(ex)}
                            style={{ cursor: 'pointer', backgroundColor: selectedExecution?.execution_id === ex.execution_id ? 'var(--accent-light)' : 'transparent' }}
                            tabIndex={0}
                            onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') setSelectedExecution(ex); }}
                          >
                            <td><strong>{ex.execution_id}</strong></td>
                            <td><code>{ex.request_id || 'N/A'}</code></td>
                            <td><code>{ex.tool || 'N/A'}</code></td>
                            <td>
                              <span className={`badge ${ex.state === 'success' || ex.state === 'completed' ? 'badge-implemented' : 'badge-planned'}`}>
                                {ex.state || 'unknown'}
                              </span>
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>

                  {selectedExecution && (
                    <div className="card" data-testid="execution-detail-pane">
                      <div className="card-title">
                        <span>Detalhes da Execução</span>
                        <button className="btn btn-secondary" style={{ padding: '0.1rem 0.4rem' }} onClick={() => setSelectedExecution(null)}>
                          Fechar
                        </button>
                      </div>
                      
                      <div style={{ display: 'flex', flexDirection: 'column', gap: '0.75rem' }}>
                        <div>
                          <strong>ID da Execução:</strong> <code>{selectedExecution.execution_id}</code>
                        </div>
                        {selectedExecution.request_id && (
                          <div>
                            <strong>Request ID:</strong> <code>{selectedExecution.request_id}</code>
                          </div>
                        )}
                        {selectedExecution.tool && (
                          <div>
                            <strong>Ferramenta:</strong> <code>{selectedExecution.tool}</code>
                          </div>
                        )}
                        {selectedExecution.state && (
                          <div>
                            <strong>Estado (State):</strong> {selectedExecution.state}
                          </div>
                        )}
                        {selectedExecution.attempts !== undefined && (
                          <div>
                            <strong>Tentativas:</strong> {selectedExecution.attempts}
                          </div>
                        )}
                        {selectedExecution.current_attempt_id && (
                          <div>
                            <strong>ID da Tentativa Atual:</strong> <code>{selectedExecution.current_attempt_id}</code>
                          </div>
                        )}
                        {selectedExecution.finalized !== undefined && (
                          <div>
                            <strong>Finalizado:</strong> {selectedExecution.finalized ? 'Sim' : 'Não'}
                          </div>
                        )}
                        {selectedExecution.found !== undefined && (
                          <div>
                            <strong>Encontrado:</strong> {selectedExecution.found ? 'Sim' : 'Não'}
                          </div>
                        )}
                        {selectedExecution.requested !== undefined && (
                          <div>
                            <strong>Solicitado:</strong> {selectedExecution.requested ? 'Sim' : 'Não'}
                          </div>
                        )}
                        <div>
                          <strong>Propriedades Sanitizadas (Redaction-Safe):</strong>
                          <pre className="code-block" style={{ marginTop: '0.5rem', maxHeight: '300px' }}>
                            {JSON.stringify(sanitizeData(selectedExecution), null, 2)}
                          </pre>
                        </div>
                      </div>
                    </div>
                  )}
                </div>
              )}
            </div>
          )}

          {/* TAB 4: Clio Status & Proposal */}
          {activeTab === 'clio' && (
            <div data-testid="tab-clio">
              <h2>Estado Clio</h2>
              <p style={{ color: 'var(--text-muted)', marginBottom: '1.5rem' }}>
                O Olimpo monitora o nível atual do Clio. Mudanças de nível operam como proposta em estágio; o Olimpo nunca assume autoridade direta de runtime.
              </p>

              {clioLoading ? (
                <div className="spinner-container">
                  <div className="loading-spinner"></div>
                  Carregando estado do Clio...
                </div>
              ) : clioError ? (
                <div className="alert alert-danger" data-testid="clio-error">{clioError}</div>
              ) : (
                <div style={{ display: 'flex', flexDirection: 'column', gap: '1.5rem' }}>
                  <div className="card">
                    <h3>Nível do Clio Atual</h3>
                    <div style={{ marginTop: '1rem', display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '1rem' }}>
                      <div>
                        <strong>Nível Ativo:</strong>
                        <div style={{ fontSize: '1.25rem', fontWeight: 'bold', color: 'var(--accent-color)', marginTop: '0.25rem' }}>
                          {clio?.level === 'complete' ? 'Completo' : clio?.level === 'partial' ? 'Parcial' : clio?.level === 'technical' ? 'Técnico' : clio?.level === 'none' ? 'Nenhum' : (clio?.level || 'Nenhum')}
                        </div>
                      </div>
                      <div>
                        <strong>Mecanismo de Storage:</strong>
                        <div style={{ fontSize: '1.1rem', marginTop: '0.25rem' }}>
                          {clio?.storage || 'Desconhecido'}
                        </div>
                      </div>
                    </div>

                    {clio?.counters && (
                      <div style={{ marginTop: '1.5rem' }}>
                        <strong>Métricas e Contadores:</strong>
                        <div className="dashboard-grid" style={{ gridTemplateColumns: 'repeat(3, 1fr)', marginTop: '0.5rem', gap: '0.75rem' }}>
                          {Object.entries(clio.counters).map(([key, val]) => (
                            <div key={key} style={{ padding: '0.5rem', background: 'var(--bg-app)', border: '1px solid var(--border-color)', borderRadius: '4px', textAlign: 'center' }}>
                              <div style={{ fontSize: '0.8rem', color: 'var(--text-muted)' }}>{key}</div>
                              <div style={{ fontSize: '1.2rem', fontWeight: 'bold' }}>{val}</div>
                            </div>
                          ))}
                        </div>
                      </div>
                    )}
                  </div>

                  <div className="card">
                    <h3>Simulação e Proposta de Níveis Clio</h3>
                    <p style={{ fontSize: '0.9rem', color: 'var(--text-muted)', marginBottom: '1rem' }}>
                      Selecione um dos quatro níveis Clio suportados para submeter uma proposta de alteração.
                    </p>

                    <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(200px, 1fr))', gap: '1rem' }}>
                      {[
                        { level: 'complete', label: 'Completo' },
                        { level: 'partial', label: 'Parcial' },
                        { level: 'technical', label: 'Técnico' },
                        { level: 'none', label: 'Nenhum' }
                      ].map(({ level, label }) => {
                        const isCurrent = clio?.level === level;
                        return (
                          <button
                            key={level}
                            onClick={() => stageClioProposal(level)}
                            className="btn btn-secondary"
                            style={{
                              padding: '1rem',
                              flexDirection: 'column',
                              gap: '0.25rem',
                              borderColor: isCurrent ? 'var(--accent-color)' : 'var(--border-color)',
                              background: isCurrent ? 'var(--accent-light)' : 'transparent'
                            }}
                            data-testid={`clio-level-btn-${level}`}
                          >
                            <strong>{label}</strong>
                            <span style={{ fontSize: '0.75rem', fontWeight: 'normal' }}>
                              {isCurrent ? 'Nível Ativo' : 'Propor Nível'}
                            </span>
                          </button>
                        );
                      })}
                    </div>

                    {showClioConfirm && (
                      <div style={{ marginTop: '1.5rem', padding: '1rem', border: '1px solid var(--border-color)', borderRadius: '6px' }} data-testid="clio-proposal-box">
                        <h4>📋 Proposta de Nível Clio Local (Rascunho)</h4>
                        <p style={{ fontSize: '0.9rem', margin: '0.5rem 0' }}>
                          Nível Proposto: <strong>{stagedClioLevel === 'complete' ? 'Completo' : stagedClioLevel === 'partial' ? 'Parcial' : stagedClioLevel === 'technical' ? 'Técnico' : stagedClioLevel === 'none' ? 'Nenhum' : stagedClioLevel}</strong> (Apenas para exibição/exportação local).
                        </p>
                        <div style={{ display: 'flex', gap: '0.5rem', marginTop: '0.75rem' }}>
                          <button
                            disabled
                            className="btn btn-secondary"
                            style={{ cursor: 'not-allowed' }}
                            data-testid="clio-confirm-btn"
                          >
                            Execução Indisponível (Planejado)
                          </button>
                          <button onClick={cancelClioProposal} className="btn btn-secondary" style={{ padding: '0.25rem 0.75rem' }} data-testid="clio-cancel-btn">
                            Limpar
                          </button>
                        </div>
                      </div>
                    )}
                  </div>
                </div>
              )}
            </div>
          )}

          {/* TAB 5: Inventory */}
          {activeTab === 'inventory' && (
            <div data-testid="tab-inventory">
              <h2>Inventário Local (Read-Only)</h2>
              <p style={{ color: 'var(--text-muted)', marginBottom: '1.5rem' }}>
                Lista oficial de provedores, funções e modelos locais integrados ao snapshot Olimpo.
              </p>

              {inventoryLoading ? (
                <div className="spinner-container">
                  <div className="loading-spinner"></div>
                  Carregando inventário...
                </div>
              ) : inventoryError ? (
                <div className="alert alert-danger">{inventoryError}</div>
              ) : inventory.length === 0 ? (
                <div className="card" style={{ textAlign: 'center', padding: '3rem' }}>
                  <p style={{ color: 'var(--text-muted)' }}>Nenhum item encontrado no inventário.</p>
                </div>
              ) : (
                <div className="table-container">
                  <table className="table" aria-label="Tabela de Inventário">
                    <thead>
                      <tr>
                        <th>Provider</th>
                        <th>Function ID</th>
                        <th>Modo</th>
                        <th>Modelo Padrão</th>
                        <th>Disponibilidade</th>
                      </tr>
                    </thead>
                    <tbody>
                      {inventory.map((item, idx) => (
                        <tr key={idx}>
                          <td><strong>{item.provider_id || 'N/A'}</strong></td>
                          <td><code>{item.function_id || 'N/A'}</code></td>
                          <td>{item.mode || 'N/A'}</td>
                          <td><code>{item.default_model || 'N/A'}</code></td>
                          <td>{renderMaturity(item.availability)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </div>
          )}

          {/* TAB 6: Config Editor (Atomic Compare-and-Swap Manager) */}
          {activeTab === 'config' && (
            <div data-testid="tab-config">
              <h2>Configuração do Projeto</h2>
              <p style={{ color: 'var(--text-muted)', marginBottom: '1.5rem' }}>
                Altere manifestos de configuração local de forma atômica por meio de Preview & Apply.
              </p>

              {configLoading ? (
                <div className="spinner-container">
                  <div className="loading-spinner"></div>
                  Carregando configuração...
                </div>
              ) : configError ? (
                <div className="alert alert-danger" data-testid="config-error">{configError}</div>
              ) : (
                <div style={{ display: 'flex', flexDirection: 'column', gap: '1.5rem' }}>
                  
                  {/* Current CAS Metadata */}
                  <div className="card">
                    <h3>Identificadores Atuais</h3>
                    <div style={{ marginTop: '0.75rem', display: 'flex', flexDirection: 'column', gap: '0.5rem' }}>
                      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                        <span>Hash do Snapshot Atual:</span>
                        <code style={{ fontSize: '0.9rem', wordBreak: 'break-all' }} data-testid="current-hash">
                          {configStatus?.current_hash || 'Nenhum hash carregado'}
                        </code>
                      </div>
                      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                        <span>Schema Version:</span>
                        <span>{configStatus?.schema_version || 'N/A'}</span>
                      </div>
                    </div>
                  </div>

                  {/* Config Text Editor */}
                  <div className="card">
                    <h3>Editor de Manifesto</h3>
                    
                    <div className="form-group" style={{ marginTop: '1rem' }}>
                      <label className="form-label" htmlFor="manifest-editor">Configuração JSON:</label>
                      <textarea
                        id="manifest-editor"
                        className="form-textarea"
                        value={configText}
                        onChange={(e) => setConfigText(e.target.value)}
                        data-testid="manifest-textarea"
                      />
                    </div>

                    <div style={{ display: 'flex', gap: '0.75rem' }}>
                      <button
                        onClick={handlePreview}
                        disabled={previewLoading || !isConfigEditable}
                        className="btn btn-primary"
                        data-testid="preview-btn"
                      >
                        {previewLoading ? 'Validando...' : '🔍 Validar Alterações (Preview First)'}
                      </button>

                      <button
                        onClick={handleApply}
                        disabled={applyLoading || !previewResult || !isConfigEditable}
                        className="btn btn-secondary"
                        style={{
                          borderColor: previewResult?.ok ? 'var(--success-color)' : 'var(--border-color)',
                          background: previewResult?.ok ? 'var(--success-bg)' : 'transparent',
                          color: previewResult?.ok ? 'var(--success-color)' : 'var(--text-main)'
                        }}
                        data-testid="apply-btn"
                      >
                        {applyLoading ? 'Aplicando...' : '💾 Aplicar Configuração (Apply)'}
                      </button>
                    </div>

                    {/* Preview Messages and Validation Status */}
                    {previewError && (
                      <div className="alert alert-danger" style={{ marginTop: '1rem' }} data-testid="preview-error">
                        {previewError}
                      </div>
                    )}

                    {previewResult && (
                      <div style={{ marginTop: '1.5rem', borderTop: '1px solid var(--border-color)', paddingTop: '1rem' }}>
                        <h3>Resultado da Validação</h3>
                        
                        <div style={{ marginTop: '0.5rem', display: 'flex', flexDirection: 'column', gap: '0.5rem' }}>
                          <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                            <span>Status da Validação:</span>
                            <span className={`badge ${previewResult.ok ? 'badge-implemented' : 'badge-unavailable'}`}>
                              {previewResult.ok ? 'Válido' : 'Falhou'}
                            </span>
                          </div>
                          <div>
                            <strong>Hash Proposto:</strong> <code>{previewResult.proposed_hash || 'N/A'}</code>
                          </div>
                        </div>

                        {/* Staged Changes / Diff List */}
                        {previewResult.changes && previewResult.changes.length > 0 && (
                          <div style={{ marginTop: '1rem' }}>
                            <strong>Lista de Alterações Sanitizadas:</strong>
                            <ul style={{ paddingLeft: '1.25rem', marginTop: '0.25rem' }}>
                              {previewResult.changes.map((change, index) => (
                                <li key={index} style={{ fontSize: '0.9rem' }}>{change}</li>
                              ))}
                            </ul>
                          </div>
                        )}
                      </div>
                    )}

                    {/* Apply Messages */}
                    {applyError && (
                      <div className="alert alert-danger" style={{ marginTop: '1rem' }} data-testid="apply-error">
                        {applyError}
                      </div>
                    )}

                    {applySuccess && (
                      <div className="alert alert-success" style={{ marginTop: '1rem' }} data-testid="apply-success">
                        <strong>Configuração aplicada com sucesso!</strong> Nova configuração e hash publicados atomicamente.
                        {applyResult?.applied_hash && (
                          <div style={{ fontSize: '0.85rem', marginTop: '0.25rem' }}>
                            Hash Aplicado: <code>{applyResult.applied_hash}</code>
                          </div>
                        )}
                      </div>
                    )}
                  </div>
                </div>
              )}
            </div>
          )}
        </main>
      </div>
    </div>
  );
}
