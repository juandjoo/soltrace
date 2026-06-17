const API = '/api/v1';
let token = localStorage.getItem('soltrace_token');
let charts = {};
let allGroups = [];
let allTelcos = [];
let logPage = 1;
let logPageSize = 50;

// ── 세션 타이머 ──────────────────────────────────────────────────────────────
let _warnTimer = null;
let _expireTimer = null;
let _countdownInterval = null;
const _WARN_BEFORE_MS = 5 * 60 * 1000;   // 만료 5분 전 경고

function _parseJwtExp(tok) {
  try {
    const payload = JSON.parse(atob(tok.split('.')[1].replace(/-/g, '+').replace(/_/g, '/')));
    return payload.exp || null;
  } catch { return null; }
}

function _clearSessionTimers() {
  if (_warnTimer)        { clearTimeout(_warnTimer);          _warnTimer = null; }
  if (_expireTimer)      { clearTimeout(_expireTimer);        _expireTimer = null; }
  if (_countdownInterval){ clearInterval(_countdownInterval); _countdownInterval = null; }
  const el = document.getElementById('sessionWarning');
  if (el) el.classList.add('d-none');
}

function _showSessionWarning(expiresAt) {
  const el  = document.getElementById('sessionWarning');
  const msg = document.getElementById('sessionWarningMsg');
  if (!el || !msg) return;
  el.classList.remove('d-none');
  if (_countdownInterval) clearInterval(_countdownInterval);
  _countdownInterval = setInterval(() => {
    const left = Math.max(0, Math.ceil((expiresAt - Date.now()) / 1000));
    const m = Math.floor(left / 60);
    const s = String(left % 60).padStart(2, '0');
    msg.textContent = `세션 만료까지 ${m}:${s} 남았습니다.`;
  }, 1000);
}

function startSessionTimers(tok) {
  _clearSessionTimers();
  const exp = _parseJwtExp(tok);
  if (!exp) return;
  const expiresAt = exp * 1000;
  const msLeft = expiresAt - Date.now();
  if (msLeft <= 0) { showLogin(); return; }
  const warnAt = msLeft - _WARN_BEFORE_MS;
  if (warnAt > 0) {
    _warnTimer = setTimeout(() => _showSessionWarning(expiresAt), warnAt);
  } else {
    _showSessionWarning(expiresAt);
  }
  _expireTimer = setTimeout(() => {
    _clearSessionTimers();
    showLogin();
  }, msLeft);
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
  document.getElementById('appLayout').style.display = 'none';
  bootstrap.Modal.getOrCreateInstance(document.getElementById('loginModal')).show();
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
