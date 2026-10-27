import readline from 'readline';
import fs from 'fs';
import path from 'path';

const ENV_FILE = path.resolve('./.env');
const CONFIG_FILE = path.resolve('./directories.json');

const rl = readline.createInterface({
  input: process.stdin,
  output: process.stdout,
});

function ask(question) {
  return new Promise((resolve) => rl.question(question, resolve));
}

function clearLine() {
  console.log('');
}

function header() {
  console.clear();
  console.log('╔══════════════════════════════════════════════════════╗');
  console.log('║       ChickenSee Agent — Directory Configurator       ║');
  console.log('╚══════════════════════════════════════════════════════╝');
  clearLine();
}

function loadConfig() {
  if (fs.existsSync(CONFIG_FILE)) {
    try {
      return JSON.parse(fs.readFileSync(CONFIG_FILE, 'utf-8'));
    } catch {
      return { directories: [] };
    }
  }
  return { directories: [] };
}

function saveConfig(config) {
  fs.writeFileSync(CONFIG_FILE, JSON.stringify(config, null, 2));
}

function loadEnv() {
  if (!fs.existsSync(ENV_FILE)) return {};
  const lines = fs.readFileSync(ENV_FILE, 'utf-8').split('\n');
  const env = {};
  for (const line of lines) {
    const match = line.match(/^([^=]+)=(.*)$/);
    if (match) env[match[1].trim()] = match[2].trim().replace(/^"|"$/g, '');
  }
  return env;
}

function writeEnv(env) {
  const content = Object.entries(env)
    .map(([k, v]) => `${k}="${v}"`)
    .join('\n');
  fs.writeFileSync(ENV_FILE, content + '\n');
}

async function mainMenu() {
  header();
  const config = loadConfig();
  const env = loadEnv();

  // Show current Vercel URL
  console.log(`  Vercel Sync URL : ${env.VERCEL_SYNC_URL || '(not set)'}`);
  console.log(`  Sync Secret     : ${env.SYNC_SECRET ? '****** (set)' : '(not set)'}`);
  clearLine();

  // Show watched directories
  if (config.directories.length === 0) {
    console.log('  Watched Directories: (none configured)');
  } else {
    console.log('  Watched Directories:');
    config.directories.forEach((dir, i) => {
      const exists = fs.existsSync(dir);
      console.log(`    [${i + 1}] ${dir}  ${exists ? '✓ exists' : '✗ NOT FOUND'}`);
    });
  }

  clearLine();
  console.log('  ─────────────────────────────────────────────');
  console.log('  [1] Add a directory to watch');
  console.log('  [2] Remove a directory');
  console.log('  [3] Set Vercel Sync URL');
  console.log('  [4] Set Sync Secret');
  console.log('  [5] Save & Exit');
  clearLine();

  const choice = await ask('  Enter choice: ');

  switch (choice.trim()) {
    case '1': await addDirectory(config); break;
    case '2': await removeDirectory(config); break;
    case '3': await setVercelUrl(env); break;
    case '4': await setSyncSecret(env); break;
    case '5': await saveAndExit(config, env); return;
    default:
      console.log('\n  ⚠ Invalid choice, try again.');
      await new Promise((r) => setTimeout(r, 1000));
  }

  await mainMenu();
}

