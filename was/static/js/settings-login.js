// ── 로그인 화면 설정 (설정 > 로그인 설정) ──────────────────────────────────
// 실제 로그인 창과 미리보기가 **같은 함수**로 그려진다(applyLoginScreen). 두 벌로 두면
// 한쪽만 고쳐져 "미리보기와 실제가 다른" 버그가 조용히 생긴다.

const LOGIN_DEFAULT_TITLE    = 'SolTrace';
const LOGIN_DEFAULT_SUBTITLE = 'FTP Log Analyzer에 로그인하세요.';

// 업로드 이미지 종류 — 서버(app/login_config.py IMAGE_KINDS)와 같은 키를 쓴다
const LC_IMAGES = {
  bg:   {label: '배경 이미지', box: 'lcBgBox',   preview: 'width:100%;height:120px;object-fit:cover'},
  logo: {label: '로고 이미지', box: 'lcLogoBox', preview: 'height:32px;max-width:180px;object-fit:contain'},
};

let _loginCfg = null;   // 마지막으로 읽은 서버 값 — 로그인 창·미리보기·설정 화면이 공유

// cfg 를 el(요소 묶음)에 반영한다. el 은 로그인 창 또는 미리보기 창의 요소들.
function applyLoginScreen(cfg, el) {
  cfg = cfg || {};
  if (el.root) {
    // 이미지가 있으면 색상보다 이미지가 우선. 둘 다 없으면 기본(투명 → 부트스트랩 백드롭).
    el.root.style.backgroundColor = cfg.bg_image ? '' : (cfg.bg_color || '');
    el.root.style.backgroundImage = cfg.bg_image ? `url("${cfg.bg_image}")` : '';
    el.root.style.backgroundSize = 'cover';
    el.root.style.backgroundPosition = 'center';
  }
  if (el.title) el.title.textContent = cfg.title || LOGIN_DEFAULT_TITLE;
  if (el.subtitle) {
    const sub = cfg.subtitle || LOGIN_DEFAULT_SUBTITLE;
    el.subtitle.textContent = sub;
    el.subtitle.classList.toggle('d-none', !sub);
  }
  if (el.warning) {
    el.warning.textContent = cfg.warning || '';
    el.warning.classList.toggle('d-none', !cfg.warning);
  }
  if (el.logo) {
    // src 를 빈 문자열로 두면 브라우저가 현재 페이지를 다시 받으러 간다 → 속성을 지운다
    if (cfg.logo_image) el.logo.src = cfg.logo_image;
    else el.logo.removeAttribute('src');
    el.logo.classList.toggle('d-none', !cfg.logo_image);
  }
  if (el.icon) el.icon.classList.toggle('d-none', !!cfg.logo_image);
}

function _loginScreenEls() {
  return {
    root:     document.getElementById('loginModal'),
    logo:     document.getElementById('loginLogo'),
    icon:     document.getElementById('loginLogoIcon'),
    title:    document.getElementById('loginTitle'),
    subtitle: document.getElementById('loginSubtitle'),
    warning:  document.getElementById('loginWarning'),
  };
}

// 로그인 창을 띄우기 직전에 부른다. 로그인 전이라 인증 없이 읽는 공개 엔드포인트다.
// 설정을 못 읽어도 기본 화면으로 로그인은 되어야 하므로 실패는 조용히 넘긴다.
function ensureLoginBranding() {
  if (_loginCfg) { applyLoginScreen(_loginCfg, _loginScreenEls()); return; }
  fetch(API + '/settings/login-config', {cache: 'no-store'})
    .then(r => r.ok ? r.json() : null)
    .then(cfg => { if (cfg) { _loginCfg = cfg; applyLoginScreen(cfg, _loginScreenEls()); } })
    .catch(() => { /* 기본 화면 그대로 */ });
}

// ── 설정 화면 ───────────────────────────────────────────────────────────────

function _renderCfgImage(kind, url) {
  const spec = LC_IMAGES[kind];
  document.getElementById(spec.box).innerHTML = url
    ? `<div class="border rounded p-2 d-inline-block bg-light mb-2">
         <img src="${esc(url)}" alt="" style="${spec.preview};display:block">
       </div>
       <div><button class="btn btn-sm btn-outline-danger" onclick="removeLoginImage('${kind}')">
         <i class="bi bi-trash me-1"></i>${spec.label} 제거</button></div>`
    : `<div class="form-text mb-0">등록된 ${spec.label}가 없습니다.</div>`;
}

