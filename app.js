/**
 * Clinical Documentation Agent — Interactive Testing Studio & Harness
 */

const $ = (id) => document.getElementById(id);

// Application State
const state = {
  scenarios: [],
  activeScenarioId: 'scenario-1',
  currentScenarioData: null,
  activePatientData: null,
  allGuidelines: [],
  customTurns: [],
  lastAgentOutput: null,
  toolCallCount: 0,
  revisionCount: 0,
};

// Utilities
function escapeHtml(s) {
  return String(s ?? '').replace(/[&<>'"]/g, c => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;'
  }[c]));
}

function showModal(id) {
  const el = $(id);
  if (el) el.classList.remove('hidden');
}

function hideModal(id) {
  const el = $(id);
  if (el) el.classList.add('hidden');
}

// --------------------------------------------------------------------------
// Initialization & Scenario Loading
// --------------------------------------------------------------------------
async function initApp() {
  try {
    const [scenRes, guideRes] = await Promise.all([
      fetch('/api/scenarios'),
      fetch('/api/guidelines')
    ]);

    if (scenRes.ok) {
      const data = await scenRes.json();
      state.scenarios = data.scenarios || [];
      renderScenarioPills();
    }

    if (guideRes.ok) {
      state.allGuidelines = await guideRes.json();
    }

    // Load initial default scenario
    await loadScenario(state.activeScenarioId);
  } catch (err) {
    console.error('Initialization failed:', err);
    $('systemStatus').textContent = 'INIT ERROR';
  }
}

function renderScenarioPills() {
  const container = $('scenarioPills');
  if (!container) return;

  const pillsHtml = state.scenarios.map((s, idx) => `
    <button class="scenario-pill ${s.id === state.activeScenarioId ? 'active' : ''}" data-id="${s.id}">
      <div class="pill-top-row">
        <span class="pill-id">${escapeHtml(s.patient_id)}</span>
        <span class="pill-badge">${escapeHtml(s.badge || `CASE ${idx + 1}`)}</span>
      </div>
      <div class="pill-title">${escapeHtml(s.title)}</div>
      <div class="pill-cat">${escapeHtml(s.category)}</div>
    </button>
  `).join('');

  container.innerHTML = pillsHtml;

  // Add click handlers
  container.querySelectorAll('.scenario-pill').forEach(btn => {
    btn.addEventListener('click', () => {
      const sid = btn.dataset.id;
      if (sid && sid !== state.activeScenarioId) {
        loadScenario(sid);
      }
    });
  });
}

async function loadScenario(scenarioId) {
  state.activeScenarioId = scenarioId;
  renderScenarioPills();

  $('systemStatus').textContent = 'LOADING SCENARIO...';

  try {
    const res = await fetch(`/api/scenario/${scenarioId}`);
    if (!res.ok) throw new Error(`Could not load scenario ${scenarioId}`);
    const scen = await res.json();
    state.currentScenarioData = scen;

    // Update scenario meta card
    $('activeScenarioBadge').textContent = scen.badge || scen.id.toUpperCase();
    $('scenarioTitle').textContent = `${scen.title} (${scen.patient_id})`;
    $('scenarioSummary').textContent = scen.summary || '—';
    $('scenarioChallenge').textContent = scen.challenge || '—';
    $('scenarioCategory').textContent = scen.category || 'Clinical Follow-up';
    $('scenarioDifficulty').textContent = scen.difficulty || 'Standard';

    // Update Consultation panel
    $('patientId').textContent = scen.patient_id;
    $('visitDate').textContent = scen.visit_date;
    $('patientAvatar').textContent = scen.patient_id.replace('SYN-', 'P');
    $('patientBadge').textContent = scen.difficulty ? scen.difficulty.toUpperCase() : 'SYNTHETIC';

    // Render Transcript Turns
    renderTranscript(scen.transcript?.turns || []);

    // Setup Late Update Section
    const lateText = scen.late_update?.text || '';
    $('lateText').textContent = lateText;
    if (scen.late_update?.source) {
      $('lateMeta').textContent = `Source: ${scen.late_update.source.replace(/_/g, ' ').toUpperCase()}`;
    }
    $('updateSection').classList.add('hidden');
    $('updateBtn').disabled = true;

    // Reset traces and record
    resetWorkspaces();

    $('systemStatus').textContent = 'READY';
  } catch (err) {
    console.error('Error loading scenario:', err);
    $('systemStatus').textContent = 'SCENARIO ERROR';
  }
}