async function addDirectory(config) {
  header();
  console.log('  ADD WATCH DIRECTORY');
  console.log('  ─────────────────────────────────────────────');
  console.log('  Enter the full path to a folder containing database');
  console.log('  files to sync. Video files always remain local.');
  clearLine();
  console.log('  Examples:');
  console.log('    C:\\ChickenSee\\Recordings');
  console.log('    D:\\Camera\\Videos');
  console.log('    C:\\Users\\Kairi\\AppData\\Local\\ChickenApp\\data');
  clearLine();

  const input = await ask('  Folder path (or press Enter to cancel): ');
  const dirPath = input.trim().replace(/^"|"$/g, '');

  if (!dirPath) return;

  if (!path.isAbsolute(dirPath)) {
    console.log('\n  ⚠ Please enter a full/absolute path (starting with a drive letter like C:\\)');
    await new Promise((r) => setTimeout(r, 2000));
    return;
  }

  if (!fs.existsSync(dirPath)) {
    const create = await ask(`\n  ⚠ Folder does not exist. Create it? (y/n): `);
    if (create.trim().toLowerCase() === 'y') {
      fs.mkdirSync(dirPath, { recursive: true });
      console.log(`  ✓ Created: ${dirPath}`);
    } else {
      return;
    }
  }

  if (config.directories.includes(dirPath)) {
    console.log('\n  ⚠ This directory is already in the list.');
    await new Promise((r) => setTimeout(r, 1500));
    return;
  }

  config.directories.push(dirPath);
  saveConfig(config);
  console.log(`\n  ✓ Added: ${dirPath}`);
  await new Promise((r) => setTimeout(r, 1200));
}

async function removeDirectory(config) {
  header();
  console.log('  REMOVE WATCH DIRECTORY');
  console.log('  ─────────────────────────────────────────────');

  if (config.directories.length === 0) {
    console.log('  No directories to remove.');
    await new Promise((r) => setTimeout(r, 1500));
    return;
  }

  config.directories.forEach((dir, i) => {
    console.log(`  [${i + 1}] ${dir}`);
  });

  clearLine();
  const input = await ask('  Enter number to remove (or Enter to cancel): ');
  const idx = parseInt(input.trim(), 10) - 1;

  if (isNaN(idx) || idx < 0 || idx >= config.directories.length) {
    return;
  }

  const removed = config.directories.splice(idx, 1);
  saveConfig(config);
  console.log(`\n  ✓ Removed: ${removed[0]}`);
  await new Promise((r) => setTimeout(r, 1200));
}

async function setVercelUrl(env) {
  header();
  console.log('  SET VERCEL SYNC URL');
  console.log('  ─────────────────────────────────────────────');
  console.log(`  Current: ${env.VERCEL_SYNC_URL || '(not set)'}`);
  clearLine();
  console.log('  This Vercel API issues private Supabase upload links.');
  console.log('  Example: https://chicken-see-videos-sql.vercel.app/api/sync');
  clearLine();

  const input = await ask('  New URL (or Enter to keep current): ');
  if (input.trim()) {
    env.VERCEL_SYNC_URL = input.trim();
    writeEnv(env);
    console.log('\n  ✓ Saved!');
    await new Promise((r) => setTimeout(r, 1200));
  }
}

async function setSyncSecret(env) {
  header();
  console.log('  SET SYNC SECRET');
  console.log('  ─────────────────────────────────────────────');
  console.log('  This must match the SYNC_SECRET set in Vercel env vars.');
  clearLine();

  const input = await ask('  New secret (or Enter to keep current): ');
  if (input.trim()) {
    env.SYNC_SECRET = input.trim();
    writeEnv(env);
    console.log('\n  ✓ Saved!');
    await new Promise((r) => setTimeout(r, 1200));
  }
}

async function saveAndExit(config, env) {
  // Write WATCH_DIRS to .env as comma-separated list
  env.WATCH_DIRS = config.directories.join(',');
  if (!env.WATCH_DIR) env.WATCH_DIR = './data'; // fallback
  writeEnv(env);
  saveConfig(config);

  header();
  console.log('  ✓ Configuration saved!');
  clearLine();
  console.log('  directories.json  — directory list');
  console.log('  .env              — connection settings');
  clearLine();
  if (config.directories.length > 0) {
    console.log('  Watching:');
    config.directories.forEach((d) => console.log(`    → ${d}`));
  }
  clearLine();
  console.log('  Start the watcher with:   npm start');
  clearLine();
  rl.close();
}

// Run
mainMenu().catch((err) => {
  console.error('Setup error:', err.message);
  rl.close();
  process.exit(1);
});
