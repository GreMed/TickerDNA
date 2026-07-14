const fs = require('fs');
const path = require('path');

const ENV_VAR = 'NEXT_PUBLIC_TICKERDNA_APP_URL';

// 占位 hostname（小写），不受大小写、尾部斜杠、路径、query、hash 影响
const REJECT_HOSTNAMES = [
  'your-app.streamlit.app',
  'your-real-subdomain.streamlit.app',
  'example.streamlit.app',
];

// 占位原值（未解析前的精确匹配）
const REJECT_RAW = [
  '__APP_URL__',
  'https://share.streamlit.io',
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

if (REJECT_RAW.includes(raw)) {
  fail(`地址「${raw}」是示例或占位值，不能作为正式配置。`);
}

// 先解析再判断，确保不受尾部斜杠、路径、query、hash 影响
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

// 检查占位 hostname（大小写不敏感，不受路径/query/hash 影响）
if (REJECT_HOSTNAMES.includes(hostname)) {
  fail(`域名「${hostname}」是示例占位地址，不能作为正式配置。`);
}

// .streamlit.app 前必须存在真实、非空子域名
// hostname 形如 "sub.streamlit.app"，去掉 ".streamlit.app" 后必须非空
const subdomain = hostname.slice(0, hostname.length - '.streamlit.app'.length);
if (!subdomain || subdomain === '') {
  fail('域名缺少真实子域名，不能仅为 .streamlit.app。');
}

// 端口必须为空
if (parsed.port !== '') {
  fail(`地址不允许包含端口，当前端口为「${parsed.port}」。`);
}

// 正式地址使用根路径，不得包含 query 或 hash
if (parsed.search !== '' || parsed.hash !== '') {
  fail('地址不得包含 query 参数或 hash 片段。');
}

if (parsed.pathname !== '/' && parsed.pathname !== '') {
  fail(`地址不得包含路径，当前路径为「${parsed.pathname}」。`);
}

// 规范化 URL：使用解析后的 href，防止引号注入
const safeUrl = parsed.href;

let html = fs.readFileSync(path.join(__dirname, 'index.html'), 'utf-8');
html = html.replace(/__APP_URL__/g, safeUrl);

fs.mkdirSync(path.join(__dirname, 'dist'), { recursive: true });
fs.writeFileSync(path.join(__dirname, 'dist', 'index.html'), html);

console.log(`✅ 构建完成。体验地址：${safeUrl}`);
