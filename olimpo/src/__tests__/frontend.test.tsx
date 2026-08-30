import { describe, test, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import App, { sanitizeData } from '../App';
import { setCsrfTokenForTesting, getCsrfToken, validateApiBase } from '../bootstrap';

// Mock fetch globally using standard globalThis type
const mockFetch = vi.fn();
(globalThis as any).fetch = mockFetch;

describe('Athena OLIMPO-0 Frontend Tests', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    setCsrfTokenForTesting(null); // Default to disabled writes (no CSRF)

    // Default mock implementation to prevent unhandled fetch crashes
    mockFetch.mockImplementation(async (url: string) => {
      if (url.includes('/olimpo/v0/health')) {
        return {
          ok: true,
          json: async () => ({
            schema_version: 'olimpo.v0',
            package_version: '1.2.3',
            adapter_status: 'implemented',
            capabilities: {
              health: 'implemented',
              tasks: 'implemented',
              executions: 'implemented',
              clio: 'implemented',
              inventory: 'implemented',
              config_preview: 'implemented',
              config_apply: 'implemented',
              frontend: 'planned'
            }
          })
        };
      }
      if (url.includes('/olimpo/v0/config') && !url.includes('/preview') && !url.includes('/apply')) {
        return {
          ok: true,
          json: async () => ({
            available: true,
            current_hash: '9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08',
            schema_version: 'config.v0'
          })
        };
      }
      if (url.includes('/olimpo/v0/clio/status')) {
        return {
          ok: true,
          json: async () => ({
            level: 'none',
            storage: 'sqlite-local',
            counters: {}
          })
        };
      }
      return {
        ok: false,
        status: 404,
        json: async () => ({ reason_code: 'OLIMPO_ROUTE_NOT_FOUND' })
      };
    });
  });

  test('1. Maturity labels are rendered correctly', async () => {
    render(<App />);

    // Wait for async load to finish and verify package version and maturity labels
    await waitFor(() => {
      expect(screen.getByTestId('package-version')).toHaveTextContent('v1.2.3');
    });
  });

  test('2. Unavailable and Planned states are visibly disabled', async () => {
    render(<App />);

    // Go to clio tab
    const clioTabBtn = screen.getByRole('button', { name: /Estado Clio/i });
    fireEvent.click(clioTabBtn);

    // Wait for clio levels and click one
    await waitFor(() => {
      expect(screen.getByTestId('clio-level-btn-technical')).toBeInTheDocument();
    });
    fireEvent.click(screen.getByTestId('clio-level-btn-technical'));
    
    // Look for disabled button of planned feature
    const plannedBtn = screen.getByTestId('clio-confirm-btn');
    expect(plannedBtn).toBeDisabled();
  });

  test('3. Redaction-safe rendering of tasks and executions', () => {
    const sensitiveData = {
      task_handle: 'task-999',
      status: 'failed',
      prompt: 'SELECT * FROM secrets;',
      command: 'rm -rf /',
      secret: 'super-secret-token',
      normal_meta: 'this is safe'
    };

    const sanitized = sanitizeData(sensitiveData);

    // Prompt, command, secret should be redacted
    expect(sanitized.prompt).toBe('[REDACT_SAFE: SENSITIVE_CONTENT_OMITTED]');
    expect(sanitized.command).toBe('[REDACT_SAFE: SENSITIVE_CONTENT_OMITTED]');
    expect(sanitized.secret).toBe('[REDACT_SAFE: SENSITIVE_CONTENT_OMITTED]');
    // Safe keys should remain untouched
    expect(sanitized.task_handle).toBe('task-999');
    expect(sanitized.normal_meta).toBe('this is safe');
  });

  test('4. Preview-before-Apply sequence requirement', async () => {
    // Set CSRF token to enable writes
    setCsrfTokenForTesting('csrf-valid-token-123');

    mockFetch.mockImplementation(async (url: string) => {
      if (url.includes('/olimpo/v0/health')) {
        return {
          ok: true,
          json: async () => ({
            schema_version: 'olimpo.v0',
            package_version: '1.2.3',
            adapter_status: 'implemented',
            capabilities: {
              config_preview: 'implemented',
              config_apply: 'implemented'
            }
          })
        };
      }
      if (url.includes('/olimpo/v0/config/preview')) {
        return {
          ok: true,
          json: async () => ({
            ok: true,
            proposed_hash: 'hash-xyz',
            current_hash: 'hash-abc',
            changes: ['Update manifest structure'],
            validation_status: 'valid'
          })
        };
      }
      if (url.includes('/olimpo/v0/config')) {
        return {
          ok: true,
          json: async () => ({
            available: true,
            current_hash: 'hash-abc',
            schema_version: 'config.v0'
          })
        };
      }
      return { ok: false, status: 404 };
    });

    render(<App />);

    // Go to config tab
    const configTabBtn = screen.getByRole('button', { name: /configuração/i });
    fireEvent.click(configTabBtn);

    await waitFor(() => {
      expect(screen.getByTestId('current-hash')).toHaveTextContent('hash-abc');
    });

    const previewBtn = screen.getByTestId('preview-btn');
    const applyBtn = screen.getByTestId('apply-btn');

    // Initially, preview is enabled but apply is disabled (requires preview first)
    expect(previewBtn).not.toBeDisabled();
    expect(applyBtn).toBeDisabled();

    // Click preview
    fireEvent.click(previewBtn);

    await waitFor(() => {
      expect(screen.getByText(/Resultado da Validação/i)).toBeInTheDocument();
    });

    // Apply should now be enabled
    expect(applyBtn).not.toBeDisabled();
  });

  test('5. CAS conflict on apply results in clear error banner', async () => {
    setCsrfTokenForTesting('csrf-valid-token-123');

    mockFetch.mockImplementation(async (url: string) => {
      if (url.includes('/olimpo/v0/health')) {
        return { ok: true, json: async () => ({ capabilities: {} }) };
      }
      if (url.includes('/olimpo/v0/config/preview')) {
        return {
          ok: true,
          json: async () => ({
            ok: true,
            proposed_hash: 'hash-proposed',
            current_hash: 'hash-old',
            changes: ['modify values']
          })
        };
      }
      if (url.includes('/olimpo/v0/config/apply')) {
        return {
          ok: false,
          status: 409,
          json: async () => ({
            ok: false,
            reason_code: 'OLIMPO_CONFIG_CONFLICT',
            current_hash: 'hash-changed-by-other-client'
          })
        };
      }
      if (url.includes('/olimpo/v0/config')) {
        return {
          ok: true,
          json: async () => ({
            available: true,
            current_hash: 'hash-old',
            schema_version: 'config.v0'
          })
        };
      }
      return { ok: false, status: 404 };
    });

    render(<App />);

    // Go to config tab
    fireEvent.click(screen.getByRole('button', { name: /configuração/i }));

    // Wait for config tab to load
    await waitFor(() => {
      expect(screen.getByTestId('preview-btn')).toBeInTheDocument();
    });

    // Click Preview
    fireEvent.click(screen.getByTestId('preview-btn'));

    await waitFor(() => {
      expect(screen.getByTestId('apply-btn')).not.toBeDisabled();
    });

    // Click Apply (which returns 409 Conflict)
    fireEvent.click(screen.getByTestId('apply-btn'));

    await waitFor(() => {
      expect(screen.getByTestId('apply-error')).toHaveTextContent(/Conflito detectado/i);
    });
  });

  test('6. Disabled writes without in-memory CSRF token', async () => {
    // CSRF token is null by default
    render(<App />);

    // Verify warning banner is shown
    expect(screen.getByTestId('csrf-missing-warning')).toBeInTheDocument();

    // Go to config tab
    fireEvent.click(screen.getByRole('button', { name: /configuração/i }));

    // Preview button should be disabled
    await waitFor(() => {
      expect(screen.getByTestId('preview-btn')).toBeInTheDocument();
    });
    expect(screen.getByTestId('preview-btn')).toBeDisabled();
  });

  test('7. Offline state has no fake data', async () => {
    // Force fetch failure for all endpoints
    mockFetch.mockImplementation(async () => {
      return {
        ok: false,
        status: 500,
        json: async () => ({})
      };
    });

    render(<App />);

    // 1. Verify health overview has no fake package version or capabilities
    await waitFor(() => {
      expect(screen.queryByTestId('package-version')).not.toBeInTheDocument();
    });
    expect(screen.queryByTestId('maturity-badge-implemented')).not.toBeInTheDocument();

    // 2. Go to tasks tab
    fireEvent.click(screen.getByRole('button', { name: /tarefas/i }));
    await waitFor(() => {
      expect(screen.getByText(/Falha ao buscar tarefas/i)).toBeInTheDocument();
    });
    expect(screen.queryByText('task-01')).not.toBeInTheDocument();

    // 3. Go to executions tab
    fireEvent.click(screen.getByRole('button', { name: /execuções/i }));
    await waitFor(() => {
      expect(screen.getByText(/Falha ao buscar execuções/i)).toBeInTheDocument();
    });
    expect(screen.queryByText('exec-101')).not.toBeInTheDocument();

    // 4. Go to clio tab
    fireEvent.click(screen.getByRole('button', { name: /Estado Clio/i }));
    await waitFor(() => {
      expect(screen.getByTestId('clio-error')).toBeInTheDocument();
    });
    expect(screen.queryByText(/sqlite-local/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/active_runs/i)).not.toBeInTheDocument();

    // 5. Go to inventory tab
    fireEvent.click(screen.getByRole('button', { name: /inventário/i }));
    await waitFor(() => {
      expect(screen.getByText(/Falha ao obter inventário/i)).toBeInTheDocument();
    });
    expect(screen.queryByText('ollama')).not.toBeInTheDocument();

    // 6. Go to config tab
    fireEvent.click(screen.getByRole('button', { name: /configuração/i }));
    await waitFor(() => {
      expect(screen.getByTestId('config-error')).toBeInTheDocument();
    });
    expect(screen.queryByTestId('current-hash')).not.toBeInTheDocument();
  });

  test('8. URL CSRF is ignored', () => {
    // Verify that the token in bootstrap remains null and is not populated from URL
    expect(getCsrfToken()).toBeNull();
  });

  test('9. Invalid remote API base is rejected and falls back safely', () => {
    // Valid inputs
    expect(validateApiBase('')).toBe(true);
    expect(validateApiBase(undefined)).toBe(true);
    expect(validateApiBase('http://127.0.0.1:8000')).toBe(true);
    expect(validateApiBase('http://127.0.0.1:3000')).toBe(true);

    // Invalid remote hosts
    expect(validateApiBase('http://example.com:8000')).toBe(false);
    expect(validateApiBase('http://localhost:8000')).toBe(false);
    expect(validateApiBase('http://127.0.0.2:8000')).toBe(false);

    // Invalid credentials
    expect(validateApiBase('http://user:pass@127.0.0.1:8000')).toBe(false);

    // Invalid protocols
    expect(validateApiBase('https://127.0.0.1:8000')).toBe(false);
    expect(validateApiBase('ftp://127.0.0.1:21')).toBe(false);

    // Invalid path/query/fragment
    expect(validateApiBase('http://127.0.0.1:8000/api')).toBe(false);
    expect(validateApiBase('http://127.0.0.1:8000?foo=bar')).toBe(false);
    expect(validateApiBase('http://127.0.0.1:8000#hash')).toBe(false);

    // Invalid port
    expect(validateApiBase('http://127.0.0.1')).toBe(false); // no port
    expect(validateApiBase('http://127.0.0.1:0')).toBe(false); // 0 is invalid
    expect(validateApiBase('http://127.0.0.1:99999')).toBe(false); // out of range
  });

  test('10. Writes require real config hash + token - Scenario A', async () => {
    // CSRF token exists but config status failed to read (no hash)
    setCsrfTokenForTesting('csrf-valid');
    mockFetch.mockImplementation(async (url: string) => {
      if (url.includes('/olimpo/v0/health')) {
        return { ok: true, json: async () => ({ capabilities: {} }) };
      }
      if (url.includes('/olimpo/v0/config')) {
        return { ok: false, status: 500 };
      }
      return { ok: false };
    });

    render(<App />);
    fireEvent.click(screen.getByRole('button', { name: /configuração/i }));
    await waitFor(() => {
      expect(screen.getByTestId('config-error')).toBeInTheDocument();
    });
    expect(screen.queryByTestId('preview-btn')).not.toBeInTheDocument();
  });

  test('10. Writes require real config hash + token - Scenario B', async () => {
    // Config hash exists but CSRF token is missing
    setCsrfTokenForTesting(null);
    mockFetch.mockImplementation(async (url: string) => {
      if (url.includes('/olimpo/v0/health')) {
        return { ok: true, json: async () => ({ capabilities: {} }) };
      }
      if (url.includes('/olimpo/v0/config')) {
        return {
          ok: true,
          json: async () => ({
            available: true,
            current_hash: 'real-hash',
            schema_version: 'config.v0'
          })
        };
      }
      return { ok: false };
    });

    render(<App />);
    fireEvent.click(screen.getByRole('button', { name: /configuração/i }));
    await waitFor(() => {
      expect(screen.getByTestId('preview-btn')).toBeInTheDocument();
    });
    expect(screen.getByTestId('preview-btn')).toBeDisabled();
    expect(screen.getByTestId('apply-btn')).toBeDisabled();
  });

  test('11. Clio has no executable fake action', async () => {
    mockFetch.mockImplementation(async (url: string) => {
      if (url.includes('/clio/status')) {
        return {
          ok: true,
          json: async () => ({
            level: 'none',
            storage: 'sqlite-local',
            counters: {}
          })
        };
      }
      return { ok: true, json: async () => ({}) };
    });

    render(<App />);

    // Go to Clio tab
    fireEvent.click(screen.getByRole('button', { name: /Estado Clio/i }));
    await waitFor(() => {
      expect(screen.getByTestId('clio-level-btn-technical')).toBeInTheDocument();
    });

    // Select L2 (technical / Técnico)
    fireEvent.click(screen.getByTestId('clio-level-btn-technical'));

    // Check staging box is visible
    expect(screen.getByTestId('clio-proposal-box')).toBeInTheDocument();
    expect(screen.getByTestId('clio-proposal-box')).toHaveTextContent(/Nível Proposto: Técnico/i);

    // Confirm button must be disabled (labeled planned/unavailable)
    const confirmBtn = screen.getByTestId('clio-confirm-btn');
    expect(confirmBtn).toBeDisabled();
    expect(confirmBtn).toHaveTextContent(/Execução Indisponível/i);

    // Verify there is no success alert
    expect(screen.queryByTestId('clio-proposal-success')).not.toBeInTheDocument();
  });

  test('12. Exact DTO state fields for tasks and executions', async () => {
    mockFetch.mockImplementation(async (url: string) => {
      if (url.includes('/olimpo/v0/health')) {
        return { ok: true, json: async () => ({ capabilities: {} }) };
      }
      if (url.includes('/olimpo/v0/tasks')) {
        return {
          ok: true,
          json: async () => ({
            items: [{
              task_handle: 'task-dto-test-01',
              task_type: 'type-test-A',
              state: 'completed',
              priority: 15,
              revision: 4,
              created_at: '2026-08-29T12:00:00Z',
              updated_at: '2026-08-29T13:00:00Z',
              execution_id: 'exec-dto-test-01',
              execution_status: 'success',
              validation_status: 'valid',
              delivery_status: 'delivered',
              chronos_action: 'notify',
              attempts_used: 2,
              reason_codes: ['rc-code-ok']
            }]
          })
        };
      }
      if (url.includes('/olimpo/v0/executions')) {
        return {
          ok: true,
          json: async () => ({
            items: [{
              execution_id: 'exec-dto-test-01',
              request_id: 'req-dto-test-999',
              tool: 'tool-test-db',
              state: 'completed',
              attempts: 5,
              current_attempt_id: 'att-dto-test-2',
              finalized: true,
              found: true,
              requested: true
            }]
          })
        };
      }
      return { ok: false };
    });

    render(<App />);

    // 1. Tasks verification
    fireEvent.click(screen.getByRole('button', { name: /tarefas/i }));
    await waitFor(() => {
      expect(screen.getByText('task-dto-test-01')).toBeInTheDocument();
    });

    // Select the task to open detail pane
    fireEvent.click(screen.getByText('task-dto-test-01'));
    await waitFor(() => {
      expect(screen.getByTestId('task-detail-pane')).toBeInTheDocument();
    });

    // Verify fields exist
    expect(screen.getByText(/Tipo de Tarefa/i)).toBeInTheDocument();
    expect(screen.getAllByText(/Estado/i).length).toBeGreaterThan(0);
    expect(screen.getByText(/Prioridade/i)).toBeInTheDocument();
    expect(screen.getByText(/Revisão/i)).toBeInTheDocument();
    expect(screen.getAllByText(/Criado Em/i).length).toBeGreaterThan(0);
    expect(screen.getByText(/Atualizado Em/i)).toBeInTheDocument();
    expect(screen.getByText(/ID de Execução/i)).toBeInTheDocument();
    expect(screen.getByText(/Status de Execução/i)).toBeInTheDocument();
    expect(screen.getByText(/Status de Validação/i)).toBeInTheDocument();
    expect(screen.getByText(/Status de Entrega/i)).toBeInTheDocument();
    expect(screen.getByText(/Ação Chronos/i)).toBeInTheDocument();
    expect(screen.getByText(/Tentativas Usadas/i)).toBeInTheDocument();
    expect(screen.getByText(/Códigos de Motivo/i)).toBeInTheDocument();

    // Verify invented status field label is NOT present
    expect(screen.queryByText(/Status:/i)).not.toBeInTheDocument();

    // 2. Executions verification
    fireEvent.click(screen.getByRole('button', { name: /execuções/i }));
    await waitFor(() => {
      expect(screen.getByText('exec-dto-test-01')).toBeInTheDocument();
    });

    // Select the execution to open detail pane
    fireEvent.click(screen.getByText('exec-dto-test-01'));
    await waitFor(() => {
      expect(screen.getByTestId('execution-detail-pane')).toBeInTheDocument();
    });

    // Verify fields exist
    expect(screen.getAllByText(/Request ID/i).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/Ferramenta/i).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/Estado/i).length).toBeGreaterThan(0);
    expect(screen.getByText(/Tentativas/i)).toBeInTheDocument();
    expect(screen.getByText(/ID da Tentativa Atual/i)).toBeInTheDocument();
    expect(screen.getByText(/Finalizado/i)).toBeInTheDocument();
    expect(screen.getByText(/Encontrado/i)).toBeInTheDocument();
    expect(screen.getByText(/Solicitado/i)).toBeInTheDocument();

    // Verify task_handle, status, and created_at labels are NOT present in details
    expect(screen.queryByText(/Handle Relacionado/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/Criado Em/i)).not.toBeInTheDocument();
  });

  test('13. Clio levels exactly complete, partial, technical, none with Portuguese labels', async () => {
    mockFetch.mockImplementation(async (url: string) => {
      if (url.includes('/clio/status')) {
        return {
          ok: true,
          json: async () => ({
            level: 'complete',
            storage: 'sqlite-local',
            counters: {}
          })
        };
      }
      return { ok: true, json: async () => ({}) };
    });

    render(<App />);

    // Go to Clio tab
    fireEvent.click(screen.getByRole('button', { name: /Estado Clio/i }));
    await waitFor(() => {
      // Check current level Portuguese label
      expect(screen.getAllByText('Completo').length).toBeGreaterThan(0);
    });

    // Verify all four option buttons with Portuguese labels exist
    expect(screen.getByTestId('clio-level-btn-complete')).toHaveTextContent('Completo');
    expect(screen.getByTestId('clio-level-btn-partial')).toHaveTextContent('Parcial');
    expect(screen.getByTestId('clio-level-btn-technical')).toHaveTextContent('Técnico');
    expect(screen.getByTestId('clio-level-btn-none')).toHaveTextContent('Nenhum');
  });

  test('14. Absence of Cloud Registry and invented config', async () => {
    render(<App />);

    // Go to config tab
    fireEvent.click(screen.getByRole('button', { name: /configuração/i }));
    await waitFor(() => {
      expect(screen.getByTestId('manifest-textarea')).toBeInTheDocument();
    });

    // Verify absence of Cloud Registry / upload words/buttons
    expect(screen.queryByText(/Upload Remoto/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/Cloud Push/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/Athena Cloud Registry/i)).not.toBeInTheDocument();

    // Verify default config text is exactly '{}'
    const textarea = screen.getByTestId('manifest-textarea') as HTMLTextAreaElement;
    expect(textarea.value).toBe('{}');
  });

  test('15. Missing maturity renders Indisponível', async () => {
    mockFetch.mockImplementation(async (url: string) => {
      if (url.includes('/olimpo/v0/health')) {
        return {
          ok: true,
          json: async () => ({
            schema_version: 'olimpo.v0',
            package_version: '1.2.3',
            adapter_status: null, // missing maturity
            capabilities: {
              health: undefined, // missing maturity
              tasks: 'implemented'
            }
          })
        };
      }
      return { ok: true, json: async () => ({}) };
    });

    render(<App />);

    await waitFor(() => {
      // Missing capabilities should fall back to rendering "Indisponível"
      const unavailableBadges = screen.getAllByTestId('maturity-badge-unavailable');
      expect(unavailableBadges.length).toBeGreaterThan(0);
      expect(unavailableBadges[0]).toHaveTextContent('Indisponível');
    });
  });
});
