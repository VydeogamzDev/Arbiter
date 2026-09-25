import { scryptSync, randomBytes, timingSafeEqual } from 'crypto';
import { db } from '../storage/db';

export function registerAccount(email: string, password: string) {
  const salt = randomBytes(16);
  const hash = scryptSync(password, salt, 32);
  return db.insertAccount(email, salt, hash);
}

export function authenticate(email: string, password: string) {
  const acct = db.findAccount(email);
  if (!acct) return null;
  const hash = scryptSync(password, acct.salt, 32);
  return timingSafeEqual(hash, acct.hash) ? acct : null;
}
