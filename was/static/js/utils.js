const API = '/api/v1';
let token = localStorage.getItem('soltrace_token');
let charts = {};
let allGroups = [];
let allTelcos = [];
let logPage = 1;
let logPageSize = 50;

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