function _renderLoginConfig(c) {
  _loginCfg = c;
  document.getElementById('lcTitle').value    = c.title || '';
  document.getElementById('lcSubtitle').value = c.subtitle || '';
  document.getElementById('lcWarning').value  = c.warning || '';
  document.getElementById('lcBgColor').value  = c.bg_color || '';
  if (/^#[0-9A-Fa-f]{6}$/.test(c.bg_color || '')) {
    document.getElementById('lcBgColorPicker').value = c.bg_color;
  }
  _renderCfgImage('bg', c.bg_image);
  _renderCfgImage('logo', c.logo_image);
}

async function loadLoginConfig() {
  document.getElementById('lcMsg').className = 'alert d-none py-2 small';
  try {
    _renderLoginConfig(await api('GET', '/settings/login-config'));
  } catch (e) { settingsMsg('lcMsg', 'danger', e.message); }
}

// 색상 선택기와 hex 입력칸을 서로 맞춘다 (어느 쪽을 고쳐도 같은 값이 되게)
function syncLoginBgColor(from) {
  const picker = document.getElementById('lcBgColorPicker');
  const text   = document.getElementById('lcBgColor');
  if (from === 'picker') text.value = picker.value;
  else if (/^#[0-9A-Fa-f]{6}$/.test(text.value)) picker.value = text.value;
}

function clearLoginBgColor() {
  document.getElementById('lcBgColor').value = '';
}

async function saveLoginConfig() {
  const body = {
    title:    document.getElementById('lcTitle').value,
    subtitle: document.getElementById('lcSubtitle').value,
    warning:  document.getElementById('lcWarning').value,
    bg_color: document.getElementById('lcBgColor').value,
  };
  try {
    _renderLoginConfig(await api('PUT', '/settings/login-config', body));
    settingsMsg('lcMsg', 'success', '로그인 화면 설정을 저장했습니다. 다음 로그인 화면부터 보입니다.');
  } catch (e) { settingsMsg('lcMsg', 'danger', e.message); }
}

// 이미지는 multipart 라 api() 헬퍼(JSON)를 쓰지 않고 직접 보낸다.
// 크기·형식 판정은 서버(app/login_config.py) 한 곳에서만 한다 — 규칙이 갈라지지 않게.
async function uploadLoginImage(kind, input) {
  const file = input.files && input.files[0];
  if (!file) return;
  const fd = new FormData();
  fd.append('file', file);
  try {
    const r = await fetch(`${API}/settings/login-config/image/${kind}`, {
      method: 'POST',
      headers: {'Authorization': `Bearer ${token}`},
      body: fd,
      cache: 'no-store',
    });
    if (r.status === 401) { showLogin(); return; }
    if (r.status === 413) throw new Error('파일이 너무 큽니다.');
    const data = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(data.detail || r.statusText);
    _renderLoginConfig(data);
    settingsMsg('lcMsg', 'success', `${LC_IMAGES[kind].label}를 저장했습니다.`);
  } catch (e) {
    settingsMsg('lcMsg', 'danger', e.message);
  } finally {
    input.value = '';   // 같은 파일을 다시 골라도 change 가 오도록
  }
}

async function removeLoginImage(kind) {
  if (!confirm(`${LC_IMAGES[kind].label}를 제거할까요?`)) return;
  try {
    _renderLoginConfig(await api('DELETE', `/settings/login-config/image/${kind}`));
    settingsMsg('lcMsg', 'success', `${LC_IMAGES[kind].label}를 제거했습니다.`);
  } catch (e) { settingsMsg('lcMsg', 'danger', e.message); }
}

// 저장하지 않은 입력값 그대로 보여준다 — 이미지는 이미 저장된 것을 쓴다
function previewLoginConfig() {
  const cfg = {
    title:      document.getElementById('lcTitle').value,
    subtitle:   document.getElementById('lcSubtitle').value,
    warning:    document.getElementById('lcWarning').value,
    bg_color:   document.getElementById('lcBgColor').value,
    bg_image:   (_loginCfg || {}).bg_image || '',
    logo_image: (_loginCfg || {}).logo_image || '',
  };
  applyLoginScreen(cfg, {
    root:     document.getElementById('loginPreviewModal'),
    logo:     document.getElementById('lpLogo'),
    icon:     document.getElementById('lpLogoIcon'),
    title:    document.getElementById('lpTitle'),
    subtitle: document.getElementById('lpSubtitle'),
    warning:  document.getElementById('lpWarning'),
  });
  bootstrap.Modal.getOrCreateInstance(document.getElementById('loginPreviewModal')).show();
}
