export interface Config { dbPath: string; jwtSecret: string; maxNoteBytes: number; }

export function loadConfig(): Config {
  return {
    dbPath: process.env.NOTES_DB ?? 'notes.sqlite',
    jwtSecret: process.env.JWT_SECRET ?? 'dev-only',
    maxNoteBytes: 64 * 1024,
  };
}