function renderTranscript(turns) {
  const container = $('transcript');
  if (!turns || turns.length === 0) {
    container.innerHTML = '<div class="empty-state"><p>No consultation turns recorded.</p></div>';
    return;
  }

  container.innerHTML = turns.map(t => `
    <div class="turn ${escapeHtml(t.speaker)}">
      <div class="speaker">${t.speaker === 'clinician' ? 'CLINICIAN (DR.)' : 'PATIENT'}</div>
      <p>${escapeHtml(t.text)}</p>
    </div>
  `).join('');
}

function resetWorkspaces() {
  state.lastAgentOutput = null;
  state.toolCallCount = 0;
  state.revisionCount = 0;
  $('toolCount').textContent = '0 tool calls';
  $('revisionCount').textContent = '0 revisions';

  $('trace').innerHTML = `
    <div class="empty-state">
      <div class="empty-icon">⚡</div>
      <p>Click <strong>Run Agent</strong> to watch the orchestrator plan, conditionally call sandbox tools, and reconcile evidence.</p>
    </div>`;
  $('traceState').textContent = 'WAITING';
  $('traceState').className = 'live-badge';

  $('record').innerHTML = `
    <div class="empty-state">
      <div class="empty-icon">📋</div>
      <p>The structured, non-prescriptive follow-up record will be generated and validated here.</p>
    </div>`;
  $('validationBadge').textContent = 'NOT RUN';
  $('validationBadge').className = 'validation neutral';
  $('issues').classList.add('hidden');
  $('exportSoapBtn').disabled = true;
  $('exportJsonBtn').disabled = true;
}

// --------------------------------------------------------------------------
// Operational Trace Animation
// --------------------------------------------------------------------------
function clearTrace() {
  $('trace').innerHTML = '';
  state.toolCallCount = 0;
  state.revisionCount = 0;
  $('toolCount').textContent = '0 tool calls';
  $('revisionCount').textContent = '0 revisions';
}

function addTrace(e) {
  const time = new Date().toLocaleTimeString([], { hour12: false });
  let icon = '•';
  let cls = '';

  if (e.type === 'tool') {
    icon = '↳';
    state.toolCallCount++;
    $('toolCount').textContent = `${state.toolCallCount} tool call${state.toolCallCount > 1 ? 's' : ''}`;
  } else if (e.type === 'tool_result') {
    icon = e.status === 'OK' ? '✓' : '✗';
  } else if (e.type === 'validation') {
    icon = e.status === 'PASS' ? '✓' : '⚠';
    cls = e.status === 'PASS' ? 'validation' : 'fail';
  } else if (e.type === 'revision') {
    icon = '↻';
    state.revisionCount++;
    $('revisionCount').textContent = `${state.revisionCount} revision${state.revisionCount > 1 ? 's' : ''}`;
    cls = 'fail';
  } else if (e.type === 'adapt') {
    icon = '◆';
  } else if (e.type === 'submit') {
    icon = '⚖';
  }

  const itemHtml = `
    <div class="trace-item ${cls}">
      <div class="trace-time">${time}</div>
      <div class="trace-icon">${icon}</div>
      <div class="trace-message">
        ${escapeHtml(e.message)}
        ${e.tool ? `<small>${escapeHtml(e.tool)}</small>` : ''}
      </div>
    </div>`;

  $('trace').insertAdjacentHTML('beforeend', itemHtml);
  $('trace').scrollTop = $('trace').scrollHeight;
}

