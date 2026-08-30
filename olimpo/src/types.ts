export type CapabilityState = 'implemented' | 'unavailable' | 'planned';

export interface CapabilityStatus {
  health: CapabilityState;
  tasks: CapabilityState;
  executions: CapabilityState;
  clio: CapabilityState;
  inventory: CapabilityState;
  config_preview: CapabilityState;
  config_apply: CapabilityState;
  frontend: CapabilityState;
}

export interface HealthStatus {
  schema_version: string;
  package_version: string;
  adapter_status: string;
  capabilities: CapabilityStatus;
}

export interface ClioStatus {
  level: string;
  storage: string;
  counters: Record<string, number>;
}

export interface InventoryEntry {
  provider_id?: string;
  function_id?: string;
  mode?: string;
  runtime_class?: string;
  enabled?: boolean;
  approved?: boolean;
  default_model?: string;
  specialist?: string;
  version?: string;
  min_status?: string;
  observed_discovered?: boolean;
  observed_healthy?: boolean;
  availability: CapabilityState;
}

export interface ConfigSnapshotStatus {
  available: boolean;
  current_hash?: string;
  schema_version?: string;
}

export interface ConfigPreviewResult {
  ok: boolean;
  reason_code?: string;
  current_hash?: string;
  proposed_hash?: string;
  changes: string[];
  validation_status?: string;
}

export interface ConfigApplyResult {
  ok: boolean;
  reason_code?: string;
  applied_hash?: string;
  current_hash?: string;
}

export interface TaskItem {
  task_handle: string;
  task_type?: string;
  state?: string;
  priority?: number;
  revision?: number;
  created_at?: string;
  updated_at?: string;
  execution_id?: string;
  execution_status?: string;
  validation_status?: string;
  delivery_status?: string;
  chronos_action?: string;
  attempts_used?: number;
  reason_codes?: string[];
  [key: string]: any;
}

export interface ExecutionItem {
  execution_id: string;
  request_id?: string;
  tool?: string;
  state?: string;
  attempts?: number;
  current_attempt_id?: string;
  finalized?: boolean;
  found?: boolean;
  requested?: boolean;
  [key: string]: any;
}
