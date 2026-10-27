import fs from 'fs';
import path from 'path';
import initSqlJs, { Database, SqlValue } from 'sql.js';

let SQLModule: any = null;

async function getSqlModule() {
  if (!SQLModule) {
    const candidatePaths = [
      path.join(process.cwd(), 'public', 'sql-wasm.wasm'),
      path.join(process.cwd(), 'node_modules', 'sql.js', 'dist', 'sql-wasm.wasm'),
      path.join(__dirname, 'sql-wasm.wasm'),
      '/var/task/public/sql-wasm.wasm',
      '/var/task/node_modules/sql.js/dist/sql-wasm.wasm',
    ];

    let wasmBinary: ArrayBuffer | undefined;
    for (const candidate of candidatePaths) {
      try {
        if (fs.existsSync(candidate)) {
          const buf = fs.readFileSync(candidate);
          wasmBinary = buf.buffer.slice(buf.byteOffset, buf.byteOffset + buf.byteLength);
          break;
        }
      } catch {
        // ignore and continue
      }
    }

    if (wasmBinary) {
      SQLModule = await initSqlJs({ wasmBinary });
    } else {
      SQLModule = await initSqlJs({
        locateFile: (file) => {
          for (const candidate of [
            path.join(process.cwd(), 'public', file),
            path.join(process.cwd(), 'node_modules', 'sql.js', 'dist', file),
          ]) {
            try {
              if (fs.existsSync(candidate)) return candidate;
            } catch {
              // ignore
            }
          }
          return path.join(process.cwd(), 'public', file);
        },
      });
    }
  }
  return SQLModule;
}

export interface QueryResult {
  columns: string[];
  values: SqlValue[][];
  totalRows: number;
}

export async function openDatabaseFromBytes(content: ArrayBuffer): Promise<Database> {
  const SQL = await getSqlModule();
  const uInt8Array = new Uint8Array(content);
  return new SQL.Database(uInt8Array);
}

export function getDatabaseTables(db: Database): string[] {
  const res = db.exec("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name ASC;");
  if (!res.length || !res[0].values) return [];
  return res[0].values.map((row) => String(row[0]));
}

export function queryTableData(
  db: Database,
  tableName: string,
  limit: number = 50,
  offset: number = 0
): QueryResult {
  // Sanitize tableName against SQL injection
  const sanitizedTable = tableName.replace(/[^a-zA-Z0-9_]/g, '');
  if (!sanitizedTable) {
    throw new Error('Invalid table name.');
  }

  // Count total rows
  const countRes = db.exec(`SELECT COUNT(*) FROM "${sanitizedTable}";`);
  const totalRows = countRes.length && countRes[0].values.length ? Number(countRes[0].values[0][0]) : 0;

  // Query paginated rows
  const query = `SELECT * FROM "${sanitizedTable}" LIMIT ${Math.min(limit, 200)} OFFSET ${offset};`;
  const result = db.exec(query);

  if (!result.length) {
    // If table is empty, query schema for column names
    const pragmaRes = db.exec(`PRAGMA table_info("${sanitizedTable}");`);
    const columns = pragmaRes.length ? pragmaRes[0].values.map((col) => String(col[1])) : [];
    return { columns, values: [], totalRows: 0 };
  }

  return {
    columns: result[0].columns,
    values: result[0].values,
    totalRows,
  };
}