// --------------------------------------------------------------------------
// Structured Record Rendering
// --------------------------------------------------------------------------
function renderRecord(output) {
  state.lastAgentOutput = output;
  const rec = output.record || {};
  const obj = rec.objective || {};

  const metricsHtml = Object.entries(obj).map(([k, v]) => `
    <div class="metric">
      <b>${escapeHtml(v)}</b>
      <small>${escapeHtml(k.replace(/_/g, ' '))}</small>
    </div>
  `).join('');

  $('record').innerHTML = `
    <div class="record-block">
      <h3>SUBJECTIVE</h3>
      <p>${escapeHtml(rec.subjective || '—')}</p>
    </div>
    <div class="record-block">
      <h3>OBJECTIVE</h3>
      <div class="objective">${metricsHtml || '<span>—</span>'}</div>
    </div>
    <div class="record-block">
      <h3>STATUS SUMMARY</h3>
      <p>${escapeHtml(rec.status_summary || '—')}</p>
    </div>
    <div class="record-block">
      <h3>PLAN / ACTIONS FOR REVIEW</h3>
      <ul>
        ${(rec.plan_actions || []).map(a => `<li>${escapeHtml(a)}</li>`).join('') || '<li>None</li>'}
      </ul>
    </div>
    <div class="record-block">
      <h3>UNRESOLVED FOR CLINICIAN REVIEW</h3>
      <ul>
        ${(output.unresolved_for_clinician_review || []).map(u => `
          <li><b>${escapeHtml(u.issue)}</b> — ${escapeHtml(u.detail)}</li>
        `).join('') || '<li>No unresolved clinical conflicts.</li>'}
      </ul>
    </div>
  `;

  // Validation Status
  const v = output.validation || {};
  const badge = $('validationBadge');
  badge.textContent = v.status || 'UNKNOWN';
  badge.className = `validation ${v.status === 'FAIL' ? 'fail' : ''}`;

  if (v.issues && v.issues.length > 0) {
    $('issues').classList.remove('hidden');
    $('issues').innerHTML = `
      <strong>Validator Findings (${v.issues.length})</strong>
      <ul>${v.issues.map(iss => `<li>${escapeHtml(iss.detail || iss.check)}</li>`).join('')}</ul>
    `;
  } else {
    $('issues').classList.add('hidden');
  }

  // Enable Export buttons
  $('exportSoapBtn').disabled = false;
  $('exportJsonBtn').disabled = false;
}

// --------------------------------------------------------------------------
// Agent Run (Phase 1)
// --------------------------------------------------------------------------
async function runAgent(customPayload = null) {
  $('runBtn').disabled = true;
  $('updateBtn').disabled = true;
  $('systemStatus').textContent = 'AGENT RUNNING';
  $('traceState').textContent = 'LIVE';
  $('traceState').className = 'live-badge';
  clearTrace();

  $('record').innerHTML = `
    <div class="empty-state">
      <div class="empty-icon">⏳</div>
      <p>Orchestrator is retrieving supporting evidence and reconciling records...</p>
    </div>`;
  $('validationBadge').textContent = 'RUNNING';
  $('validationBadge').className = 'validation neutral';
  $('updateSection').classList.add('hidden');

  try {
    const payload = customPayload || { scenario_id: state.activeScenarioId };
    const res = await fetch('/api/run', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload)
    });

    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || 'Agent execution failed');

    // Stream trace events with smooth staggered animation
    const events = data.events || [];
    events.forEach((ev, idx) => {
      setTimeout(() => addTrace(ev), idx * 110);
    });

    const finishDelay = events.length * 110 + 120;
    setTimeout(() => {
      renderRecord(data.output);
      $('systemStatus').textContent = 'DRAFT COMPLETE';
      $('traceState').textContent = 'COMPLETE';
      $('updateBtn').disabled = false;
      $('updateSection').classList.remove('hidden');
    }, finishDelay);

  } catch (err) {
    $('trace').innerHTML = `<div class="empty-state"><p style="color:var(--bad)">${escapeHtml(err.message)}</p></div>`;
    $('systemStatus').textContent = 'EXECUTION ERROR';
    $('traceState').textContent = 'ERROR';
    $('traceState').className = 'live-badge';
  } finally {
    setTimeout(() => { $('runBtn').disabled = false; }, 800);
  }
}

