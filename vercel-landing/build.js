const fs = require('fs');
const path = require('path');

const PLACEHOLDER_VALUES = [
  '',
  'https://share.streamlit.io',
];

const appUrl = process.env.NEXT_PUBLIC_TICKERDNA_APP_URL;

if (!appUrl || PLACEHOLDER_VALUES.includes(appUrl)) {
  console.error('');
  console.error('❌ 构建失败：未配置真实体验地址。');
  console.error('   请先部署 Streamlit 应用并配置真实体验地址。');
  console.error('   环境变量：NEXT_PUBLIC_TICKERDNA_APP_URL');
  console.error('');
  process.exit(1);
}

let html = fs.readFileSync(path.join(__dirname, 'index.html'), 'utf-8');
html = html.replace(/__APP_URL__/g, appUrl);

fs.mkdirSync(path.join(__dirname, 'dist'), { recursive: true });
fs.writeFileSync(path.join(__dirname, 'dist', 'index.html'), html);

console.log(`✅ 构建完成。体验地址：${appUrl}`);
