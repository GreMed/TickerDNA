const fs = require('fs');
const path = require('path');

const ENV_VAR = 'NEXT_PUBLIC_TICKERDNA_APP_URL';

const REJECT_PLACEHOLDERS = [
  '__APP_URL__',
  'https://share.streamlit.io',
  'https://your-app.streamlit.app',
  'https://your-real-subdomain.streamlit.app',
  'https://example.streamlit.app',
  'https://your-app.share.streamlit.app',
];

function fail(msg) {
  console.error('');
  console.error('❌ 构建失败：体验地址无效。');
  console.error(`   ${msg}`);
  console.error(`   环境变量：${ENV_VAR}`);
  console.error('   要求：https://<真实子域名>.streamlit.app');
  console.error('');
  process.exit(1);
}

const raw = (process.env[ENV_VAR] || '').trim();

if (!raw) {
  fail('未配置体验地址，或值为空。请先部署 Streamlit 应用并配置真实地址。');
}

if (REJECT_PLACEHOLDERS.includes(raw)) {
  fail(`地址「${raw}」是示例或占位值，不能作为正式配置。`);
  return;
}

let parsed;
try {
  parsed = new URL(raw);
} catch {
  fail(`地址「${raw}」不是有效的 URL。`);
}

if (parsed.protocol !== 'https:') {
  fail(`协议必须为 https，当前为「${parsed.protocol}」。`);
}

if (parsed.username || parsed.password) {
  fail('地址不允许包含用户名或密码。');
}

const hostname = parsed.hostname.toLowerCase();
if (hostname === 'localhost' || hostname === '127.0.0.1') {
  fail('正式部署不允许使用 localhost 或 127.0.0.1。');
}

if (!hostname.endsWith('.streamlit.app')) {
  fail(`域名必须为 .streamlit.app 结尾，当前为「${hostname}」。`);
}

// 规范化 URL：使用解析后的 href，防止引号注入
const safeUrl = parsed.href;

let html = fs.readFileSync(path.join(__dirname, 'index.html'), 'utf-8');
html = html.replace(/__APP_URL__/g, safeUrl);

fs.mkdirSync(path.join(__dirname, 'dist'), { recursive: true });
fs.writeFileSync(path.join(__dirname, 'dist', 'index.html'), html);

console.log(`✅ 构建完成。体验地址：${safeUrl}`);