// --------------------------------------------------------------------------
// Agent Adaptation (Phase 2)
// --------------------------------------------------------------------------
async function adaptAgent() {
  $('updateBtn').disabled = true;
  $('systemStatus').textContent = 'ADAPTING';
  $('adaptStatus').textContent = 'PROCESSING';

  try {
    const text = state.currentScenarioData?.late_update?.text || $('lateText').textContent;
    const res = await fetch('/api/adapt', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        scenario_id: state.activeScenarioId,
        text: text
      })
    });

    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || 'Adaptation failed');

    const events = data.events || [];
    events.forEach((ev, idx) => {
      setTimeout(() => addTrace(ev), idx * 110);
    });

    const finishDelay = events.length * 110 + 120;
    setTimeout(() => {
      renderRecord(data.output);
      $('adaptStatus').textContent = 'ADAPTED';
      $('systemStatus').textContent = 'RECORD ADAPTED';
      $('traceState').textContent = 'ADAPTED';
    }, finishDelay);

  } catch (err) {
    $('adaptStatus').textContent = 'ERROR';
    $('systemStatus').textContent = 'ERROR';
    alert(`Adaptation error: ${err.message}`);
  } finally {
    setTimeout(() => { $('updateBtn').disabled = false; }, 1000);
  }
}

// --------------------------------------------------------------------------
// Data Inspector Feature
// --------------------------------------------------------------------------
async function openDataInspector() {
  const patientId = state.currentScenarioData?.patient_id || 'SYN-0042';
  $('inspectorPatientLabel').textContent = `Inspecting Patient: ${patientId} (${state.currentScenarioData?.title || ''})`;

  try {
    const res = await fetch(`/api/patient-data/${patientId}`);
    if (!res.ok) throw new Error('Failed to retrieve patient chart');
    const chart = await res.json();
    state.activePatientData = chart;

    renderInspectorCards(chart);
    renderInspectorTable(chart);
    renderInspectorJson(chart);
    renderGuidelinesList(state.allGuidelines);

    showModal('inspectorModal');
  } catch (err) {
    alert(`Error loading data inspector: ${err.message}`);
  }
}

function renderInspectorCards(chart) {
  // Medications
  const meds = chart.medications || [];
  $('medsCount').textContent = meds.length;
  $('inspectorMedsList').innerHTML = meds.map(m => `
    <div class="card-item">
      <strong>${escapeHtml(m.name)} ${escapeHtml(m.dose || '')}</strong>
      <span>Frequency: ${escapeHtml(m.frequency || 'daily')} · Started: ${escapeHtml(m.start_date || '—')}</span>
    </div>
  `).join('') || '<div class="card-item"><span>No active medications</span></div>';

  // Labs
  const labs = chart.labs || [];
  $('labsCount').textContent = labs.length;
  $('inspectorLabsList').innerHTML = labs.map(panel => {
    const results = panel.results || {};
    const metricsHtml = Object.entries(results).map(([k, v]) => {
      const num = Number(v);
      const isAbnormal = (k === 'inr' && num > 3.0) ||
                         (k === 'potassium' && num > 5.0) ||
                         (k === 'creatinine' && num > 1.4) ||
                         (k === 'egfr' && num < 60) ||
                         (k === 'wbc' && num > 11.0);
      return `
        <div class="lab-val ${isAbnormal ? 'abnormal' : ''}">
          <b>${escapeHtml(v)}</b>
          <small>${escapeHtml(k)}</small>
        </div>
      `;
    }).join('');

    return `
      <div class="card-item">
        <strong>${escapeHtml(panel.panel || 'Laboratory Panel')} (${escapeHtml(panel.date || '')})</strong>
        <div class="lab-results-grid">${metricsHtml}</div>
      </div>
    `;
  }).join('') || '<div class="card-item"><span>No laboratory records</span></div>';

  // Allergies
  const allergies = chart.allergies || [];
  $('allergiesCount').textContent = allergies.length;
  $('inspectorAllergiesList').innerHTML = allergies.map(a => `
    <div class="card-item ${a.severity === 'life-threatening' ? 'danger' : 'warning'}">
      <strong>${escapeHtml(a.allergen || 'Unknown Allergen')}</strong>
      <span>Reaction: ${escapeHtml(a.reaction || 'Unspecified')}</span>
      <span>Severity: ${escapeHtml(a.severity || 'Moderate')}</span>
    </div>
  `).join('') || '<div class="card-item"><span>No documented drug allergies on file.</span></div>';

  // Prior Notes
  const notes = chart.prior_notes || [];
  $('notesCount').textContent = notes.length;
  $('inspectorNotesList').innerHTML = notes.map(n => `
    <div class="card-item">
      <strong>${escapeHtml(n.date || '—')} · ${escapeHtml(n.author || 'Clinician')}</strong>
      <span>${escapeHtml(n.summary || '')}</span>
    </div>
  `).join('') || '<div class="card-item"><span>No prior clinical notes found.</span></div>';
}

