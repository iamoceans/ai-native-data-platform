const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const ts = require('typescript');
const root = path.resolve(__dirname, '../..');
const messages = JSON.parse(fs.readFileSync(path.join(root, 'src/i18n/messages.json'), 'utf8'));

test('both languages contain the same interpolation placeholders', () => {
 const placeholders = text => [...text.matchAll(/\{(\w+)\}/g)].map(match => match[1]).sort();
 for (const [key, entry] of Object.entries(messages)) {
  assert.ok(entry.en.trim(), `${key}: English missing`);
  assert.ok(entry['zh-CN'].trim(), `${key}: Chinese missing`);
  assert.deepEqual(placeholders(entry.en), placeholders(entry['zh-CN']), key);
 }
});

test('every literal UI translation and status label has both translations', () => {
 for (const dir of ['pages', 'components']) for (const filename of fs.readdirSync(path.join(root, 'src', dir))) {
  if (!filename.endsWith('.tsx')) continue;
  const source = fs.readFileSync(path.join(root, 'src', dir, filename), 'utf8');
  const ast = ts.createSourceFile(filename, source, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
  const visit = node => {
   if (ts.isCallExpression(node) && node.expression.getText(ast) === 't' && ts.isStringLiteral(node.arguments[0])) {
    assert.ok(messages[node.arguments[0].text], `${filename}: missing ${node.arguments[0].text}`);
   }
   if (ts.isVariableDeclaration(node) && ['LABELS', 'REFUSAL_LABELS', 'KIND_LABELS', 'STATUS_LABELS'].includes(node.name.getText(ast))) {
    for (const entry of node.initializer.properties) assert.ok(messages[entry.initializer.text], `${filename}: missing label ${entry.initializer.text}`);
   }
   ts.forEachChild(node, visit);
  };
  visit(ast);
 }
});

test('all backend error codes have localized guidance', () => {
 const constants = fs.readFileSync(path.join(root, '../backend/app/constants.py'), 'utf8').split('class ErrorCode')[1];
 for (const match of constants.matchAll(/= "([A-Z_]+)"/g)) assert.ok(messages['error.' + match[1]], match[1]);
});

test('interpolation preserves user values and unknown keys fall back safely', () => {
 const exports = {};
 const code = ts.transpileModule(fs.readFileSync(path.join(root, 'src/i18n/index.ts'), 'utf8'), { compilerOptions: { module: ts.ModuleKind.CommonJS, esModuleInterop: true, target: ts.ScriptTarget.ES2022 } }).outputText;
 vm.runInNewContext(code, {
  exports, navigator: { languages: ['en-US'], language: 'en-US' }, document: { documentElement: {} },
  localStorage: { getItem: () => null, setItem: () => {} }, window: { addEventListener: () => {} },
  require: name => name === 'react' ? {} : messages,
 });
 assert.equal(exports.t('unknown-key'), 'unknown-key');
 assert.equal(exports.t('Saved {0} v{1}'), 'Saved {0} v{1}');
 assert.equal(exports.t('已保存 {0} v{1}', { 0: '<script>{1}</script>', 1: 2 }), 'Saved <script>{1}</script> v2');
 exports.setLocale('zh-CN');
 assert.equal(exports.t('已保存 {0} v{1}', { 0: '原始技能名', 1: 2 }), '已保存 原始技能名 v2');
 assert.equal(exports.errorMessage({ code: 'SQL_FORBIDDEN', message: 'diagnostic' }), '此 SQL 不符合只读安全策略。');
});
