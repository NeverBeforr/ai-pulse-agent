const csrf = document.querySelector('meta[name="csrf-token"]').content;
const toastElement = document.getElementById('toast');
let toastTimer;
function toast(text, error = false) {
  toastElement.textContent = text;
  toastElement.classList.toggle('error', error);
  toastElement.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { toastElement.hidden = true; }, 5500);
}
async function api(path, method = 'POST', data) {
  const response = await fetch(path, {method, headers: {'Content-Type': 'application/json', 'X-CSRF-Token': csrf}, body: data === undefined ? undefined : JSON.stringify(data)});
  const payload = await response.json();
  if (!response.ok) {
    const detail = payload.detail;
    throw new Error(typeof detail === 'string' ? detail : JSON.stringify(detail));
  }
  return payload;
}
async function pollRun(id) {
  const banner = document.getElementById('active-banner');
  banner.hidden = false;
  document.querySelectorAll('[data-run]').forEach(button => { button.disabled = true; });
  const timer = setInterval(async () => {
    try {
      const run = await (await fetch(`/api/runs/${id}`)).json();
      document.getElementById('active-text').textContent = run.stage;
      if (run.state !== 'running') {
        clearInterval(timer);
        banner.hidden = true;
        document.querySelectorAll('[data-run]').forEach(button => { button.disabled = false; });
        if (run.state === 'success') {
          toast('任务已完成');
          setTimeout(() => location.reload(), 650);
        } else {
          toast(run.detail.error || '任务未完成，详情见运行记录', true);
        }
      }
    } catch {
      clearInterval(timer);
      document.querySelectorAll('[data-run]').forEach(button => { button.disabled = false; });
      document.getElementById('active-text').textContent = '连接中断，请刷新页面检查任务';
    }
  }, 2000);
}
document.addEventListener('click', async event => {
  const button = event.target.closest('button');
  if (!button) return;
  if (button.dataset.filter) {
    document.querySelectorAll('[data-filter]').forEach(b => b.classList.toggle('active', b === button));
    document.querySelectorAll('.edition-section').forEach(section => {
      section.hidden = button.dataset.filter !== 'all' && section.dataset.section !== button.dataset.filter;
    });
    return;
  }
  const mutation = button.dataset.run || button.dataset.sourceToggle || button.dataset.sourceTest || button.dataset.sourceRemove;
  if (!mutation) return;
  if (button.dataset.run === 'model_test' && !confirm('将发起真实模型请求并消耗额度。请先保存配置，继续测试？')) return;
  if (button.dataset.run === 'digest' && document.querySelector('.mode-pill').textContent.includes('云端') && !confirm('将调用你配置的云端模型并消耗额度，继续？')) return;
  if (button.dataset.sourceRemove && !confirm('移除此信息源？历史简报会保留其出处。')) return;
  button.disabled = true;
  try {
    if (button.dataset.run) {
      const data = await api('/api/runs', 'POST', {kind: button.dataset.run});
      toast(data.created ? '任务已启动，可以继续浏览页面' : '已有任务运行中，已合并请求');
      pollRun(data.run_id);
    } else if (button.dataset.sourceToggle) {
      await api(`/api/sources/${button.dataset.sourceToggle}`, 'PATCH', {enabled: button.dataset.enabled === 'true'});
      location.reload();
    } else if (button.dataset.sourceTest) {
      toast('正在测试来源，可能需要几十秒…');
      const result = await api(`/api/sources/${button.dataset.sourceTest}/test`);
      toast(`测试成功，取得 ${result.count} 条`);
      setTimeout(() => location.reload(), 600);
    } else {
      await api(`/api/sources/${button.dataset.sourceRemove}`, 'DELETE');
      location.reload();
    }
  } catch (error) { toast(error.message, true); }
  finally { button.disabled = false; }
});
for (const form of document.querySelectorAll('#source-form, #article-form, #settings-form')) {
  form.addEventListener('submit', async event => {
    event.preventDefault();
    const body = Object.fromEntries(new FormData(form));
    let path, method = 'POST';
    if (form.id === 'source-form') { path = '/api/sources'; body.channel = '用户添加'; }
    else if (form.id === 'article-form') { path = '/api/articles'; body.source_id = Number(body.source_id); }
    else {
      path = '/api/settings'; method = 'PATCH';
      for (const key of ['llm_input_price', 'llm_output_price']) body[key] = body[key] === '' ? null : Number(body[key]);
      if (body.model_mode === 'cloud' && !confirm('启用云端后，定时生成会调用模型并产生费用，是否保存？')) return;
    }
    const submit = form.querySelector('[type="submit"]') || form.querySelector('button');
    submit.disabled = true;
    try { await api(path, method, body); toast('已保存'); setTimeout(() => location.reload(), 500); }
    catch (error) { toast(error.message, true); }
    finally { submit.disabled = false; }
  });
}
if (document.body.dataset.activeRun) pollRun(document.body.dataset.activeRun);