function renderInspectorTable(chart) {
  const tbody = $('inspectorTableBody');
  const rows = [];

  (chart.medications || []).forEach(m => {
    rows.push(`
      <tr>
        <td><span class="tag-pill">Medication</span></td>
        <td><b>${escapeHtml(m.name)}</b></td>
        <td>${escapeHtml(m.dose)} (${escapeHtml(m.frequency)})</td>
        <td>Active (Started ${escapeHtml(m.start_date)})</td>
        <td>Monitored via EHR sandbox</td>
      </tr>
    `);
  });

  (chart.labs || []).forEach(p => {
    Object.entries(p.results || {}).forEach(([k, v]) => {
      const num = Number(v);
      const isWarn = (k === 'inr' && num > 3.0) || (k === 'potassium' && num > 5.0) || (k === 'creatinine' && num > 1.4);
      rows.push(`
        <tr>
          <td><span class="tag-pill accent">Lab Result</span></td>
          <td><b>${escapeHtml(k.toUpperCase())}</b> (${escapeHtml(p.panel)})</td>
          <td>${escapeHtml(v)}</td>
          <td>${isWarn ? '<b style="color:var(--bad)">ABNORMAL / ELEVATED</b>' : 'Within reference'}</td>
          <td>Date: ${escapeHtml(p.date)}</td>
        </tr>
      `);
    });
  });

  (chart.allergies || []).forEach(a => {
    rows.push(`
      <tr>
        <td><span class="tag-pill" style="color:var(--bad)">Allergy</span></td>
        <td><b>${escapeHtml(a.allergen)}</b></td>
        <td>${escapeHtml(a.reaction)}</td>
        <td><b style="color:var(--bad)">${escapeHtml(a.severity || 'Flagged')}</b></td>
        <td>EHR Documented Contraindication</td>
      </tr>
    `);
  });

  (chart.prior_notes || []).forEach(n => {
    rows.push(`
      <tr>
        <td><span class="tag-pill">Prior Note</span></td>
        <td><b>${escapeHtml(n.author)}</b></td>
        <td>Note Date: ${escapeHtml(n.date)}</td>
        <td>Archived Clinical Summary</td>
        <td>${escapeHtml(n.summary)}</td>
      </tr>
    `);
  });

  tbody.innerHTML = rows.join('') || '<tr><td colspan="5">No records found.</td></tr>';
}

function renderInspectorJson(chart) {
  $('inspectorRawJson').textContent = JSON.stringify(chart, null, 2);
}

function renderGuidelinesList(guidelines) {
  const container = $('guidelinesList');
  if (!guidelines || guidelines.length === 0) {
    container.innerHTML = '<div class="empty-state"><p>No guidelines loaded.</p></div>';
    return;
  }

  container.innerHTML = guidelines.map(g => `
    <div class="guideline-card">
      <h4>[${escapeHtml(g.id)}] ${escapeHtml(g.topic)}</h4>
      <p>${escapeHtml(g.text)}</p>
      <div class="guideline-tags">
        ${(g.keywords || []).map(k => `<span class="tag-pill">${escapeHtml(k)}</span>`).join('')}
      </div>
    </div>
  `).join('');
}

// --------------------------------------------------------------------------
// Interactive Custom Editor
// --------------------------------------------------------------------------
function openCustomEditor() {
  const scen = state.currentScenarioData || {};
  $('editPatientId').value = scen.patient_id ? `${scen.patient_id}-CUSTOM` : 'CUSTOM-001';
  $('editVisitDate').value = scen.visit_date || '2026-10-25';
  $('editLateUpdateText').value = scen.late_update?.text || '';

  // Populate turns list
  const turns = scen.transcript?.turns || [
    { speaker: 'clinician', text: 'How have you been feeling since starting the medication?' },
    { speaker: 'patient', text: 'I noticed some morning dizziness and fatigue.' }
  ];

  state.customTurns = JSON.parse(JSON.stringify(turns));
  renderTurnEditorList();

  showModal('editorModal');
}

