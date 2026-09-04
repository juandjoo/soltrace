const API = '/api/v1';
let token = localStorage.getItem('soltrace_token');

function esc(s) {
  return String(s ?? '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;').replace(/'/g,'&#39;');
}
let charts = {};
let allGroups = [];
let allTelcos = [];
let logPage = 1;
let logPageSize = 50;

// ── 테마 (라이트/다크) ───────────────────────────────────────────────────────
// 첫 적용은 index.html <head> 인라인 스크립트가 한다(흰 화면 번쩍임 방지).
// 여기서는 토글과 차트 색 반영만 맡는다.
const THEME_KEY = 'soltrace_theme';

function currentTheme() {
  return document.documentElement.getAttribute('data-bs-theme') === 'dark' ? 'dark' : 'light';
}

function applyTheme(mode) {
  document.documentElement.setAttribute('data-bs-theme', mode);
  try { localStorage.setItem(THEME_KEY, mode); } catch { /* 저장소 차단 — 이번 세션만 적용 */ }
  const icon = document.getElementById('themeToggleIcon');
  const btn  = document.getElementById('themeToggleBtn');
  if (icon) icon.className = mode === 'dark' ? 'bi bi-sun-fill' : 'bi bi-moon-stars';
  if (btn)  btn.title = mode === 'dark' ? '라이트 모드로' : '다크 모드로';
  _applyChartTheme(mode);
}

// Chart.js 기본 눈금·격자 색. 이미 그려진 차트에는 적용되지 않으므로 토글 후 다시 그린다.
function _applyChartTheme(mode) {
  if (typeof Chart === 'undefined') return;
  Chart.defaults.color       = mode === 'dark' ? '#adb5bd' : '#666';
  Chart.defaults.borderColor = mode === 'dark' ? '#343a40' : 'rgba(0,0,0,.1)';
}

function toggleTheme() {
  applyTheme(currentTheme() === 'dark' ? 'light' : 'dark');
  // 차트는 생성 시점의 색을 들고 있어 다시 그려야 바뀐다
  if (typeof loadAll === 'function' &&
      document.getElementById('page-dashboard')?.classList.contains('active')) {
    loadAll();
  }
}

// ── 세션 타이머 ──────────────────────────────────────────────────────────────
let _expireTimer = null;
let _countdownInterval = null;

function _parseJwt(tok) {
  try {
    return JSON.parse(atob(tok.split('.')[1].replace(/-/g, '+').replace(/_/g, '/')));
  } catch { return null; }
}

function _parseJwtExp(tok) {
  const p = _parseJwt(tok);
  return p?.exp || null;
}

// 현재 토큰의 role ('admin' | 'customer'). role 클레임 없는 구버전 토큰은 admin 취급.
function getRole() {
  const p = token ? _parseJwt(token) : null;
  return p?.role || (p?.sub === 'admin' ? 'admin' : 'customer');
}

// role 에 따라 admin 전용 UI(설정 등) 표시/숨김. data-admin-only 요소를 토글한다.
// 상단바의 로그인 사용자 표시도 같은 토큰에서 채운다 (표시가 권한과 갈라지지 않게 한 곳에서).
function applyRoleUI() {
  const isAdmin = getRole() === 'admin';
  document.querySelectorAll('[data-admin-only]').forEach(el => {
    el.classList.toggle('d-none', !isAdmin);
  });
  _renderCurrentUser(isAdmin);
}

function _renderCurrentUser(isAdmin) {
  const p = token ? _parseJwt(token) : null;
  const nameEl = document.getElementById('currentUserName');
  const roleEl = document.getElementById('currentUserRole');
  if (!nameEl || !roleEl) return;
  nameEl.textContent = p?.sub || '-';
  nameEl.title = p?.sub || '';
  if (isAdmin) {
    roleEl.textContent = '관리자';
    roleEl.className = 'badge rounded-pill text-bg-secondary';
    roleEl.title = '';
  } else {
    const customer = p?.customer || '';
    roleEl.textContent = customer || '고객';
    roleEl.className = 'badge rounded-pill text-bg-light border';
    roleEl.title = customer ? `고객사: ${customer}` : '고객 계정';
  }
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

// ── 로컬 시각 포맷 (대시보드/로그 조회가 공유) ──────────────────────────────
function pad2(n) { return String(n).padStart(2, '0'); }
// YYYY-MM-DD
function fmtLocalDate(d) {
  return `${d.getFullYear()}-${pad2(d.getMonth()+1)}-${pad2(d.getDate())}`;
}
// YYYY-MM-DD HH:MM
function fmtLocalDateTime(d) {
  return `${fmtLocalDate(d)} ${pad2(d.getHours())}:${pad2(d.getMinutes())}`;
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

// 페이지 로드 시 현재 테마에 맞춰 버튼 아이콘·차트 기본색을 맞춘다
applyTheme(currentTheme());
