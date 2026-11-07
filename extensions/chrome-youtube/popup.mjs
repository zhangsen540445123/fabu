import { ORIGINS } from './protocol.mjs';

const COPY = Object.freeze({
  disabled: '已停用，不读取 YouTube 登录态。',
  permission_required: '需要 YouTube 网站权限，请点击“启用连接”授权。',
  connecting: '正在连接本机 Fabu 助手，请点击“刷新连接状态”查看结果。',
  connected: '已连接本机 Fabu 助手，可回到 Fabu 手动重试下载。',
  reconnecting: '本机助手暂未连接，将间隔重试；请检查安装，或停用后重新启用。',
  unavailable: '暂时无法获取状态，请重新打开助手或检查扩展权限。'
});

export function mountPopup(chrome, document) {
  const enable = document.getElementById('enable');
  const disable = document.getElementById('disable');
  const status = document.getElementById('status');
  let sequence = 0;
  function render(state) {
    status.textContent = Object.hasOwn(COPY, state?.status) ? COPY[state.status] : COPY.unavailable;
    enable.disabled = state?.enabled === true;
    disable.disabled = state?.enabled === false && state?.status !== 'unavailable';
  }
  async function command(type) {
    const current = ++sequence;
    try {
      const state = await chrome.runtime.sendMessage({ type });
      if (current === sequence) render(state);
    } catch { if (current === sequence) render({ status: 'unavailable' }); }
  }
  enable.addEventListener('click', async () => {
    const current = ++sequence;
    enable.disabled = true;
    try {
      // Must be the click handler's first asynchronous API call (user gesture).
      const granted = await chrome.permissions.request({ origins: ORIGINS });
      if (current !== sequence) return;
      if (granted) await command('enable');
      else render({ enabled: false, status: 'permission_required' });
    } catch { if (current === sequence) render({ status: 'unavailable' }); }
  });
  disable.addEventListener('click', () => command('disable'));
  document.getElementById('refresh').addEventListener('click', () => command('get-state'));
  void command('get-state');
}

if (typeof document !== 'undefined' && typeof chrome !== 'undefined') mountPopup(chrome, document);
