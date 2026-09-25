import { createHmac } from 'crypto';

export function issueToken(userId: number, secret: string): string {
  const body = Buffer.from(JSON.stringify({ sub: userId, exp: Date.now() + 3600_000 })).toString('base64url');
  return body + '.' + createHmac('sha256', secret).update(body).digest('base64url');
}

export function verifyToken(token: string, secret: string) {
  const [body, sig] = token.replace('Bearer ', '').split('.');
  if (createHmac('sha256', secret).update(body).digest('base64url') !== sig) return null;
  const claims = JSON.parse(Buffer.from(body, 'base64url').toString());
  return claims.exp > Date.now() ? { id: claims.sub } : null;
}