function renderTurnEditorList() {
  const container = $('turnEditorList');
  container.innerHTML = state.customTurns.map((turn, i) => `
    <div class="turn-editor-item" data-index="${i}">
      <select class="turn-speaker-select" data-index="${i}">
        <option value="clinician" ${turn.speaker === 'clinician' ? 'selected' : ''}>Clinician</option>
        <option value="patient" ${turn.speaker === 'patient' ? 'selected' : ''}>Patient</option>
      </select>
      <input type="text" class="turn-text-input" data-index="${i}" value="${escapeHtml(turn.text)}" />
      <button class="turn-del-btn" data-index="${i}" title="Delete turn">✕</button>
    </div>
  `).join('');

  // Attach change/delete handlers
  container.querySelectorAll('.turn-speaker-select').forEach(sel => {
    sel.addEventListener('change', (e) => {
      const idx = Number(e.target.dataset.index);
      state.customTurns[idx].speaker = e.target.value;
    });
  });

  container.querySelectorAll('.turn-text-input').forEach(inp => {
    inp.addEventListener('input', (e) => {
      const idx = Number(e.target.dataset.index);
      state.customTurns[idx].text = e.target.value;
    });
  });

  container.querySelectorAll('.turn-del-btn').forEach(btn => {
    btn.addEventListener('click', (e) => {
      const idx = Number(btn.dataset.index);
      state.customTurns.splice(idx, 1);
      renderTurnEditorList();
    });
  });
}

function addTurn(speaker) {
  state.customTurns.push({
    speaker: speaker,
    text: speaker === 'clinician' ? 'Can you tell me more about that?' : 'Yes, doctor.'
  });
  renderTurnEditorList();
}

function applyAndRunCustom() {
  const patientId = $('editPatientId').value.trim() || 'CUSTOM-001';
  const visitDate = $('editVisitDate').value || '2026-10-25';
  const lateText = $('editLateUpdateText').value.trim();

  // Update Consultation panel UI with custom values
  $('patientId').textContent = patientId;
  $('visitDate').textContent = visitDate;
  $('patientAvatar').textContent = 'C1';
  $('patientBadge').textContent = 'CUSTOM TEST';
  $('lateText').textContent = lateText || 'No late update provided.';

  renderTranscript(state.customTurns);

  hideModal('editorModal');

  // Trigger agent run with custom payload
  const customPayload = {
    patient_id: patientId,
    visit_date: visitDate,
    transcript: {
      patient_id: patientId,
      visit_date: visitDate,
      turns: state.customTurns,
      late_update: lateText ? { text: lateText, source: 'custom_input' } : null
    },
    custom_data: {
      patient_id: patientId,
      medications: state.currentScenarioData?.medications || [],
      allergies: state.currentScenarioData?.allergies || [],
      labs: state.currentScenarioData?.labs || [],
      prior_notes: state.currentScenarioData?.prior_notes || []
    }
  };

  runAgent(customPayload);
}

// --------------------------------------------------------------------------
// Export Features
// --------------------------------------------------------------------------
function exportSoapNote() {
  if (!state.lastAgentOutput) return;
  const out = state.lastAgentOutput;
  const rec = out.record || {};
  const obj = rec.objective || {};

  const metricsLines = Object.entries(obj).map(([k, v]) => `- ${k.toUpperCase()}: ${v}`).join('\n');
  const planLines = (rec.plan_actions || []).map(p => `- ${p}`).join('\n');
  const unresolvedLines = (out.unresolved_for_clinician_review || []).map(u => `- **${u.issue}**: ${u.detail}`).join('\n');

  const markdown = `# CLINICAL FOLLOW-UP NOTE (AI-DOCUMENTED FOR REVIEW)
**Patient ID:** ${out.patient_id || state.currentScenarioData?.patient_id || '—'}
**Visit Date:** ${out.visit_date || state.currentScenarioData?.visit_date || '—'}
**Validation Status:** ${out.validation?.status || 'UNKNOWN'}

---

## 1. SUBJECTIVE
${rec.subjective || '—'}

## 2. OBJECTIVE
${metricsLines || 'None documented.'}

## 3. ASSESSMENT / STATUS SUMMARY
${rec.status_summary || '—'}

## 4. PLAN / RECOMMENDATIONS FOR CLINICIAN REVIEW
${planLines || 'None.'}

## 5. UNRESOLVED CONFLICTS REQUIRING LICENSED CLINICIAN REVIEW
${unresolvedLines || 'None.'}

---
*Notice: AI output strictly generated as clinical decision support. Prescriptive commands and diagnostic declarations are restricted by system guardrails.*
`;

  $('exportModalTitle').textContent = 'Clinician SOAP Note (Markdown)';
  $('exportContent').textContent = markdown;
  showModal('exportModal');
}

