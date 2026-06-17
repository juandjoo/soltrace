const API = '/api/v1';
let token = localStorage.getItem('soltrace_token');
let charts = {};
let allGroups = [];
let allTelcos = [];
let logPage = 1;
let logPageSize = 50;

// ── 세션 타이머 ──────────────────────────────────────────────────────────────
let _expireTimer = null;
let _countdownInterval = null;

function _parseJwtExp(tok) {
  try {
    const payload = JSON.parse(atob(tok.split('.')[1].replace(/-/g, '+').replace(/_/g, '/')));
    return payload.exp || null;
  } catch { return null; }
}

function _updateTopbarTimer(expiresAt) {
  const left = Math.max(0, Math.ceil((expiresAt - Date.now()) / 1000));
  const m = Math.floor(left / 60);
  const s = String(left % 60).padStart(2, '0');
  const txt  = document.getElementById('sessionTimerText');
  const disp = document.getElementById('sessionTimerDisplay');
  if (!txt || !disp) return;
  txt.textContent = `${m}:${s}`;
  disp.classList.toggle('session-timer--warn',   left <= 600 && left > 300);
  disp.classList.toggle('session-timer--danger', left <= 300);
}

function _clearSessionTimers() {
  if (_expireTimer)      { clearTimeout(_expireTimer);        _expireTimer = null; }
  if (_countdownInterval){ clearInterval(_countdownInterval); _countdownInterval = null; }
  const txt  = document.getElementById('sessionTimerText');
  const disp = document.getElementById('sessionTimerDisplay');
  if (txt)  txt.textContent = '--:--';
  if (disp) disp.classList.remove('session-timer--warn', 'session-timer--danger');
}

function startSessionTimers(tok) {
  _clearSessionTimers();
  const exp = _parseJwtExp(tok);
  if (!exp) return;
  const expiresAt = exp * 1000;
  const msLeft = expiresAt - Date.now();
  if (msLeft <= 0) { showLogin(); return; }
  _updateTopbarTimer(expiresAt);
  _countdownInterval = setInterval(() => _updateTopbarTimer(expiresAt), 1000);
  _expireTimer = setTimeout(() => { _clearSessionTimers(); showLogin(); }, msLeft);
}

async function api(method, path, body) {
  const opts = {
    method,
    headers: {'Content-Type':'application/json'},
  };
  if (token) opts.headers['Authorization'] = `Bearer ${token}`;
  if (body) opts.body = JSON.stringify(body);
  const r = await fetch(API + path, opts);
  if (r.status === 401) { showLogin(); return null; }
  if (!r.ok) {
    const err = await r.json().catch(() => ({detail: r.statusText}));
    throw new Error(err.detail || r.statusText);
  }
  if (r.status === 204) return null;
  return r.json();
}

function showLogin() {
  _clearSessionTimers();
  token = null;
  localStorage.removeItem('soltrace_token');
  document.getElementById('appLayout').classList.add('app-hidden');
  bootstrap.Modal.getOrCreateInstance(document.getElementById('loginModal')).show();
}

async function extendSession(retry = 1) {
  try {
    const r = await fetch(API + '/auth/refresh', {
      method: 'POST',
      headers: {'Authorization': `Bearer ${token}`},
    });
    if (!r.ok) { showLogin(); return; }
    const data = await r.json();
    token = data.access_token;
    localStorage.setItem('soltrace_token', token);
    startSessionTimers(token);
  } catch {
    if (retry > 0) setTimeout(() => extendSession(retry - 1), 2000);
    else showLogin();
  }
}

function logout() {
  showLogin();
}

function fmtBytes(b) {
  if (!b) return '0 B';
  if (b < 1024) return b + ' B';
  if (b < 1048576) return (b/1024).toFixed(1) + ' KB';
  if (b < 1073741824) return (b/1048576).toFixed(1) + ' MB';
  return (b/1073741824).toFixed(2) + ' GB';
}

function fmtNum(n) {
  return n?.toLocaleString() ?? '0';
}

function fmtUptime(s) {
  if (!s) return '-';
  if (s < 60) return s + '초';
  if (s < 3600) return Math.floor(s/60) + '분';
  if (s < 86400) return Math.floor(s/3600) + '시간';
  return Math.floor(s/86400) + '일';
}

function timeAgo(dt) {
  if (!dt) return '-';
  const diff = (Date.now() - new Date(dt)) / 1000;
  if (diff < 60) return '방금';
  if (diff < 3600) return Math.floor(diff/60) + '분 전';
  if (diff < 86400) return Math.floor(diff/3600) + '시간 전';
  return new Date(dt).toLocaleDateString('ko-KR');
}