function exportRawJson() {
  if (!state.lastAgentOutput) return;
  $('exportModalTitle').textContent = 'Structured Record & Audit Log (JSON)';
  $('exportContent').textContent = JSON.stringify(state.lastAgentOutput, null, 2);
  showModal('exportModal');
}

// --------------------------------------------------------------------------
// Event Listeners & Setup
// --------------------------------------------------------------------------
function setupEventListeners() {
  // Main Hero Actions
  $('runBtn').addEventListener('click', () => runAgent());
  $('updateBtn').addEventListener('click', adaptAgent);
  $('clearTraceBtn').addEventListener('click', clearTrace);

  // Inspector Modal
  $('openInspectorBtn').addEventListener('click', openDataInspector);
  $('quickChartBtn').addEventListener('click', openDataInspector);
  $('closeInspectorBtn').addEventListener('click', () => hideModal('inspectorModal'));

  // Inspector Tabs
  document.querySelectorAll('.inspector-tabs .tab-btn').forEach(btn => {
    btn.addEventListener('click', (e) => {
      document.querySelectorAll('.inspector-tabs .tab-btn').forEach(b => b.classList.remove('active'));
      document.querySelectorAll('.modal-body .tab-content').forEach(c => c.classList.remove('active'));

      e.target.classList.add('active');
      const targetId = e.target.dataset.tab;
      const targetEl = $(targetId);
      if (targetEl) targetEl.classList.add('active');
    });
  });

  // RAG Search in Inspector
  $('ragSearchBtn').addEventListener('click', () => {
    const q = $('ragQueryInput').value.trim().toLowerCase();
    if (!q) {
      renderGuidelinesList(state.allGuidelines);
      return;
    }
    const filtered = state.allGuidelines.filter(g =>
      g.topic.toLowerCase().includes(q) ||
      g.text.toLowerCase().includes(q) ||
      g.keywords.some(k => k.toLowerCase().includes(q))
    );
    renderGuidelinesList(filtered);
  });

  // Custom Editor
  $('openEditorBtn').addEventListener('click', openCustomEditor);
  $('editTranscriptBtn').addEventListener('click', openCustomEditor);
  $('closeEditorBtn').addEventListener('click', () => hideModal('editorModal'));
  $('cancelEditorBtn').addEventListener('click', () => hideModal('editorModal'));
  $('addClinicianTurnBtn').addEventListener('click', () => addTurn('clinician'));
  $('addPatientTurnBtn').addEventListener('click', () => addTurn('patient'));
  $('applyAndRunCustomBtn').addEventListener('click', applyAndRunCustom);

  // Preset Selection in Editor
  $('editPresetSelect').addEventListener('change', (e) => {
    const val = e.target.value;
    if (val.startsWith('scenario-')) {
      const match = state.scenarios.find(s => s.id === val);
      if (match) {
        loadScenario(val).then(() => openCustomEditor());
      }
    }
  });

  // Export
  $('exportSoapBtn').addEventListener('click', exportSoapNote);
  $('exportJsonBtn').addEventListener('click', exportRawJson);
  $('closeExportBtn').addEventListener('click', () => hideModal('exportModal'));
  $('copyExportBtn').addEventListener('click', () => {
    navigator.clipboard.writeText($('exportContent').textContent);
    $('copyExportBtn').textContent = 'Copied!';
    setTimeout(() => { $('copyExportBtn').textContent = 'Copy to Clipboard'; }, 1500);
  });

  $('copyJsonBtn').addEventListener('click', () => {
    navigator.clipboard.writeText($('inspectorRawJson').textContent);
    $('copyJsonBtn').textContent = 'Copied!';
    setTimeout(() => { $('copyJsonBtn').textContent = 'Copy JSON'; }, 1500);
  });
}

// Start Application
setupEventListeners();
initApp();
